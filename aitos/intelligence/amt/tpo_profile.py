"""Time-at-price (TPO) Market Profile primitives.

Unlike volume profile, TPO requires time/bracket observations. This module
therefore consumes explicit price observations tagged with a bracket and never
infers TPO from trade volume.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Iterable


class TPOBracket(str, Enum):
    """Human-readable bracket identifiers are assigned by bracket index."""


@dataclass(frozen=True)
class TPOObservation:
    timestamp: datetime
    price: float


@dataclass(frozen=True)
class TPOProfile:
    bins: tuple[tuple[float, int], ...]
    poc: float
    vah: float
    val: float
    high: float
    low: float
    total_tpo: int
    bracket_count: int
    single_prints: tuple[float, ...]
    poor_high: bool
    poor_low: bool
    excess_high: bool
    excess_low: bool


def _bin(price: float, tick_size: float) -> float:
    return round(round(price / tick_size) * tick_size, 12)


def build_tpo_profile(
    observations: Iterable[TPOObservation],
    tick_size: float,
    bracket_minutes: int = 30,
    value_area_pct: float = 0.70,
) -> TPOProfile:
    if tick_size <= 0 or bracket_minutes <= 0:
        raise ValueError("tick_size and bracket_minutes must be > 0")
    if not 0 < value_area_pct <= 1:
        raise ValueError("value_area_pct must be in (0, 1]")

    rows = sorted((o for o in observations if o.price > 0), key=lambda o: o.timestamp)
    if not rows:
        return TPOProfile((), 0, 0, 0, 0, 0, 0, 0, (), False, False, False, False)

    origin = rows[0].timestamp
    brackets: dict[int, set[float]] = {}
    for obs in rows:
        idx = int((obs.timestamp - origin).total_seconds() // (bracket_minutes * 60))
        brackets.setdefault(idx, set()).add(_bin(obs.price, tick_size))

    counts: dict[float, int] = {}
    for prices in brackets.values():
        for price in prices:
            counts[price] = counts.get(price, 0) + 1

    ordered = sorted(counts.items())
    total = sum(v for _, v in ordered)
    poc_index = max(range(len(ordered)), key=lambda i: (ordered[i][1], -ordered[i][0]))
    target = total * value_area_pct
    covered = ordered[poc_index][1]
    included = {poc_index}
    left, right = poc_index - 1, poc_index + 1
    while covered < target and (left >= 0 or right < len(ordered)):
        lv = ordered[left][1] if left >= 0 else -1
        rv = ordered[right][1] if right < len(ordered) else -1
        if rv > lv:
            included.add(right); covered += rv; right += 1
        else:
            included.add(left); covered += lv; left -= 1

    prices = [ordered[i][0] for i in included]
    high = ordered[-1][0]
    low = ordered[0][0]
    single = tuple(p for p, c in ordered if c == 1)

    # Extremes with two or more TPOs at the extreme are "poor" candidates.
    high_count = ordered[-1][1]
    low_count = ordered[0][1]
    poor_high = high_count > 1
    poor_low = low_count > 1
    # A one-bracket extreme is only a candidate for excess when the adjacent
    # price level has materially more TPOs; callers should confirm with order flow.
    excess_high = len(ordered) >= 2 and high_count == 1 and ordered[-2][1] >= 3
    excess_low = len(ordered) >= 2 and low_count == 1 and ordered[1][1] >= 3

    return TPOProfile(
        bins=tuple(ordered), poc=ordered[poc_index][0], vah=max(prices), val=min(prices),
        high=high, low=low, total_tpo=total, bracket_count=len(brackets),
        single_prints=single, poor_high=poor_high, poor_low=poor_low,
        excess_high=excess_high, excess_low=excess_low,
    )
