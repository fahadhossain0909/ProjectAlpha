"""End-to-end historical runner for ProjectAlpha intelligence and L2 execution."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Iterable
from aitos.backtest.market_adapter import HistoricalMarketAdapter, HistoricalMarketState
from aitos.backtest.replay import MarketReplay
from aitos.backtest.l2_execution import BookLevel, L2ExecutionModel
from aitos.backtest.execution import ExecutionSimulator
from aitos.models.market import TradeTick, OrderBookSnapshot

@dataclass(frozen=True)
class HistoricalDecision:
    direction: str
    confidence: float
    quantity: float

@dataclass(frozen=True)
class HistoricalRunResult:
    states: int
    decisions: int
    fills: int
    requested_quantity: float
    filled_quantity: float
    final_equity: float
    total_return: float
    total_fees: float

class ProjectAlphaHistoricalRunner:
    """Run shared historical intelligence and consume visible L2 liquidity."""
    def __init__(self, symbol: str, tick_size: float, initial_cash: float,
                 fee_rate: float = 0.0004, slippage_bps: float = 0.0,
                 trade_window: int = 500, max_book_levels: int | None = None) -> None:
        self.adapter = HistoricalMarketAdapter(symbol, tick_size, trade_window)
        self.execution = ExecutionSimulator(initial_cash, fee_rate, slippage_bps)
        self.l2 = L2ExecutionModel(max_levels=max_book_levels)
        self.initial_cash = initial_cash

    def run(self, events: Iterable[TradeTick | OrderBookSnapshot],
            decide: Callable[[HistoricalMarketState], HistoricalDecision]) -> HistoricalRunResult:
        ordered = MarketReplay(events)
        states = decisions = fills = 0
        requested = filled = 0.0
        last_price = 0.0
        for event in ordered.events:
            if isinstance(event, TradeTick):
                self.adapter.on_trade(event)
                last_price = event.price
            else:
                self.adapter.on_order_book(event)
                if event.best_bid > 0 and event.best_ask > 0:
                    last_price = (event.best_bid + event.best_ask) / 2.0
            state = self.adapter.state()
            states += 1
            decision = decide(state)
            decisions += 1
            if decision.quantity <= 0 or decision.direction not in {"long", "short"}:
                continue
            book = state.latest_order_book
            if book is None:
                continue
            side = "buy" if decision.direction == "long" else "sell"
            bids = [BookLevel(level.price, level.quantity) for level in book.bids]
            asks = [BookLevel(level.price, level.quantity) for level in book.asks]
            result = self.l2.execute(side, decision.quantity, bids, asks)
            requested += result.requested_quantity
            filled += result.filled_quantity
            if result.filled_quantity > 0:
                self.execution.execute(side, result.filled_quantity, result.average_price)
                fills += 1
                last_price = result.average_price
        final_equity = self.execution.snapshot(last_price).equity if last_price > 0 else self.initial_cash
        return HistoricalRunResult(states, decisions, fills, requested, filled,
                                   final_equity, final_equity / self.initial_cash - 1.0,
                                   self.execution.fees)
