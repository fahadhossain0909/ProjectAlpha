"""DataIngestionService — the glue between the Market Layer (exchange
adapters) and the rest of AITOS.

Consumes an ``ExchangeAdapter``'s live streams and, for every tick:
1. publishes an ``Event`` on the Event Bus (topic hierarchy per spec
   section 29.2: ``market.{type}.{symbol}[.{timeframe}]``) so agents react
   in real time, and
2. persists it via the injected ``MarketDataRepository`` (optional — pass
   ``None`` to run pure pub/sub without ClickHouse, e.g. in tests).

Backfill (``backfill_klines``) hits the REST API directly so a symbol has
history before the live stream starts contributing bars.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Deque, Dict, List, Optional

from aitos.core.contracts import AITOSModule, Event, EventPriority, EventResponse, HealthStatus, ModuleStatus
from aitos.core.exceptions import ModuleNotInitializedError
from aitos.data.repository import MarketDataRepository
from aitos.eventbus.redis_bus import EventBus
from aitos.exchange.base import ExchangeAdapter
from aitos.intelligence.liquidity_tracker import LiquidityTracker
from aitos.logging_setup import get_logger
from aitos.models.market import Kline, OrderBookSnapshot, TradeTick

logger = get_logger("aitos.data.ingestion")


def kline_topic(symbol: str, timeframe: str) -> str:
    return f"market.kline.{symbol}.{timeframe}"


def trade_topic(symbol: str) -> str:
    return f"market.trade.{symbol}"


def orderbook_topic(symbol: str) -> str:
    return f"market.orderbook.{symbol}"


def liquidity_topic(symbol: str) -> str:
    return f"market.liquidity.{symbol}"


class DataIngestionService(AITOSModule):
    """Live market ingestion plus stateful L2 liquidity event generation."""

    def __init__(
        self,
        exchange: ExchangeAdapter,
        event_bus: EventBus,
        symbols: List[str],
        kline_timeframe: str = "1m",
        repository: Optional[MarketDataRepository] = None,
        orderbook_levels: int = 20,
        liquidity_trade_window: int = 500,
    ) -> None:
        self._exchange = exchange
        self._event_bus = event_bus
        self._repository = repository
        self._symbols = symbols
        self._kline_timeframe = kline_timeframe
        self._orderbook_levels = orderbook_levels
        self._liquidity_trade_window = max(50, liquidity_trade_window)
        self._initialized = False
        self._tasks: List[asyncio.Task] = []
        self._last_event_time: Optional[str] = None
        self._ticks_processed = 0
        self._liquidity_events = 0
        self._errors = 0
        self._recent_trades: Dict[str, Deque[TradeTick]] = defaultdict(
            lambda: deque(maxlen=self._liquidity_trade_window)
        )
        self._liquidity_trackers: Dict[str, LiquidityTracker] = {
            symbol: LiquidityTracker() for symbol in symbols
        }

    @property
    def module_id(self) -> str:
        return "data-ingestion-service"

    @property
    def version(self) -> str:
        return "1.1.0"

    async def initialize(self, config: Dict[str, Any]) -> None:
        if self._initialized:
            return
        await self._exchange.connect()
        self._tasks = [
            asyncio.create_task(self._run_kline_stream(), name="ingest-klines"),
            asyncio.create_task(self._run_trade_stream(), name="ingest-trades"),
            asyncio.create_task(self._run_orderbook_stream(), name="ingest-orderbook"),
        ]
        self._initialized = True
        logger.info(
            "DataIngestionService initialized",
            extra={"aitos_extra": {"symbols": self._symbols, "timeframe": self._kline_timeframe}},
        )

    async def health_check(self) -> HealthStatus:
        alive = sum(1 for t in self._tasks if not t.done())
        if self._errors > 0:
            status = ModuleStatus.UNHEALTHY
        elif alive == 0:
            status = ModuleStatus.DEGRADED
        elif alive < len(self._tasks):
            status = ModuleStatus.DEGRADED
        else:
            status = ModuleStatus.HEALTHY
        return HealthStatus(
            module_id=self.module_id,
            status=status,
            latency_ms=0.0,
            last_event_time=self._last_event_time,
            details={
                "ticks_processed": self._ticks_processed,
                "liquidity_events": self._liquidity_events,
                "errors": self._errors,
                "tasks_alive": alive,
            },
        )

    async def shutdown(self, grace_period_seconds: float = 30.0) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=grace_period_seconds)
        await self._exchange.close()
        logger.info("DataIngestionService shut down")

    async def emit_events(self) -> AsyncIterator[Event]:
        return
        yield  # pragma: no cover

    async def handle_event(self, event: Event) -> Optional[EventResponse]:
        return None

    async def backfill_klines(self, symbol: str, timeframe: str, limit: int = 500) -> int:
        self._require_initialized()
        klines = await self._exchange.fetch_klines(symbol, timeframe, limit=limit)
        for kline in klines:
            await self._handle_kline(kline)
        logger.info("backfilled klines", extra={"aitos_extra": {"symbol": symbol, "timeframe": timeframe, "count": len(klines)}})
        return len(klines)

    async def _run_kline_stream(self) -> None:
        try:
            async for kline in self._exchange.stream_klines(self._symbols, self._kline_timeframe):
                await self._handle_kline(kline)
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            self._errors += 1
            logger.error("kline stream loop crashed: %s", exc)

    async def _run_trade_stream(self) -> None:
        try:
            async for trade in self._exchange.stream_trades(self._symbols):
                self._recent_trades[trade.symbol].append(trade)
                await self._handle_trade(trade)
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            self._errors += 1
            logger.error("trade stream loop crashed: %s", exc)

    async def _run_orderbook_stream(self) -> None:
        try:
            async for book in self._exchange.stream_order_book(self._symbols, self._orderbook_levels):
                await self._handle_order_book(book)
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            self._errors += 1
            logger.error("order book stream loop crashed: %s", exc)

    async def _handle_kline(self, kline: Kline) -> None:
        await self._event_bus.publish(
            Event(topic=kline_topic(kline.symbol, kline.timeframe), payload=kline.to_dict(), source_module=self.module_id, priority=EventPriority.NORMAL)
        )
        if self._repository is not None:
            await self._repository.save_kline(kline)
        self._tick_processed()

    async def _handle_trade(self, trade: TradeTick) -> None:
        await self._event_bus.publish(
            Event(topic=trade_topic(trade.symbol), payload=trade.to_dict(), source_module=self.module_id, priority=EventPriority.NORMAL)
        )
        if self._repository is not None:
            await self._repository.save_trade_tick(trade)
        self._tick_processed()

    async def _handle_order_book(self, book: OrderBookSnapshot) -> None:
        await self._event_bus.publish(
            Event(topic=orderbook_topic(book.symbol), payload=book.to_dict(), source_module=self.module_id, priority=EventPriority.NORMAL)
        )
        if self._repository is not None:
            await self._repository.save_order_book_snapshot(book)

        # Feed the consecutive live L2 snapshots into the stateful tracker.
        # Recent executed trades provide the aggression context needed to
        # distinguish ordinary book movement from a likely sweep.
        tracker = self._liquidity_trackers.setdefault(book.symbol, LiquidityTracker())
        events = tracker.update(book, tuple(self._recent_trades[book.symbol]))
        for liquidity_event in events:
            payload = {
                "kind": liquidity_event.kind,
                "side": liquidity_event.side,
                "score": liquidity_event.score,
                "price": liquidity_event.price,
                "details": liquidity_event.details,
                "timestamp": book.timestamp.isoformat(),
                "last_update_id": book.last_update_id,
            }
            await self._event_bus.publish(
                Event(
                    topic=liquidity_topic(book.symbol),
                    payload=payload,
                    source_module=self.module_id,
                    priority=EventPriority.HIGH if liquidity_event.kind == "sweep" else EventPriority.NORMAL,
                )
            )
            self._liquidity_events += 1
        self._tick_processed()

    def _tick_processed(self) -> None:
        self._ticks_processed += 1
        self._last_event_time = datetime.now(timezone.utc).isoformat()

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise ModuleNotInitializedError("DataIngestionService.initialize() must be called first")
