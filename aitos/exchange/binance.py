"""Binance USDT-M Futures exchange adapter.

Implements ``ExchangeAdapter`` against Binance's public market-data
endpoints — no API key/secret needed for anything in this module (klines,
depth, trades, funding rate, open interest are all public). Live order
placement is intentionally out of scope here; per the AI Constitution,
production trading actions go through ``AIKernel.enforce_governance``
first, in a later module.

REST base: https://fapi.binance.com
WebSocket base: wss://fstream.binance.com/stream (combined streams)

The HTTP session and the WebSocket connector are both injectable so this
adapter can be exercised in tests without any real network access — see
``tests/test_binance_adapter.py``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

import aiohttp

from aitos.exchange.base import ExchangeAdapter
from aitos.exchange.parsing import (
    parse_agg_trade_ws,
    parse_depth_ws,
    parse_funding_rate_rest,
    parse_kline_rest,
    parse_kline_ws,
    parse_open_interest_rest,
    parse_order_book_rest,
    parse_trade_rest,
)
from aitos.exchange.rate_limiter import TokenBucketRateLimiter
from aitos.exchange.symbol_filters import SymbolFilters, parse_exchange_info
from aitos.logging_setup import get_logger
from aitos.models.market import FundingRate, Kline, OpenInterest, OrderBookSnapshot, TradeTick

logger = get_logger("aitos.exchange.binance")

REST_BASE_URL = "https://fapi.binance.com"
WS_BASE_URL = "wss://fstream.binance.com/stream"

# Binance's REST weight budget is 2400/min on /fapi. We stay well under that
# by default; tune via constructor if running many symbols.
DEFAULT_RATE_LIMIT_CAPACITY = 2000
DEFAULT_RATE_LIMIT_REFILL_PER_SECOND = 2000 / 60

MAX_BACKOFF_SECONDS = 60.0
INITIAL_BACKOFF_SECONDS = 1.0


class BinanceFuturesAdapter(ExchangeAdapter):
    def __init__(
        self,
        session_factory: Callable[[], aiohttp.ClientSession] = aiohttp.ClientSession,
        ws_connector: Optional[Callable[..., Any]] = None,
        rate_limiter: Optional[TokenBucketRateLimiter] = None,
    ) -> None:
        self._session_factory = session_factory
        self._session: Optional[aiohttp.ClientSession] = None
        if ws_connector is None:
            import websockets

            ws_connector = websockets.connect
        self._ws_connector = ws_connector
        self._rate_limiter = rate_limiter or TokenBucketRateLimiter(
            capacity=DEFAULT_RATE_LIMIT_CAPACITY, refill_per_second=DEFAULT_RATE_LIMIT_REFILL_PER_SECOND
        )

    async def connect(self) -> None:
        if self._session is None or self._session.closed:
            self._session = self._session_factory()

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    # -- REST ---------------------------------------------------------------------

    async def fetch_klines(self, symbol: str, timeframe: str, limit: int = 500) -> List[Kline]:
        weight = 5 if limit <= 100 else (10 if limit <= 500 else 25)
        raw = await self._get("/fapi/v1/klines", {"symbol": symbol, "interval": timeframe, "limit": limit}, weight)
        return [parse_kline_rest(row, symbol=symbol, timeframe=timeframe) for row in raw]

    async def fetch_order_book(self, symbol: str, limit: int = 50) -> OrderBookSnapshot:
        weight = 2 if limit <= 50 else (5 if limit <= 100 else 10)
        raw = await self._get("/fapi/v1/depth", {"symbol": symbol, "limit": limit}, weight)
        return parse_order_book_rest(raw, symbol=symbol)

    async def fetch_recent_trades(self, symbol: str, limit: int = 500) -> List[TradeTick]:
        raw = await self._get("/fapi/v1/trades", {"symbol": symbol, "limit": limit}, weight=5)
        return [parse_trade_rest(row, symbol=symbol) for row in raw]

    async def fetch_funding_rate(self, symbol: str) -> FundingRate:
        raw = await self._get("/fapi/v1/premiumIndex", {"symbol": symbol}, weight=1)
        return parse_funding_rate_rest(raw)

    async def fetch_open_interest(self, symbol: str) -> OpenInterest:
        raw = await self._get("/fapi/v1/openInterest", {"symbol": symbol}, weight=1)
        return parse_open_interest_rest(raw)

    async def fetch_exchange_info(self, symbols: Optional[List[str]] = None) -> Dict[str, SymbolFilters]:
        """GET /fapi/v1/exchangeInfo — public, no auth. Weight 1. Returns
        every symbol's filters unless ``symbols`` narrows it, in which case
        the result dict is filtered client-side (Binance's endpoint doesn't
        support filtering by symbol for this one)."""
        raw = await self._get("/fapi/v1/exchangeInfo", {}, weight=1)
        all_filters = parse_exchange_info(raw)
        if symbols is None:
            return all_filters
        return {s: all_filters[s] for s in symbols if s in all_filters}

    # -- Streaming --------------------------------------------------------------

    async def stream_klines(self, symbols: List[str], timeframe: str) -> AsyncIterator[Kline]:
        streams = [f"{s.lower()}@kline_{timeframe}" for s in symbols]

        async def _parse(data: Any) -> Kline:
            return parse_kline_ws(data)

        async for kline in self._stream(streams, _parse):
            yield kline

    async def stream_trades(self, symbols: List[str]) -> AsyncIterator[TradeTick]:
        streams = [f"{s.lower()}@aggTrade" for s in symbols]

        async def _parse(data: Any) -> TradeTick:
            return parse_agg_trade_ws(data)

        async for trade in self._stream(streams, _parse):
            yield trade

    async def stream_order_book(self, symbols: List[str], levels: int = 20) -> AsyncIterator[OrderBookSnapshot]:
        streams = [f"{s.lower()}@depth{levels}@100ms" for s in symbols]
        symbol_by_stream = {f"{s.lower()}@depth{levels}@100ms": s for s in symbols}

        async def _parse(data: Any, stream_name: str) -> OrderBookSnapshot:
            return parse_depth_ws(data, symbol=symbol_by_stream[stream_name])

        async for book in self._stream_with_name(streams, _parse):
            yield book

    # -- Internals ----------------------------------------------------------------

    async def _get(self, path: str, params: dict, weight: int) -> Any:
        if self._session is None:
            raise RuntimeError("BinanceFuturesAdapter.connect() must be called first (or use 'async with')")
        await self._rate_limiter.acquire(weight)
        url = f"{REST_BASE_URL}{path}"
        async with self._session.get(url, params=params) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _stream(self, streams: List[str], parser: Callable[[Any], Any]) -> AsyncIterator[Any]:
        async for data, _stream_name in self._raw_stream(streams):
            yield await parser(data)

    async def _stream_with_name(self, streams: List[str], parser: Callable[[Any, str], Any]) -> AsyncIterator[Any]:
        async for data, stream_name in self._raw_stream(streams):
            yield await parser(data, stream_name)

    async def _raw_stream(self, streams: List[str]) -> AsyncIterator[tuple]:
        """Connect to the combined stream URL and yield ``(data, stream_name)`` forever,
        reconnecting with exponential backoff on any disconnect/error."""
        url = f"{WS_BASE_URL}?streams={'/'.join(streams)}"
        backoff = INITIAL_BACKOFF_SECONDS
        while True:
            try:
                async with self._ws_connector(url) as ws:
                    logger.info("connected to Binance stream", extra={"aitos_extra": {"streams": streams}})
                    backoff = INITIAL_BACKOFF_SECONDS  # reset after a clean connect
                    async for raw_message in ws:
                        try:
                            envelope = json.loads(raw_message)
                        except (TypeError, ValueError):
                            continue
                        stream_name = envelope.get("stream", streams[0] if streams else "")
                        data = envelope.get("data", envelope)
                        yield data, stream_name
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Binance stream disconnected, reconnecting",
                    extra={"aitos_extra": {"error": str(exc), "backoff_seconds": backoff}},
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
