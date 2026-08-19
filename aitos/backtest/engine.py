"""Integrated deterministic backtest runner.

Connects replay events to a user-supplied strategy/execution callback and
computes portfolio performance metrics without hiding execution assumptions.
"""
from __future__ import annotations
from dataclasses import dataclass
from math import sqrt
from typing import Callable, Iterable
from aitos.backtest.replay import MarketReplay, ReplayEvent
from aitos.backtest.execution import ExecutionSimulator

@dataclass(frozen=True)
class BacktestMetrics:
    initial_equity: float
    final_equity: float
    total_return: float
    max_drawdown: float
    sharpe: float
    total_fees: float
    trades: int
    wins: int
    losses: int
    win_rate: float
    profit_factor: float

@dataclass(frozen=True)
class BacktestResult:
    metrics: BacktestMetrics
    equity_curve: tuple[float, ...]

class BacktestEngine:
    """Replay events and record equity after each marked event.

    The strategy callback owns signal generation and calls ``execute`` when it
    wants a fill. This keeps the engine agnostic to the live strategy code.
    """
    def __init__(self, initial_cash: float, fee_rate: float = 0.0004, slippage_bps: float = 0.0) -> None:
        self.execution = ExecutionSimulator(initial_cash, fee_rate, slippage_bps)
        self.initial_cash = initial_cash
        self._trade_pnls: list[float] = []

    def run(
        self,
        events: Iterable[ReplayEvent],
        strategy: Callable[[ReplayEvent, ExecutionSimulator], None],
        mark_price: Callable[[ReplayEvent], float],
    ) -> BacktestResult:
        curve: list[float] = []
        replay = MarketReplay(events)
        for event in replay.events:
            before = self.execution.realized_pnl
            strategy(event, self.execution)
            after = self.execution.realized_pnl
            if after != before:
                self._trade_pnls.append(after - before)
            price = mark_price(event)
            curve.append(self.execution.snapshot(price).equity)
        return BacktestResult(self._metrics(curve), tuple(curve))

    def _metrics(self, curve: list[float]) -> BacktestMetrics:
        final = curve[-1] if curve else self.initial_cash
        total_return = round((final / self.initial_cash) - 1.0, 12)
        peak = self.initial_cash
        max_dd = 0.0
        for equity in curve:
            peak = max(peak, equity)
            if peak > 0:
                max_dd = max(max_dd, (peak - equity) / peak)
        returns = [(curve[i] / curve[i-1]) - 1.0 for i in range(1, len(curve)) if curve[i-1] > 0]
        if len(returns) > 1:
            mean = sum(returns) / len(returns)
            variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
            sharpe = sqrt(len(returns)) * mean / sqrt(variance) if variance > 0 else 0.0
        else:
            sharpe = 0.0
        wins = sum(1 for p in self._trade_pnls if p > 0)
        losses = sum(1 for p in self._trade_pnls if p < 0)
        gross_profit = sum(p for p in self._trade_pnls if p > 0)
        gross_loss = -sum(p for p in self._trade_pnls if p < 0)
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
        trades = wins + losses
        return BacktestMetrics(self.initial_cash, final, total_return, max_dd, sharpe, self.execution.fees, trades, wins, losses, wins / trades if trades else 0.0, profit_factor)
