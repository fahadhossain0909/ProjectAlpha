"""End-to-end historical runner for ProjectAlpha intelligence and execution.

The runner intentionally keeps signal generation and execution separate: the
same historical market state is passed through the shared intelligence adapter,
then a caller-provided decision function determines whether to trade.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from aitos.backtest.market_adapter import HistoricalMarketAdapter, HistoricalMarketState
from aitos.backtest.replay import MarketReplay
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
    final_equity: float
    total_return: float
    total_fees: float


class ProjectAlphaHistoricalRunner:
    """Run shared market intelligence over timestamp-ordered historical data."""

    def __init__(self, symbol: str, tick_size: float, initial_cash: float,
                 fee_rate: float = 0.0004, slippage_bps: float = 0.0,
                 trade_window: int = 500) -> None:
        self.adapter = HistoricalMarketAdapter(symbol, tick_size, trade_window)
        self.execution = ExecutionSimulator(initial_cash, fee_rate, slippage_bps)
        self.initial_cash = initial_cash

    def run(
        self,
        events: Iterable[TradeTick | OrderBookSnapshot],
        decide: Callable[[HistoricalMarketState], HistoricalDecision],
    ) -> HistoricalRunResult:
        ordered = MarketReplay(events)
        states = decisions = fills = 0
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
            if decision.quantity > 0 and decision.direction in {"long", "short"} and last_price > 0:
                side = "buy" if decision.direction == "long" else "sell"
                self.execution.execute(side, decision.quantity, last_price)
                fills += 1
        final_equity = self.execution.snapshot(last_price).equity if last_price > 0 else self.initial_cash
        return HistoricalRunResult(
            states=states,
            decisions=decisions,
            fills=fills,
            final_equity=final_equity,
            total_return=final_equity / self.initial_cash - 1.0,
            total_fees=self.execution.fees,
        )
