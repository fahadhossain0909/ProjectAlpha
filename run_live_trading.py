#!/usr/bin/env python3
"""AITOS LIVE trading entrypoint — places REAL orders with REAL money.

This is deliberately a separate file from ``run_paper_trading.py`` so
running the wrong script by habit isn't how you end up trading live.
Everything else about the system is identical — same ``build_system``
wiring, same Risk Engine, same Trade Lifecycle — only the executor and
the governance path differ:

- Uses ``BinanceFuturesOrderExecutor`` instead of ``SimulatedOrderExecutor``.
  Defaults to Binance's testnet (see ``BINANCE_TESTNET`` in ``.env``);
  mainnet requires that flag to be explicitly set to false.
- Every opportunity is submitted with ``is_production=True``, which routes
  it through ``AIKernel.enforce_governance`` -- this REQUIRES an
  ``approved_by`` value. This script gets that from an interactive,
  typed confirmation at startup (a session-level human approval), not a
  per-trade prompt -- a live trading loop that paused for a human every
  scan cycle wouldn't be a trading system. If you want per-trade human
  approval instead, that's a different (and reasonable!) design; this
  script doesn't build it.
- Enables ``use_exchange_side_stops=True`` (real resting SL/TP orders on
  Binance) and runs ``ReconciliationScheduler`` automatically, since a
  live position needs real protection, not just this process's own
  virtual monitoring.
- Loads real ``/fapi/v1/exchangeInfo`` precision data before trading, so
  order quantities/prices are always valid for each symbol.

Requires (in ``.env`` or the environment):
    BINANCE_API_KEY, BINANCE_API_SECRET   -- from your Binance account
    BINANCE_TESTNET=true                  -- leave true until you mean it
    BINANCE_HEDGE_MODE=false               -- match your account's actual mode

Run: python3 run_live_trading.py
"""

from __future__ import annotations

import asyncio
import signal

from redis.asyncio import Redis

from aitos.app import LivePortfolioTracker, build_system, initialize_all, run_scan_and_trade_cycle, shutdown_all
from aitos.config.settings import get_settings
from aitos.exchange.binance import BinanceFuturesAdapter
from aitos.health_server import HealthServer
from aitos.kernel.ai_kernel import AIKernel
from aitos.live_trading import confirm_live_trading, prepare_live_executor
from aitos.logging_setup import configure_logging, get_logger
from aitos.resilience import RetryExhaustedError, retry_with_backoff

logger = get_logger("aitos.run_live_trading")

SYMBOLS = ["BTCUSDT", "ETHUSDT"]  # keep this short for live trading -- start small
SCAN_INTERVAL_SECONDS = 60.0
KLINE_TIMEFRAME = "15m"
HEALTH_SERVER_PORT = 8091


async def connect_redis_with_retry(settings) -> Redis:
    async def _attempt() -> Redis:
        client = Redis.from_url(settings.redis.url)
        await client.ping()
        return client

    try:
        return await retry_with_backoff(_attempt, max_attempts=5, base_delay_seconds=2.0, max_delay_seconds=30.0, operation_name="Redis connection")
    except RetryExhaustedError as exc:
        logger.error("could not connect to Redis after retries -- exiting: %s", exc)
        raise SystemExit(1) from exc


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    approved_by = confirm_live_trading(SYMBOLS, testnet=settings.binance.testnet)
    logger.info("live trading session approved", extra={"aitos_extra": {"approved_by": approved_by, "testnet": settings.binance.testnet}})

    redis_client = await connect_redis_with_retry(settings)

    from aitos.eventbus.redis_bus import EventBus

    event_bus = EventBus(redis_client=redis_client)
    await event_bus.initialize({})

    order_executor = await prepare_live_executor(settings, SYMBOLS)
    exchange = BinanceFuturesAdapter()

    # require_human_approval_for_prod is already the default -- kept explicit
    # here as a reminder that this is exactly what confirm_live_trading() above
    # is satisfying via approved_by, not something this script is bypassing.
    kernel = AIKernel(event_bus=event_bus, require_human_approval_for_prod=True)

    components = await build_system(
        event_bus=event_bus,
        exchange=exchange,
        order_executor=order_executor,
        symbols=SYMBOLS,
        kline_timeframe=KLINE_TIMEFRAME,
        scanner_timeframe=KLINE_TIMEFRAME,
        kernel=kernel,
        use_exchange_side_stops=True,  # real resting SL/TP -- see aitos/trading/reconciliation.py
    )
    await initialize_all(components)
    logger.info("system initialized -- entering LIVE scan/trade loop", extra={"aitos_extra": {"scan_interval_seconds": SCAN_INTERVAL_SECONDS}})

    health_server = HealthServer(components.all_modules(), port=HEALTH_SERVER_PORT)
    await health_server.start()

    tracker = LivePortfolioTracker(order_executor=order_executor)
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    try:
        while not stop_event.is_set():
            try:
                submitted = await run_scan_and_trade_cycle(components, tracker, is_production=True, approved_by=approved_by)
                logger.info(
                    "scan cycle complete",
                    extra={"aitos_extra": {
                        "submitted": submitted,
                        "open_trades": len(components.trade_lifecycle.get_open_trades()),
                        "account_equity_usd": tracker._last_known_equity_usd,
                    }},
                )
                # Reconciliation also runs on its own background interval
                # (ReconciliationScheduler), but a manual pass after every
                # scan cycle catches anything sooner.
                if components.reconciliation is not None:
                    await components.reconciliation.run_once()
            except Exception as exc:  # noqa: BLE001
                logger.error("scan/trade cycle failed, will retry next interval: %s", exc)

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=SCAN_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass
    finally:
        logger.info("shutting down live trading -- open positions remain on the exchange, protected by resting SL/TP orders")
        await health_server.stop()
        await shutdown_all(components)
        await order_executor.close()
        await redis_client.aclose()
        logger.info("shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
