#!/usr/bin/env python3
"""AITOS paper-trading entrypoint.

Wires every module built across this project into one running system and
runs a continuous scan → rank → size → submit loop against **live Binance
market data**, trading on paper (``SimulatedOrderExecutor`` — no real
orders, no API keys needed for this script to run).

Requires infrastructure from ``docker-compose.yml``:

    docker compose up -d          # Redis is required; ClickHouse/Neo4j optional
    pip install -r requirements.txt
    cp .env.example .env          # defaults are fine for paper trading
    python3 run_paper_trading.py

ClickHouse and Neo4j are optional — if they're unreachable at startup,
this script logs a warning and runs without persistence/knowledge-graph
rather than failing outright (same "repository=None" pattern used
throughout the test suite).

This uses the same production-supervision building blocks as
``run_live_trading.py``: retry-with-backoff on the required Redis
connection (``aitos/resilience.py``), and a ``/health``+``/metrics`` HTTP
server (``aitos/health_server.py``) for a process supervisor or
monitoring stack to poll. No daemonization here — see
``deploy/aitos-paper.service`` for a systemd unit that handles
restart-on-crash and log capture.
"""

from __future__ import annotations

import asyncio
import signal
from typing import Optional

from redis.asyncio import Redis

from aitos.app import PaperPortfolioTracker, build_system, initialize_all, run_scan_and_trade_cycle, shutdown_all
from aitos.config.settings import get_settings
from aitos.data.repository import MarketDataRepository
from aitos.exchange.binance import BinanceFuturesAdapter
from aitos.execution.order_executor import SimulatedOrderExecutor
from aitos.health_server import HealthServer
from aitos.journal.repository import JournalRepository
from aitos.logging_setup import configure_logging, get_logger
from aitos.resilience import RetryExhaustedError, retry_with_backoff

logger = get_logger("aitos.run_paper_trading")

# Adjust to taste — a handful of liquid USDT-M perpetuals is plenty to start.
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
SCAN_INTERVAL_SECONDS = 60.0
KLINE_TIMEFRAME = "15m"
STARTING_EQUITY_USD = 10_000.0
HEALTH_SERVER_PORT = 8090


async def try_connect_clickhouse_repositories(settings) -> tuple[Optional[MarketDataRepository], Optional[JournalRepository]]:
    market_repo = MarketDataRepository(
        host=settings.clickhouse.host, port=settings.clickhouse.port,
        username=settings.clickhouse.user, password=settings.clickhouse.password, database=settings.clickhouse.database,
    )
    journal_repo = JournalRepository(
        host=settings.clickhouse.host, port=settings.clickhouse.port,
        username=settings.clickhouse.user, password=settings.clickhouse.password, database=settings.clickhouse.database,
    )
    try:
        await market_repo.initialize({})
        await journal_repo.initialize({})
        logger.info("connected to ClickHouse — market data and journal entries will be persisted")
        return market_repo, journal_repo
    except Exception as exc:  # noqa: BLE001
        logger.warning("ClickHouse unavailable, running without persistence: %s", exc)
        return None, None


async def try_connect_neo4j(settings):
    """Returns a connected Neo4j async driver, or None if unreachable —
    the Knowledge Graph writer/correlation updater are skipped entirely
    when this fails, same optional-infra pattern as ClickHouse above."""
    from neo4j import AsyncGraphDatabase

    driver = AsyncGraphDatabase.driver(settings.neo4j.uri, auth=(settings.neo4j.user, settings.neo4j.password))
    try:
        await driver.verify_connectivity()
        logger.info("connected to Neo4j — knowledge graph will be populated")
        return driver
    except Exception as exc:  # noqa: BLE001
        logger.warning("Neo4j unavailable, running without the knowledge graph: %s", exc)
        await driver.close()
        return None


async def connect_redis_with_retry(settings) -> Redis:
    async def _attempt() -> Redis:
        client = Redis.from_url(settings.redis.url)
        await client.ping()
        return client

    try:
        return await retry_with_backoff(_attempt, max_attempts=5, base_delay_seconds=2.0, max_delay_seconds=30.0, operation_name="Redis connection")
    except RetryExhaustedError as exc:
        logger.error("could not connect to Redis after retries — Redis is required, exiting: %s", exc)
        raise SystemExit(1) from exc


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info("starting AITOS paper trading", extra={"aitos_extra": {"symbols": SYMBOLS, "environment": settings.environment}})

    redis_client = await connect_redis_with_retry(settings)

    from aitos.eventbus.redis_bus import EventBus

    event_bus = EventBus(redis_client=redis_client)
    await event_bus.initialize({})

    market_repo, journal_repo = await try_connect_clickhouse_repositories(settings)
    graph_driver = await try_connect_neo4j(settings)

    exchange = BinanceFuturesAdapter()
    order_executor = SimulatedOrderExecutor()  # paper trading — always, for this script

    components = await build_system(
        event_bus=event_bus,
        exchange=exchange,
        order_executor=order_executor,
        symbols=SYMBOLS,
        kline_timeframe=KLINE_TIMEFRAME,
        scanner_timeframe=KLINE_TIMEFRAME,
        market_data_repository=market_repo,
        journal_repository=journal_repo,
        graph_driver=graph_driver,
        risk_limits=None,  # defaults from RiskLimits()
    )
    await initialize_all(components)
    logger.info("system initialized — entering scan/trade loop", extra={"aitos_extra": {"scan_interval_seconds": SCAN_INTERVAL_SECONDS}})

    health_server = HealthServer(components.all_modules(), port=HEALTH_SERVER_PORT)
    await health_server.start()
    logger.info("health/metrics available", extra={"aitos_extra": {"health": f"http://127.0.0.1:{HEALTH_SERVER_PORT}/health", "metrics": f"http://127.0.0.1:{HEALTH_SERVER_PORT}/metrics"}})

    tracker = PaperPortfolioTracker(starting_equity_usd=STARTING_EQUITY_USD)
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    try:
        while not stop_event.is_set():
            try:
                submitted = await run_scan_and_trade_cycle(components, tracker)
                open_count = len(components.trade_lifecycle.get_open_trades())
                closed_count = len(components.trade_lifecycle.get_closed_trades())
                logger.info(
                    "scan cycle complete",
                    extra={"aitos_extra": {"submitted": submitted, "open_trades": open_count, "closed_trades": closed_count}},
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("scan/trade cycle failed, will retry next interval: %s", exc)

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=SCAN_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass
    finally:
        logger.info("shutting down")
        await health_server.stop()
        await shutdown_all(components)
        if market_repo is not None:
            await market_repo.shutdown()
        if journal_repo is not None:
            await journal_repo.shutdown()
        # Note: if graph_driver was set, KnowledgeGraphWriter.shutdown() (called
        # via shutdown_all above) already closed it — nothing further needed here.
        await redis_client.aclose()
        logger.info("shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
