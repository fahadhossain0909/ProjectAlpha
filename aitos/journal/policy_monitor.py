"""Monitor active policy performance and create rollback candidates."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, Mapping

@dataclass(frozen=True)
class PolicyHealth:
    version: str
    observations: int
    avg_r: float
    win_rate: float
    baseline_avg_r: float
    degradation: float
    rollback_recommended: bool
    reason: str
    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()

def evaluate_policy_health(version: str, outcomes: list[Mapping[str, Any]], *, baseline_avg_r: float, min_observations: int = 30, max_degradation: float = 0.20, min_avg_r: float = 0.0) -> PolicyHealth:
    rs = [float(x["r_multiple"]) for x in outcomes if isinstance(x.get("r_multiple"), (int, float))]
    wins = sum(1 for r in rs if r > 0)
    avg = sum(rs) / len(rs) if rs else 0.0
    degradation = (baseline_avg_r - avg) / abs(baseline_avg_r) if baseline_avg_r > 0 else 0.0
    bad = len(rs) >= min_observations and (avg < min_avg_r or degradation >= max_degradation)
    reason = "rollback_recommended" if bad else ("insufficient_observations" if len(rs) < min_observations else "policy_within_guardrails")
    return PolicyHealth(version, len(rs), avg, wins / len(rs) if rs else 0.0, baseline_avg_r, degradation, bad, reason)
