"""Historical market-event adapter for the shared AITOS intelligence pipeline.

This module deliberately keeps event handling synchronous and deterministic.
It feeds the same OrderFlow/Footprint components used by live processing,
without introducing a second backtest-specific signal formula.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from aitos.models.market import TradeTick, OrderBookSnapshot
from aitos.intelligence.order_flow_engine import OrderFlowEngine
from aitos.intelligence.footprint import FootprintEngine
from aitos.intelligence.footprint_signals import FootprintSignalEngine, FootprintSignals


@dataclass(frozen=True)
class HistoricalMarketState:
    symbol: str
    latest_trade: TradeTick | None
    latest_order_book: OrderBookSnapshot | None
    footprint_signals: FootprintSignals | None


class HistoricalMarketAdapter:
    """Feed historical trades through the same stateful intelligence primitives."""

    def __init__(self, symbol: str, tick_size: float, trade_window: int = 500) -> None:
        if trade_window <= 0:
            raise ValueError("trade_window must be positive")
        self.symbol = symbol
        self.order_flow = OrderFlowEngine(max_trades=trade_window)
        self.footprint = FootprintEngine(tick_size=tick_size)
        self.footprint_signals = FootprintSignalEngine()
        self._trades: list[TradeTick] = []
        self._latest_trade: TradeTick | None = None
        self._latest_book: OrderBookSnapshot | None = None
        self._last_signals: FootprintSignals | None = None

    def on_trade(self, trade: TradeTick) -> None:
        if trade.symbol != self.symbol:
            raise ValueError("trade symbol does not match adapter symbol")
        self.order_flow.update(trade)
        self._trades.append(trade)
        if len(self._trades) > self.order_flow.max_trades:
            self._trades.pop(0)
        self._latest_trade = trade
        self._last_signals = self.footprint_signals.evaluate(
            self.footprint.build(self._trades)
        )

    def on_order_book(self, book: OrderBookSnapshot) -> None:
        if book.symbol != self.symbol:
            raise ValueError("order-book symbol does not match adapter symbol")
        self._latest_book = book

    def state(self) -> HistoricalMarketState:
        return HistoricalMarketState(
            symbol=self.symbol,
            latest_trade=self._latest_trade,
            latest_order_book=self._latest_book,
            footprint_signals=self._last_signals,
        )

    def feed(self, events: Iterable[TradeTick | OrderBookSnapshot]) -> HistoricalMarketState:
        for event in sorted(events, key=lambda item: item.timestamp):
            if isinstance(event, TradeTick):
                self.on_trade(event)
            elif isinstance(event, OrderBookSnapshot):
                self.on_order_book(event)
            else:
                raise TypeError(f"unsupported market event: {type(event).__name__}")
        return self.state()
