"""Liquidity quality scoring — spec section 32.1's "Liquidity quality
(zone proximity, sweep potential)" dimension, computed from a live
``OrderBookSnapshot`` (spread tightness + depth balance) since that's
what this layer actually has without a full liquidity-zone map module.
"""

from __future__ import annotations

import math

from aitos.models.market import OrderBookSnapshot


def liquidity_quality_score(book: OrderBookSnapshot, typical_spread_bps: float = 5.0) -> float:
    """0-10: tighter spread and more balanced two-sided depth score higher.

    ``typical_spread_bps`` is the spread you'd expect in normal conditions
    for this symbol (in basis points of mid price) — pass a
    symbol-appropriate value if you have one; the default is a reasonable
    major-pair assumption.
    """
    if not book.bids or not book.asks:
        return 0.0

    mid = (book.best_bid + book.best_ask) / 2
    if mid <= 0:
        return 0.0

    spread_bps = (book.spread / mid) * 10_000
    spread_score = max(0.0, min(10.0, 10.0 - (spread_bps / typical_spread_bps) * 5.0))

    # Balanced depth (ratio near 1.0) scores higher than heavily lopsided books;
    # a wildly imbalanced book means one side will move price a lot on a fill.
    ratio = book.depth_ratio
    if ratio == float("inf"):
        balance_score = 0.0
    else:
        # distance of log(ratio) from 0 (perfectly balanced) — symmetric in both directions
        imbalance = abs(math.log(ratio)) if ratio > 0 else 10.0
        balance_score = max(0.0, 10.0 - imbalance * 5.0)

    return round((spread_score + balance_score) / 2, 2)
