"""JournalSystem — spec §34.

Subscribes to the Trade Lifecycle's own events (no direct coupling — pure
Event Bus, per the AI Constitution) and automatically records a
``PRE_TRADE`` journal entry with a full ``TradeExplanation`` when a
position opens, and a ``POST_TRADE`` entry with the outcome when it
closes. ``record_mistake`` gives a human or a future Learning Agent a way
to add mistakes/lessons/improvements after the fact. Periodic reviews
(``generate_daily_review`` etc.) wrap the pure functions in
``reviews.py`` and persist + publish them the same way.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List, Optional

from aitos.core.contracts import AITOSModule, Event, EventResponse, HealthStatus, ModuleStatus
from aitos.core.exceptions import ModuleNotInitializedError
from aitos.eventbus.redis_bus import EventBus, Subscription
from aitos.journal import reviews
from aitos.journal.models import DailyReview, JournalEntry, JournalEntryType, MonthlyReview, WeeklyReview
from aitos.journal.repository import JournalRepository
from aitos.logging_setup import get_logger
from aitos.models.trade import Trade
from aitos.risk.risk_engine import RiskEngine
from aitos.xai.explanation import TradeExplanation, build_trade_explanation

logger = get_logger("aitos.journal.system")

TOPIC_DAILY_REVIEW = "journal.daily_review"
TOPIC_WEEKLY_REVIEW = "journal.weekly_review"
TOPIC_MONTHLY_REVIEW = "journal.monthly_review"
TOPIC_MISTAKE_RECORDED = "journal.mistake_recorded"


class JournalSystem(AITOSModule):
    def __init__(
        self,
        event_bus: EventBus,
        repository: Optional[JournalRepository] = None,
        risk_engine: Optional[RiskEngine] = None,
    ) -> None:
        self._event_bus = event_bus
        self._repository = repository
        self._risk_engine = risk_engine
        self._initialized = False
        self._subscriptions: List[Subscription] = []
        self._explanations: Dict[str, TradeExplanation] = {}
        self._entries: List[JournalEntry] = []
        self._last_event_time: Optional[str] = None

    # -- AITOSModule contract -------------------------------------------------

    @property
    def module_id(self) -> str:
        return "journal-system"

    @property
    def version(self) -> str:
        return "1.0.0"

    async def initialize(self, config: Dict[str, Any]) -> None:
        if self._initialized:
            return
        self._subscriptions.append(
            await self._event_bus.subscribe("trade.position_opened", self._on_position_opened, group="journal")
        )
        self._subscriptions.append(
            await self._event_bus.subscribe("trade.position_closed", self._on_position_closed, group="journal")
        )
        self._subscriptions.append(
            await self._event_bus.subscribe("trade.rejected", self._on_rejected, group="journal")
        )
        self._initialized = True
        logger.info("JournalSystem initialized")

    async def health_check(self) -> HealthStatus:
        return HealthStatus(
            module_id=self.module_id,
            status=ModuleStatus.HEALTHY if self._initialized else ModuleStatus.UNHEALTHY,
            latency_ms=0.0,
            last_event_time=self._last_event_time,
            details={"entries_recorded": len(self._entries), "explanations_cached": len(self._explanations)},
        )

    async def shutdown(self, grace_period_seconds: float = 30.0) -> None:
        for sub in self._subscriptions:
            sub.cancel()
        self._subscriptions.clear()
        logger.info("JournalSystem shut down")

    async def emit_events(self) -> AsyncIterator[Event]:
        return
        yield  # pragma: no cover

    async def handle_event(self, event: Event) -> Optional[EventResponse]:
        return None

    # -- Public API ---------------------------------------------------------------

    def get_explanation(self, trade_id: str) -> Optional[TradeExplanation]:
        return self._explanations.get(trade_id)

    def get_entries(self) -> List[JournalEntry]:
        return list(self._entries)

    async def record_mistake(self, trade_id: str, mistake: str, lesson: Optional[str] = None, improvement: Optional[str] = None) -> JournalEntry:
        """Human or Learning Agent input — spec §34.1's 'Mistakes identified
        (by Learning Agent or human)' / 'Lessons learned'."""
        self._require_initialized()
        entry = JournalEntry(
            trade_id=trade_id,
            entry_type=JournalEntryType.MISTAKE,
            market_context={},
            mistakes=[mistake],
            lessons=[lesson] if lesson else [],
            improvements=[improvement] if improvement else [],
        )
        await self._persist(entry)
        await self._event_bus.publish(
            Event(topic=TOPIC_MISTAKE_RECORDED, payload=entry.to_dict(), source_module=self.module_id)
        )
        return entry

    async def generate_daily_review(self, trades: List[Trade], date: str) -> DailyReview:
        self._require_initialized()
        review = reviews.daily_review(trades, date)
        entry = JournalEntry(trade_id=None, entry_type=JournalEntryType.DAILY, market_context=review.to_dict())
        await self._persist(entry)
        await self._event_bus.publish(Event(topic=TOPIC_DAILY_REVIEW, payload=review.to_dict(), source_module=self.module_id))
        return review

    async def generate_weekly_review(self, trades: List[Trade], week_start: str) -> WeeklyReview:
        self._require_initialized()
        review = reviews.weekly_review(trades, week_start)
        entry = JournalEntry(trade_id=None, entry_type=JournalEntryType.WEEKLY, market_context=review.to_dict())
        await self._persist(entry)
        await self._event_bus.publish(Event(topic=TOPIC_WEEKLY_REVIEW, payload=review.to_dict(), source_module=self.module_id))
        return review

    async def generate_monthly_review(self, trades: List[Trade], month: str, starting_equity: float = 10_000.0) -> MonthlyReview:
        self._require_initialized()
        review = reviews.monthly_review(trades, month, starting_equity)
        entry = JournalEntry(trade_id=None, entry_type=JournalEntryType.MONTHLY, market_context=review.to_dict())
        await self._persist(entry)
        await self._event_bus.publish(Event(topic=TOPIC_MONTHLY_REVIEW, payload=review.to_dict(), source_module=self.module_id))
        return review

    # -- Event handlers -------------------------------------------------------------

    async def _on_position_opened(self, event: Event) -> Optional[EventResponse]:
        trade_dict = event.payload
        risk_assessment = self._risk_engine.last_assessment if self._risk_engine else None
        explanation = build_trade_explanation(trade_dict, risk_assessment=risk_assessment)
        self._explanations[trade_dict.get("trade_id", "")] = explanation

        agent_consensus = trade_dict.get("agent_consensus", {}) or {}
        entry = JournalEntry(
            trade_id=trade_dict.get("trade_id"),
            entry_type=JournalEntryType.PRE_TRADE,
            market_context={"symbol": trade_dict.get("symbol"), "entry_price": trade_dict.get("entry_price"), "explanation": explanation.to_dict()},
            confidence_score=explanation.confidence_score,
            order_flow_observations={"order_flow_bias": agent_consensus.get("order_flow_bias")},
            liquidity_observations={"liquidity_quality": agent_consensus.get("liquidity_quality")},
            amt_observations={"auction_context": agent_consensus.get("auction_context"), "market_regime": agent_consensus.get("market_regime")},
            lead_lag_observations={"lead_lag": agent_consensus.get("lead_lag")},
        )
        await self._persist(entry)
        if self._repository is not None:
            await self._repository.save_trade_snapshot(trade_dict)
        self._last_event_time = entry.created_at
        return None

    async def _on_position_closed(self, event: Event) -> Optional[EventResponse]:
        trade_dict = event.payload
        entry = JournalEntry(
            trade_id=trade_dict.get("trade_id"),
            entry_type=JournalEntryType.POST_TRADE,
            market_context={
                "symbol": trade_dict.get("symbol"),
                "exit_price": trade_dict.get("exit_price"),
                "exit_reason": trade_dict.get("exit_reason"),
                "pnl": trade_dict.get("pnl"),
                "pnl_percent": trade_dict.get("pnl_percent"),
            },
        )
        await self._persist(entry)
        if self._repository is not None:
            await self._repository.save_trade_snapshot(trade_dict)
        self._last_event_time = entry.created_at
        return None

    async def _on_rejected(self, event: Event) -> Optional[EventResponse]:
        trade_dict = event.payload
        entry = JournalEntry(
            trade_id=trade_dict.get("trade_id"),
            entry_type=JournalEntryType.PRE_TRADE,
            market_context={"rejected": True, "reason": trade_dict.get("rejection_reason"), "symbol": trade_dict.get("symbol")},
        )
        await self._persist(entry)
        self._last_event_time = entry.created_at
        return None

    # -- Internals --------------------------------------------------------------

    async def _persist(self, entry: JournalEntry) -> None:
        self._entries.append(entry)
        if self._repository is not None:
            await self._repository.save_journal_entry(entry)

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise ModuleNotInitializedError("JournalSystem.initialize() must be called first")
