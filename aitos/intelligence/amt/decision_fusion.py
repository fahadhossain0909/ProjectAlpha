"""Deterministic AMT decision-fusion helpers.

The fusion layer converts structured auction context into a directional score
without replacing the scanner's existing order-flow/liquidity signals.
"""
from __future__ import annotations

from dataclasses import dataclass
from .auction_intent import AuctionIntent, AuctionIntentResult, classify_auction_intent
from .engine import AMTContext


@dataclass(frozen=True)
class AMTDecisionSignal:
    score: float
    confidence: float
    intent: AuctionIntent
    veto: bool
    reasons: tuple[str, ...]


def fuse_amt_context(context: AMTContext, direction: str) -> AMTDecisionSignal:
    """Return a 0-10 directional AMT score and hard-conflict veto.

    This deliberately stays deterministic and bounded. A strong rejection
    against the requested direction can veto the AMT component; it does not
    independently place or cancel a trade.
    """
    if direction not in {"long", "short"}:
        raise ValueError("direction must be 'long' or 'short'")

    intent: AuctionIntentResult = classify_auction_intent(context)
    bullish = direction == "long"
    score = 5.0
    reasons: list[str] = []

    # Location: long prefers upper half only when acceptance/discovery agrees;
    # short prefers lower half under the same condition.
    location = context.price_location
    score += (location - 0.5) * 2.5 if bullish else (0.5 - location) * 2.5

    # Auction state alignment.
    state = context.state.value
    aligned = {"long": {"discovery_up", "acceptance", "trend"},
               "short": {"discovery_down", "acceptance", "trend"}}[direction]
    opposed = {"long": {"discovery_down", "rejection"},
               "short": {"discovery_up", "rejection"}}[direction]
    if state in aligned:
        score += 1.2; reasons.append(f"state supports {direction}")
    elif state in opposed:
        score -= 1.8; reasons.append(f"state opposes {direction}")

    # Value migration.
    migration = context.value_migration.value
    if (bullish and migration == "up") or ((not bullish) and migration == "down"):
        score += 0.8; reasons.append("value migration aligns")
    elif migration in {"up", "down"}:
        score -= 0.6; reasons.append("value migration conflicts")

    # Acceptance/rejection are bounded probabilities.
    score += min(1.0, max(-1.0, context.acceptance - context.rejection))
    if context.acceptance > context.rejection:
        reasons.append("acceptance dominates rejection")
    elif context.rejection > context.acceptance:
        reasons.append("rejection dominates acceptance")

    # Book imbalance is directional evidence, but intentionally capped.
    imbalance = max(-1.0, min(1.0, context.book_imbalance))
    score += imbalance * 0.7 if bullish else -imbalance * 0.7

    # Strong opposite rejection is an AMT veto, not a trade execution veto.
    veto = context.rejection >= 0.75 and context.acceptance < 0.35 and state == "rejection"
    if veto:
        reasons.append("strong auction rejection conflicts with direction")

    if intent.intent == AuctionIntent.INITIATIVE:
        reasons.append("initiative auction")
    elif intent.intent == AuctionIntent.RESPONSIVE:
        reasons.append("responsive auction")

    score = round(max(0.0, min(10.0, score)), 3)
    confidence = round(max(0.0, min(1.0, context.confidence * (0.6 + 0.4 * intent.confidence))), 4)
    return AMTDecisionSignal(score, confidence, intent.intent, veto, tuple(reasons))
