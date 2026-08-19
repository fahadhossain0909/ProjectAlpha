"""Structured AMT engine built on executed trades and optional L2 data.

The engine intentionally separates *measurement* (volume profile/session
levels) from *interpretation* (auction state/day type). It never treats candle
volume as volume-at-price.
"""
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
    ib_range: float = 0.0
    ib_extension_up: float = 0.0
    ib_extension_down: float = 0.0
    open_price: float = 0.0
    open_location: str = "unknown"
    book_imbalance: float = 0.0
    data_quality: float = 0.0

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

    ``session_start`` defines the session being analysed. For 24/7 crypto,
    callers should explicitly select the session convention (UTC, exchange
    session, London, New York, etc.) instead of silently assuming one day.
    """

    def __init__(
        self,
        tick_size: float,
        value_area_pct: float = 0.70,
        ib_minutes: int = 60,
        acceptance_window: int = 50,
        rejection_window: int = 10,
    ) -> None:
        if tick_size <= 0:
            raise ValueError("tick_size must be > 0")
        if not 0 < value_area_pct <= 1:
            raise ValueError("value_area_pct must be in (0, 1]")
        if ib_minutes <= 0 or acceptance_window <= 0 or rejection_window <= 0:
            raise ValueError("session/window parameters must be > 0")
        self.tick_size = tick_size
        self.value_area_pct = value_area_pct
        self.ib_minutes = ib_minutes
        self.acceptance_window = acceptance_window
        self.rejection_window = rejection_window

    @staticmethod
    def _session_start(ts: datetime) -> datetime:
        return ts.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    @staticmethod
    def _open_location(open_price: float, profile: VolumeProfile) -> str:
        if not open_price or not profile.bins:
            return "unknown"
        if open_price > profile.vah:
            return "above_value"
        if open_price < profile.val:
            return "below_value"
        if open_price >= profile.poc:
            return "inside_value_upper"
        return "inside_value_lower"

    def analyze(
        self,
        trades: Iterable[TradeTick],
        klines: Sequence[Kline] | None = None,
        book: OrderBookSnapshot | None = None,
        previous_profile: VolumeProfile | None = None,
        session_start: datetime | None = None,
    ) -> AMTContext:
        ticks = sorted(
            (t for t in trades if t.quantity > 0 and t.price > 0),
            key=lambda t: t.timestamp,
        )
        profile = build_volume_profile(ticks, self.tick_size, self.value_area_pct)
        if not ticks:
            return AMTContext(
                profile, AuctionState.UNKNOWN, DayType.UNKNOWN, ValueMigration.UNKNOWN,
                0.0, 0.0, 0.0, 0.0, 0.0, 0.0, ("no valid trades",), data_quality=0.0,
            )

        if session_start is None:
            session_start = self._session_start(ticks[0].timestamp)
        session_start = session_start.astimezone(timezone.utc)
        session_end = session_start + timedelta(days=1)
        ib_end = session_start + timedelta(minutes=self.ib_minutes)
        session_ticks = [t for t in ticks if session_start <= t.timestamp.astimezone(timezone.utc) < session_end]
        ib_ticks = [t for t in session_ticks if t.timestamp.astimezone(timezone.utc) < ib_end]
        if not session_ticks:
            session_ticks = ticks
        last = session_ticks[-1].price
        open_price = session_ticks[0].price

        width = profile.vah - profile.val
        location = max(0.0, min(1.0, (last - profile.val) / width)) if width > 0 else 0.5
        ib_high = max((t.price for t in ib_ticks), default=0.0)
        ib_low = min((t.price for t in ib_ticks), default=0.0)
        ib_range = max(0.0, ib_high - ib_low)
        ib_extension_up = max(0.0, last - ib_high) if ib_high else 0.0
        ib_extension_down = max(0.0, ib_low - last) if ib_low else 0.0

        recent = session_ticks[-min(len(session_ticks), self.acceptance_window):]
        inside_count = sum(profile.val <= t.price <= profile.vah for t in recent)
        acceptance = inside_count / len(recent) if recent else 0.0
        tail = recent[-min(len(recent), self.rejection_window):]
        outside_tail = sum(t.price > profile.vah or t.price < profile.val for t in tail)
        returned_to_value = sum(profile.val <= t.price <= profile.vah for t in tail)
        rejection = (returned_to_value / len(tail)) if outside_tail else 0.0

        if previous_profile is None:
            migration = ValueMigration.UNKNOWN
        elif profile.poc > previous_profile.poc + self.tick_size:
            migration = ValueMigration.UP
        elif profile.poc < previous_profile.poc - self.tick_size:
            migration = ValueMigration.DOWN
        else:
            migration = ValueMigration.FLAT

        outside_strength = abs(last - profile.poc) / width if width > 0 else 0.0
        if last > profile.vah and migration == ValueMigration.UP and acceptance < 0.75:
            state = AuctionState.TREND
        elif last < profile.val and migration == ValueMigration.DOWN and acceptance < 0.75:
            state = AuctionState.TREND
        elif last > profile.vah and outside_tail and rejection < 0.3:
            state = AuctionState.ACCEPTANCE
        elif last < profile.val and outside_tail and rejection < 0.3:
            state = AuctionState.ACCEPTANCE
        elif rejection >= 0.3:
            state = AuctionState.REJECTION
        elif acceptance >= 0.75 and outside_strength < 0.75:
            state = AuctionState.BALANCE
        elif last > profile.vah:
            state = AuctionState.DISCOVERY_UP
        elif last < profile.val:
            state = AuctionState.DISCOVERY_DOWN
        else:
            state = AuctionState.ROTATION

        # A crude concentration metric is deliberately not called a full
        # Market Profile day-type classifier. Double distribution is detected
        # only when two separated local volume peaks are materially stronger
        # than the intervening valley.
        day_type = self._classify_day_type(profile, state)

        book_imbalance = 0.0
        if book is not None:
            bid = sum(q for _, q in book.bids)
            ask = sum(q for _, q in book.asks)
            if bid + ask > 0:
                book_imbalance = (bid - ask) / (bid + ask)

        data_quality = min(1.0, 0.4 + min(0.4, len(session_ticks) / 1000.0) + (0.2 if book is not None else 0.0))
        confidence = min(
            1.0,
            0.25 + 0.35 * data_quality + 0.20 * max(acceptance, rejection) + 0.20 * min(1.0, abs(book_imbalance)),
        )
        rationale = [
            f"state={state.value}",
            f"day_type={day_type.value}",
            f"poc={profile.poc:g}",
            f"vah={profile.vah:g}",
            f"val={profile.val:g}",
            f"value_migration={migration.value}",
            f"acceptance={acceptance:.3f}",
            f"rejection={rejection:.3f}",
            f"open_location={self._open_location(open_price, profile)}",
        ]
        if ib_high:
            rationale.append(f"initial_balance={ib_low:g}-{ib_high:g}")
            rationale.append(f"ib_extensions=up:{ib_extension_up:g},down:{ib_extension_down:g}")
        if book is not None:
            rationale.append(f"book_imbalance={book_imbalance:.3f}")
        return AMTContext(
            profile=profile,
            state=state,
            day_type=day_type,
            value_migration=migration,
            acceptance=round(acceptance, 4),
            rejection=round(rejection, 4),
            price_location=round(location, 4),
            ib_high=ib_high,
            ib_low=ib_low,
            confidence=round(confidence, 4),
            rationale=tuple(rationale),
            ib_range=ib_range,
            ib_extension_up=ib_extension_up,
            ib_extension_down=ib_extension_down,
            open_price=open_price,
            open_location=self._open_location(open_price, profile),
            book_imbalance=round(book_imbalance, 4),
            data_quality=round(data_quality, 4),
        )

    @staticmethod
    def _classify_day_type(profile: VolumeProfile, state: AuctionState) -> DayType:
        if state == AuctionState.TREND:
            return DayType.TREND
        if len(profile.bins) < 5 or profile.total_volume <= 0:
            return DayType.UNKNOWN
        values = [v for _, v in profile.bins]
        mean = sum(values) / len(values)
        if mean <= 0:
            return DayType.UNKNOWN
        peaks = []
        for i in range(1, len(values) - 1):
            if values[i] > values[i - 1] and values[i] >= values[i + 1] and values[i] >= mean * 1.5:
                peaks.append(i)
        if len(peaks) >= 2:
            first, last = peaks[0], peaks[-1]
            valley = min(values[first:last + 1])
            peak_floor = min(values[first], values[last])
            if peak_floor > 0 and valley / peak_floor <= 0.55:
                return DayType.DOUBLE_DISTRIBUTION
        concentration = max(values) / profile.total_volume
        return DayType.NORMAL if concentration < 0.25 else DayType.NEUTRAL
