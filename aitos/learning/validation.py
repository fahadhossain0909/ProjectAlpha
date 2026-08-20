"""Validation gate around the canonical ProjectAlpha backtest engine."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from aitos.backtest.engine import BacktestEngine, BacktestResult


@dataclass(frozen=True)
class ValidationPolicy:
    min_total_return: float = 0.0
    max_drawdown: float = 0.25
    min_sharpe: float = 0.0
    min_trades: int = 1
    require_improvement_over_champion: bool = True


@dataclass(frozen=True)
class ValidationResult:
    passed: bool
    reason: str
    candidate: BacktestResult
    champion: BacktestResult | None = None


class CandidateValidator:
    """Evaluate candidates with the same deterministic engine used everywhere."""

    def __init__(self, policy: ValidationPolicy | None = None) -> None:
        self.policy = policy or ValidationPolicy()

    def evaluate(
        self,
        candidate_events: Iterable[Any],
        candidate_strategy: Callable,
        mark_price: Callable,
        champion_result: BacktestResult | None = None,
        initial_cash: float = 10_000.0,
        fee_rate: float = 0.0004,
        slippage_bps: float = 0.0,
    ) -> ValidationResult:
        candidate = BacktestEngine(initial_cash, fee_rate, slippage_bps).run(
            candidate_events, candidate_strategy, mark_price
        )
        m = candidate.metrics
        if m.total_return < self.policy.min_total_return:
            return ValidationResult(False, "candidate return below minimum", candidate, champion_result)
        if m.max_drawdown > self.policy.max_drawdown:
            return ValidationResult(False, "candidate drawdown above maximum", candidate, champion_result)
        if m.sharpe < self.policy.min_sharpe:
            return ValidationResult(False, "candidate Sharpe below minimum", candidate, champion_result)
        if m.trades < self.policy.min_trades:
            return ValidationResult(False, "insufficient candidate trades", candidate, champion_result)
        if self.policy.require_improvement_over_champion and champion_result is not None:
            cm = champion_result.metrics
            if m.total_return <= cm.total_return and m.max_drawdown >= cm.max_drawdown:
                return ValidationResult(False, "candidate does not improve return or drawdown", candidate, champion_result)
        return ValidationResult(True, "candidate passed configured validation gate", candidate, champion_result)
