"""Domain models for market data, mirroring the ClickHouse schema in the
AITOS spec (section 7.1: market_ohlcv, order_book_snapshots, trade_ticks)
plus funding rate / open interest, which the spec's Opportunity Scanner
(section 32) and Adaptive Leverage logic (section 30.2) depend on.

All frozen + serializable to plain dicts so they can travel as
``Event.payload`` on the Event Bus and be written straight to ClickHouse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Tuple


class TradeSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class Kline:
    """One OHLCV candle for a symbol/timeframe. Maps to ``market_ohlcv``."""

    symbol: str
    timeframe: str
    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float
    trades_count: int
    taker_buy_volume: float
    taker_buy_quote_volume: float
    is_closed: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "open_time": _iso(self.open_time),
            "close_time": _iso(self.close_time),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "quote_volume": self.quote_volume,
            "trades_count": self.trades_count,
            "taker_buy_volume": self.taker_buy_volume,
            "taker_buy_quote_volume": self.taker_buy_quote_volume,
            "is_closed": self.is_closed,
        }


@dataclass(frozen=True)
class OrderBookSnapshot:
    """Top-of-book / depth snapshot. Maps to ``order_book_snapshots``."""

    symbol: str
    bids: Tuple[Tuple[float, float], ...]  # ((price, qty), ...) sorted desc by price
    asks: Tuple[Tuple[float, float], ...]  # sorted asc by price
    last_update_id: int
    timestamp: datetime

    @property
    def best_bid(self) -> float:
        return self.bids[0][0] if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0][0] if self.asks else 0.0

    @property
    def spread(self) -> float:
        if not self.bids or not self.asks:
            return 0.0
        return self.best_ask - self.best_bid

    @property
    def depth_ratio(self) -> float:
        """bid depth / ask depth across the levels we have — >1 means bid-heavy."""
        bid_depth = sum(qty for _, qty in self.bids)
        ask_depth = sum(qty for _, qty in self.asks)
        return bid_depth / ask_depth if ask_depth else float("inf")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "bid_levels": [{"price": p, "qty": q} for p, q in self.bids],
            "ask_levels": [{"price": p, "qty": q} for p, q in self.asks],
            "spread": self.spread,
            "depth_ratio": self.depth_ratio,
            "last_update_id": self.last_update_id,
            "timestamp": _iso(self.timestamp),
        }


@dataclass(frozen=True)
class TradeTick:
    """A single executed trade. Maps to ``trade_ticks``."""

    symbol: str
    trade_id: int
    price: float
    quantity: float
    side: TradeSide  # taker side
    is_buyer_maker: bool
    timestamp: datetime

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "trade_id": self.trade_id,
            "price": self.price,
            "quantity": self.quantity,
            "side": self.side.value,
            "is_buyer_maker": self.is_buyer_maker,
            "timestamp": _iso(self.timestamp),
        }


@dataclass(frozen=True)
class FundingRate:
    symbol: str
    funding_rate: float
    funding_time: datetime
    mark_price: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "funding_rate": self.funding_rate,
            "funding_time": _iso(self.funding_time),
            "mark_price": self.mark_price,
        }


@dataclass(frozen=True)
class OpenInterest:
    symbol: str
    open_interest: float
    timestamp: datetime

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "open_interest": self.open_interest,
            "timestamp": _iso(self.timestamp),
        }
