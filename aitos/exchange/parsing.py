"""Pure parsing functions: raw Binance USDT-M Futures payloads → AITOS models.

Kept separate from ``binance.py`` (which does the actual HTTP/WebSocket I/O)
so parsing logic can be unit tested with plain dicts/lists — no network, no
mocking required.

Reference: Binance USDT-M Futures API docs (fapi.binance.com / fstream.binance.com).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from aitos.models.market import FundingRate, Kline, OpenInterest, OrderBookSnapshot, TradeSide, TradeTick


def _ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


# -- REST -----------------------------------------------------------------------

def parse_kline_rest(raw: List[Any], symbol: str, timeframe: str) -> Kline:
    """Parse one row of GET /fapi/v1/klines.

    Row shape: [openTime, open, high, low, close, volume, closeTime,
    quoteVolume, numTrades, takerBuyBaseVolume, takerBuyQuoteVolume, ignore]
    """
    return Kline(
        symbol=symbol,
        timeframe=timeframe,
        open_time=_ms_to_dt(int(raw[0])),
        close_time=_ms_to_dt(int(raw[6])),
        open=float(raw[1]),
        high=float(raw[2]),
        low=float(raw[3]),
        close=float(raw[4]),
        volume=float(raw[5]),
        quote_volume=float(raw[7]),
        trades_count=int(raw[8]),
        taker_buy_volume=float(raw[9]),
        taker_buy_quote_volume=float(raw[10]),
        is_closed=True,
    )


def _levels(raw_levels: List[List[str]]) -> Tuple[Tuple[float, float], ...]:
    return tuple((float(p), float(q)) for p, q in raw_levels)


def parse_order_book_rest(raw: Dict[str, Any], symbol: str) -> OrderBookSnapshot:
    """Parse GET /fapi/v1/depth response."""
    timestamp = _ms_to_dt(int(raw["E"])) if "E" in raw else datetime.now(timezone.utc)
    return OrderBookSnapshot(
        symbol=symbol,
        bids=_levels(raw["bids"]),
        asks=_levels(raw["asks"]),
        last_update_id=int(raw["lastUpdateId"]),
        timestamp=timestamp,
    )


def parse_trade_rest(raw: Dict[str, Any], symbol: str) -> TradeTick:
    """Parse one entry of GET /fapi/v1/trades."""
    is_buyer_maker = bool(raw["isBuyerMaker"])
    return TradeTick(
        symbol=symbol,
        trade_id=int(raw["id"]),
        price=float(raw["price"]),
        quantity=float(raw["qty"]),
        side=TradeSide.SELL if is_buyer_maker else TradeSide.BUY,
        is_buyer_maker=is_buyer_maker,
        timestamp=_ms_to_dt(int(raw["time"])),
    )


def parse_funding_rate_rest(raw: Dict[str, Any]) -> FundingRate:
    """Parse GET /fapi/v1/premiumIndex response."""
    return FundingRate(
        symbol=raw["symbol"],
        funding_rate=float(raw["lastFundingRate"]),
        funding_time=_ms_to_dt(int(raw["nextFundingTime"])),
        mark_price=float(raw["markPrice"]),
    )


def parse_open_interest_rest(raw: Dict[str, Any]) -> OpenInterest:
    """Parse GET /fapi/v1/openInterest response."""
    return OpenInterest(
        symbol=raw["symbol"],
        open_interest=float(raw["openInterest"]),
        timestamp=_ms_to_dt(int(raw["time"])),
    )


# -- WebSocket --------------------------------------------------------------------

def parse_kline_ws(payload: Dict[str, Any]) -> Kline:
    """Parse a ``<symbol>@kline_<interval>`` stream event payload (the ``data`` field)."""
    k = payload["k"]
    return Kline(
        symbol=payload["s"],
        timeframe=k["i"],
        open_time=_ms_to_dt(int(k["t"])),
        close_time=_ms_to_dt(int(k["T"])),
        open=float(k["o"]),
        high=float(k["h"]),
        low=float(k["l"]),
        close=float(k["c"]),
        volume=float(k["v"]),
        quote_volume=float(k["q"]),
        trades_count=int(k["n"]),
        taker_buy_volume=float(k["V"]),
        taker_buy_quote_volume=float(k["Q"]),
        is_closed=bool(k["x"]),
    )


def parse_agg_trade_ws(payload: Dict[str, Any]) -> TradeTick:
    """Parse a ``<symbol>@aggTrade`` stream event payload (the ``data`` field)."""
    is_buyer_maker = bool(payload["m"])
    return TradeTick(
        symbol=payload["s"],
        trade_id=int(payload["a"]),
        price=float(payload["p"]),
        quantity=float(payload["q"]),
        side=TradeSide.SELL if is_buyer_maker else TradeSide.BUY,
        is_buyer_maker=is_buyer_maker,
        timestamp=_ms_to_dt(int(payload["T"])),
    )


def parse_depth_ws(payload: Dict[str, Any], symbol: str) -> OrderBookSnapshot:
    """Parse a ``<symbol>@depth<levels>@100ms`` partial-book-depth stream payload."""
    timestamp = _ms_to_dt(int(payload["T"])) if "T" in payload else datetime.now(timezone.utc)
    return OrderBookSnapshot(
        symbol=symbol,
        bids=_levels(payload["b"]) if "b" in payload else _levels(payload["bids"]),
        asks=_levels(payload["a"]) if "a" in payload else _levels(payload["asks"]),
        last_update_id=int(payload.get("lastUpdateId", payload.get("u", 0))),
        timestamp=timestamp,
    )
