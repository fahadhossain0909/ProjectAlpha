"""Structured AMT engine built on executed trades and optional candles/L2."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Iterable, Sequence

from aitos.models.market import Kline, OrderBookSnapshot, TradeTick
from .volume_profile import VolumeProfile, build_volume_profile


class AuctionState(str, Enum):
    BALANCE = "balance"
    DISCOVERY_UP = "discovery_up"
    DISCOVERY_DOWN = "discovery_down"
    ACCEPTANCE = "acceptance"
    REJECTION = "rejection"
    ROTATION = "rotation"
    TREND = "trend"
    UNKNOWN = "unknown"


class DayType(str, Enum):
    UNKNOWN = "unknown"
    NORMAL = "normal"
    TREND = "trend"
    DOUBLE_DISTRIBUTION = "double_distribution"
    NEUTRAL = "neutral"


class ValueMigration(str, Enum):
    UP = "up"
    DOWN = "down"
    FLAT = "flat"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class AMTContext:
    profile: VolumeProfile
    state: AuctionState
    day_type: DayType
    value_migration: ValueMigration
    acceptance: float
    rejection: float
    price_location: float
    ib_high: float
    ib_low: float
    confidence: float
    rationale: tuple[str, ...]

    @property
    def poc(self) -> float:
        return self.profile.poc

    @property
    def vah(self) -> float:
        return self.profile.vah

    @property
    def val(self) -> float:
        return self.profile.val


class AMTEngine:
    """Deterministic AMT context builder.

    Session boundaries are UTC by default. For crypto, callers should supply
    the desired session's start/end rather than assuming a single exchange day.
    """

    def __init__(self, tick_size: float, value_area_pct: float = 0.70, ib_minutes: int = 60) -> None:
        if tick_size <= 0:
            raise ValueError("tick_size must be > 0")
        if not 0 < value_area_pct <= 1:
            raise ValueError("value_area_pct must be in (0, 1]")
        if ib_minutes <= 0:
            raise ValueError("ib_minutes must be > 0")
        self.tick_size = tick_size
        self.value_area_pct = value_area_pct
        self.ib_minutes = ib_minutes

    def analyze(
        self,
        trades: Iterable[TradeTick],
        klines: Sequence[Kline] | None = None,
        book: OrderBookSnapshot | None = None,
        previous_profile: VolumeProfile | None = None,
        session_start: datetime | None = None,
    ) -> AMTContext:
        ticks = sorted((t for t in trades if t.quantity > 0 and t.price > 0), key=lambda t: t.timestamp)
        profile = build_volume_profile(ticks, self.tick_size, self.value_area_pct)
        if not ticks:
            return AMTContext(profile, AuctionState.UNKNOWN, DayType.UNKNOWN, ValueMigration.UNKNOWN, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, ("no valid trades",))

        last = ticks[-1].price
        if profile.vah > profile.val:
            location = max(0.0, min(1.0, (last - profile.val) / (profile.vah - profile.val)))
        else:
            location = 0.5

        if session_start is None:
            session_start = ticks[0].timestamp.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        session_end = session_start + timedelta(minutes=self.ib_minutes)
        ib_ticks = [t for t in ticks if session_start <= t.timestamp.astimezone(timezone.utc) < session_end]
        ib_high = max((t.price for t in ib_ticks), default=0.0)
        ib_low = min((t.price for t in ib_ticks), default=0.0)

        recent = ticks[-min(len(ticks), 50):]
        recent_inside = sum(profile.val <= t.price <= profile.vah for t in recent) / len(recent)
        acceptance = recent_inside
        outside = 1.0 - recent_inside
        rejection = 0.0
        if outside > 0:
            returned = sum(profile.val <= t.price <= profile.vah for t in recent[-min(len(recent), 10):])
            rejection = min(1.0, returned / max(1, min(len(recent), 10)))

        if previous_profile is None:
            migration = ValueMigration.UNKNOWN
        elif profile.poc > previous_profile.poc + self.tick_size:
            migration = ValueMigration.UP
        elif profile.poc < previous_profile.poc - self.tick_size:
            migration = ValueMigration.DOWN
        else:
            migration = ValueMigration.FLAT

        width = profile.vah - profile.val
        outside_strength = abs(last - profile.poc) / width if width > 0 else 0.0
        if acceptance >= 0.75 and outside_strength < 0.75:
            state = AuctionState.BALANCE
        elif last > profile.vah and rejection < 0.3:
            state = AuctionState.ACCEPTANCE if acceptance < 0.75 else AuctionState.DISCOVERY_UP
        elif last < profile.val and rejection < 0.3:
            state = AuctionState.ACCEPTANCE if acceptance < 0.75 else AuctionState.DISCOVERY_DOWN
        elif rejection >= 0.3:
            state = AuctionState.REJECTION
        else:
            state = AuctionState.ROTATION

        if width > 0 and last > profile.vah and migration == ValueMigration.UP:
            state = AuctionState.TREND
        elif width > 0 and last < profile.val and migration == ValueMigration.DOWN:
            state = AuctionState.TREND

        if width > 0 and profile.total_volume > 0:
            concentration = max(v for _, v in profile.bins) / profile.total_volume
            day_type = DayType.TREND if state == AuctionState.TREND else DayType.NORMAL if concentration < 0.25 else DayType.NEUTRAL
        else:
            day_type = DayType.UNKNOWN

        book_bonus = 0.0
        if book is not None:
            bid = sum(q for _, q in book.bids)
            ask = sum(q for _, q in book.asks)
            if bid + ask > 0:
                book_bonus = min(0.15, abs(bid - ask) / (bid + ask) * 0.15)

        confidence = min(1.0, 0.35 + min(0.35, len(ticks) / 1000) + acceptance * 0.2 + book_bonus)
        rationale = [
            f"state={state.value}",
            f"day_type={day_type.value}",
            f"poc={profile.poc:g}",
            f"vah={profile.vah:g}",
            f"val={profile.val:g}",
            f"value_migration={migration.value}",
            f"acceptance={acceptance:.3f}",
            f"rejection={rejection:.3f}",
        ]
        if ib_high:
            rationale.append(f"initial_balance={ib_low:g}-{ib_high:g}")
        return AMTContext(profile, state, day_type, migration, acceptance, rejection, location, ib_high, ib_low, round(confidence, 4), tuple(rationale))
