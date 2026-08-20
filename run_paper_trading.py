#!/usr/bin/env python3
"""AITOS paper-trading entrypoint with durable continual-learning experience capture."""
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
from aitos.learning.recorder import LearningExperienceRecorder
from aitos.logging_setup import configure_logging, get_logger
from aitos.resilience import RetryExhaustedError, retry_with_backoff

logger = get_logger("aitos.run_paper_trading")
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
SCAN_INTERVAL_SECONDS = 60.0
KLINE_TIMEFRAME = "15m"
STARTING_EQUITY_USD = 10_000.0
HEALTH_SERVER_PORT = 8090


async def try_connect_clickhouse_repositories(settings) -> tuple[Optional[MarketDataRepository], Optional[JournalRepository]]:
    market_repo = MarketDataRepository(host=settings.clickhouse.host, port=settings.clickhouse.port, username=settings.clickhouse.user, password=settings.clickhouse.password, database=settings.clickhouse.database)
    journal_repo = JournalRepository(host=settings.clickhouse.host, port=settings.clickhouse.port, username=settings.clickhouse.user, password=settings.clickhouse.password, database=settings.clickhouse.database)
    try:
        await market_repo.initialize({})
        await journal_repo.initialize({})
        return market_repo, journal_repo
    except Exception as exc:
        logger.warning("ClickHouse unavailable, running without persistence: %s", exc)
        return None, None


async def try_connect_neo4j(settings):
    from neo4j import AsyncGraphDatabase
    driver = AsyncGraphDatabase.driver(settings.neo4j.uri, auth=(settings.neo4j.user, settings.neo4j.password))
    try:
        await driver.verify_connectivity()
        return driver
    except Exception as exc:
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
        logger.error("could not connect to Redis after retries: %s", exc)
        raise SystemExit(1) from exc


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    redis_client = await connect_redis_with_retry(settings)
    from aitos.eventbus.redis_bus import EventBus
    event_bus = EventBus(redis_client=redis_client)
    await event_bus.initialize({})
    market_repo, journal_repo = await try_connect_clickhouse_repositories(settings)
    graph_driver = await try_connect_neo4j(settings)
    exchange = BinanceFuturesAdapter()
    order_executor = SimulatedOrderExecutor()
    components = await build_system(event_bus=event_bus, exchange=exchange, order_executor=order_executor, symbols=SYMBOLS,
                                     kline_timeframe=KLINE_TIMEFRAME, scanner_timeframe=KLINE_TIMEFRAME,
                                     market_data_repository=market_repo, journal_repository=journal_repo,
                                     graph_driver=graph_driver, risk_limits=None)
    await initialize_all(components)
    experience_recorder = LearningExperienceRecorder(event_bus, market_repo, source="paper")
    await experience_recorder.initialize({})
    health_server = HealthServer(components.all_modules() + [experience_recorder], port=HEALTH_SERVER_PORT)
    await health_server.start()
    tracker = PaperPortfolioTracker(starting_equity_usd=STARTING_EQUITY_USD)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM): loop.add_signal_handler(sig, stop_event.set)
    try:
        while not stop_event.is_set():
            try:
                submitted = await run_scan_and_trade_cycle(components, tracker)
                logger.info("scan cycle complete", extra={"aitos_extra": {"submitted": submitted, "open_trades": len(components.trade_lifecycle.get_open_trades()), "closed_trades": len(components.trade_lifecycle.get_closed_trades())}})
            except Exception as exc:
                logger.error("scan/trade cycle failed, will retry next interval: %s", exc)
            try: await asyncio.wait_for(stop_event.wait(), timeout=SCAN_INTERVAL_SECONDS)
            except asyncio.TimeoutError: pass
    finally:
        await health_server.stop()
        await experience_recorder.shutdown()
        await shutdown_all(components)
        if market_repo is not None: await market_repo.shutdown()
        if journal_repo is not None: await journal_repo.shutdown()
        await redis_client.aclose()


if __name__ == "__main__": asyncio.run(main())
