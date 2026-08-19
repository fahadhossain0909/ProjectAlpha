"""Binance USDT-M Futures exchange adapter."""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

import aiohttp

from aitos.exchange.base import ExchangeAdapter
from aitos.exchange.orderbook import LocalOrderBook, OrderBookSequenceError
from aitos.exchange.parsing import parse_agg_trade_ws, parse_depth_diff_ws, parse_funding_rate_rest, parse_kline_rest, parse_kline_ws, parse_open_interest_rest, parse_order_book_rest, parse_trade_rest
from aitos.exchange.rate_limiter import TokenBucketRateLimiter
from aitos.exchange.symbol_filters import SymbolFilters, parse_exchange_info
from aitos.logging_setup import get_logger
from aitos.models.market import FundingRate, Kline, OpenInterest, OrderBookSnapshot, TradeTick

logger = get_logger("aitos.exchange.binance")
REST_BASE_URL = "https://fapi.binance.com"
WS_BASE_URL = "wss://fstream.binance.com/stream"
DEFAULT_RATE_LIMIT_CAPACITY = 2000
DEFAULT_RATE_LIMIT_REFILL_PER_SECOND = 2000 / 60
MAX_BACKOFF_SECONDS = 60.0
INITIAL_BACKOFF_SECONDS = 1.0


class BinanceFuturesAdapter(ExchangeAdapter):
    def __init__(self, session_factory: Callable[[], aiohttp.ClientSession] = aiohttp.ClientSession, ws_connector: Optional[Callable[..., Any]] = None, rate_limiter: Optional[TokenBucketRateLimiter] = None) -> None:
        self._session_factory = session_factory
        self._session: Optional[aiohttp.ClientSession] = None
        if ws_connector is None:
            import websockets
            ws_connector = websockets.connect
        self._ws_connector = ws_connector
        self._rate_limiter = rate_limiter or TokenBucketRateLimiter(capacity=DEFAULT_RATE_LIMIT_CAPACITY, refill_per_second=DEFAULT_RATE_LIMIT_REFILL_PER_SECOND)

    async def connect(self) -> None:
        if self._session is None or self._session.closed:
            self._session = self._session_factory()

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

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
        return parse_funding_rate_rest(await self._get("/fapi/v1/premiumIndex", {"symbol": symbol}, weight=1))

    async def fetch_open_interest(self, symbol: str) -> OpenInterest:
        return parse_open_interest_rest(await self._get("/fapi/v1/openInterest", {"symbol": symbol}, weight=1))

    async def fetch_exchange_info(self, symbols: Optional[List[str]] = None) -> Dict[str, SymbolFilters]:
        raw = await self._get("/fapi/v1/exchangeInfo", {}, weight=1)
        all_filters = parse_exchange_info(raw)
        return all_filters if symbols is None else {s: all_filters[s] for s in symbols if s in all_filters}

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
        """Reconstruct a local L2 book from Binance diff-depth updates.

        Each symbol is seeded from REST and then advanced only when update-id
        continuity is valid. A gap/chain break triggers a REST resync; the
        current diff is discarded and the next bridging event is accepted.
        """
        streams = [f"{s.lower()}@depth@100ms" for s in symbols]
        symbol_by_stream = {f"{s.lower()}@depth@100ms": s for s in symbols}
        books: Dict[str, LocalOrderBook] = {}
        for symbol in symbols:
            book = LocalOrderBook(symbol, max_levels=max(100, levels * 5))
            book.seed(await self.fetch_order_book(symbol, limit=max(100, levels)))
            books[symbol] = book

        async for data, stream_name in self._raw_stream(streams):
            symbol = symbol_by_stream.get(stream_name)
            if symbol is None:
                continue
            try:
                update = parse_depth_diff_ws(data)
                snapshot = books[symbol].apply(update)
            except OrderBookSequenceError as exc:
                logger.warning("order-book sequence break; resyncing", extra={"aitos_extra": {"symbol": symbol, "error": str(exc)}})
                books[symbol].seed(await self.fetch_order_book(symbol, limit=max(100, levels)))
                continue
            yield snapshot

    async def _get(self, path: str, params: dict, weight: int) -> Any:
        if self._session is None:
            raise RuntimeError("BinanceFuturesAdapter.connect() must be called first (or use 'async with')")
        await self._rate_limiter.acquire(weight)
        async with self._session.get(f"{REST_BASE_URL}{path}", params=params) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _stream(self, streams: List[str], parser: Callable[[Any], Any]) -> AsyncIterator[Any]:
        async for data, _ in self._raw_stream(streams):
            yield await parser(data)

    async def _raw_stream(self, streams: List[str]) -> AsyncIterator[tuple]:
        url = f"{WS_BASE_URL}?streams={'/'.join(streams)}"
        backoff = INITIAL_BACKOFF_SECONDS
        while True:
            try:
                async with self._ws_connector(url) as ws:
                    logger.info("connected to Binance stream", extra={"aitos_extra": {"streams": streams}})
                    backoff = INITIAL_BACKOFF_SECONDS
                    async for raw_message in ws:
                        try:
                            envelope = json.loads(raw_message)
                        except (TypeError, ValueError):
                            continue
                        yield envelope.get("data", envelope), envelope.get("stream", streams[0] if streams else "")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Binance stream disconnected, reconnecting", extra={"aitos_extra": {"error": str(exc), "backoff_seconds": backoff}})
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
