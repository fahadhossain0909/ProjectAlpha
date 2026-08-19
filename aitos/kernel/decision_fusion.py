"""Evidence-based decision fusion for the AITOS trading brain.

This module replaces the old idea of treating the agent vote as the only
source of intelligence.  It accepts directional component evidence already
produced by the Opportunity Scanner (or future AMT/order-flow/ML/RL modules)
and fuses the available dimensions using explicit, inspectable weights.

Evidence values are normalized to the scanner's 0..10 scale.  A score of 10
means strong support for the proposed direction; 0 means no support.  The
fusion engine never invents a direction: callers must provide ``direction``.
This keeps direction selection in the scanner/strategy layer while making the
kernel responsible for confidence aggregation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional


DEFAULT_EVIDENCE_WEIGHTS: Dict[str, float] = {
    "trend_strength": 0.15,
    "liquidity_quality": 0.10,
    "order_flow_bias": 0.15,
    "auction_context": 0.10,
    "volatility": 0.05,
    "market_regime": 0.10,
    "lead_lag": 0.10,
    "funding_rate": 0.10,
    "open_interest_trend": 0.10,
    "rl_confidence": 0.05,
}


@dataclass(frozen=True)
class EvidenceContribution:
    """One normalized contribution to the fused decision."""

    source: str
    score: float
    weight: float
    weighted_score: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "score": self.score,
            "weight": self.weight,
            "weighted_score": self.weighted_score,
        }


@dataclass(frozen=True)
class EvidenceFusionResult:
    """Transparent result of component-evidence fusion."""

    direction: str
    confidence: float
    contributions: tuple[EvidenceContribution, ...]
    missing_components: tuple[str, ...]

    @property
    def supported(self) -> bool:
        return self.confidence > 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "direction": self.direction,
            "confidence": self.confidence,
            "contributions": [c.to_dict() for c in self.contributions],
            "missing_components": list(self.missing_components),
        }


class DecisionFusionEngine:
    """Fuse directional evidence without hiding the underlying signals.

    The engine is intentionally deterministic.  Later ML/RL models can feed
    their scores through the same interface without changing downstream
    contracts.  Only components actually present in ``component_scores`` are
    included in the denominator, so a missing optional signal does not
    silently count as zero evidence.
    """

    def __init__(
        self,
        weights: Optional[Mapping[str, float]] = None,
        min_confidence: float = 0.60,
    ) -> None:
        selected = dict(weights or DEFAULT_EVIDENCE_WEIGHTS)
        if not selected or any(weight < 0 for weight in selected.values()):
            raise ValueError("Fusion weights must be non-empty and non-negative")
        if sum(selected.values()) <= 0:
            raise ValueError("Fusion weights must have a positive total")
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError("min_confidence must be between 0 and 1")
        self._weights = selected
        self._min_confidence = min_confidence

    @property
    def weights(self) -> Dict[str, float]:
        return dict(self._weights)

    @property
    def min_confidence(self) -> float:
        return self._min_confidence

    def fuse(
        self,
        direction: str,
        component_scores: Mapping[str, Any],
    ) -> EvidenceFusionResult:
        if direction not in {"long", "short", "neutral"}:
            raise ValueError(f"Unsupported direction: {direction}")
        if direction == "neutral":
            return EvidenceFusionResult(
                direction="neutral",
                confidence=0.0,
                contributions=(),
                missing_components=tuple(self._weights),
            )

        contributions = []
        missing = []
        denominator = 0.0
        numerator = 0.0

        for name, weight in self._weights.items():
            raw = component_scores.get(name)
            if not isinstance(raw, (int, float)):
                missing.append(name)
                continue
            score = max(0.0, min(10.0, float(raw)))
            contributions.append(
                EvidenceContribution(
                    source=name,
                    score=round(score, 4),
                    weight=weight,
                    weighted_score=round(score * weight, 4),
                )
            numerator += score * weight
            denominator += weight

        confidence = (numerator / denominator) / 10.0 if denominator else 0.0
        # Below the configured threshold, the evidence is not strong enough
        # to authorize the proposed direction.  Returning neutral here makes
        # the gate explicit and easy to test.
        fused_direction = direction if confidence >= self._min_confidence else "neutral"

        return EvidenceFusionResult(
            direction=fused_direction,
            confidence=round(confidence, 4),
            contributions=tuple(contributions),
            missing_components=tuple(missing),
        )

    def fuse_context(self, context: Mapping[str, Any]) -> Optional[EvidenceFusionResult]:
        """Fuse scanner-style context when the required keys are present.

        Returns ``None`` when no component evidence is supplied.  This allows
        the existing agent-only kernel path to remain backward compatible.
        """
        direction = context.get("direction")
        component_scores = context.get("component_scores")
        if not isinstance(direction, str) or not isinstance(component_scores, Mapping):
            return None
        return self.fuse(direction, component_scores)
