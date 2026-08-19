"""AI Kernel — central orchestrator of AITOS."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional

from aitos.agents.base_agent import AgentDecision, BaseAgent
from aitos.core.contracts import AITOSModule, Event, EventResponse, HealthStatus, ModuleStatus
from aitos.core.exceptions import (
    AgentNotRegisteredError,
    DecisionFusionError,
    GovernanceViolationError,
    ModuleNotInitializedError,
)
from aitos.eventbus.redis_bus import EventBus
from aitos.kernel.decision_fusion import DecisionFusionEngine
from aitos.logging_setup import get_logger

logger = get_logger("aitos.kernel")


@dataclass
class WorldState:
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    active_symbols: List[str] = field(default_factory=list)
    open_positions: Dict[str, Any] = field(default_factory=dict)
    risk_score: float = 0.0
    regime: str = "unknown"
    registered_agents: List[str] = field(default_factory=list)


@dataclass
class DecisionContext:
    symbol: str
    context: Dict[str, Any] = field(default_factory=dict)
    requested_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class FusedDecision:
    symbol: str
    direction: str
    confidence: float
    contributions: List[Dict[str, Any]]
    conflicting_evidence: List[str]
    fused_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class Action:
    action_type: str
    payload: Dict[str, Any]
    is_production: bool = True
    approved_by: Optional[str] = None


@dataclass
class GovernanceResult:
    approved: bool
    reason: str
    requires_human_approval: bool


class AIKernel(AITOSModule):
    def __init__(
        self,
        event_bus: EventBus,
        require_human_approval_for_prod: bool = True,
        fusion_engine: Optional[DecisionFusionEngine] = None,
    ) -> None:
        self._event_bus = event_bus
        self._require_human_approval_for_prod = require_human_approval_for_prod
        self._fusion_engine = fusion_engine or DecisionFusionEngine()
        self._initialized = False
        self._agents: Dict[str, BaseAgent] = {}
        self._world_state = WorldState()
        self._last_event_time: Optional[str] = None

    @property
    def module_id(self) -> str:
        return "ai-kernel"

    @property
    def version(self) -> str:
        return "1.1.0"

    async def initialize(self, config: Dict[str, Any]) -> None:
        if self._initialized:
            return
        self._initialized = True
        logger.info("AIKernel initialized")

    async def health_check(self) -> HealthStatus:
        return HealthStatus(
            module_id=self.module_id,
            status=ModuleStatus.HEALTHY if self._initialized else ModuleStatus.UNHEALTHY,
            latency_ms=0.0,
            last_event_time=self._last_event_time,
            details={"registered_agents": list(self._agents.keys())},
        )

    async def shutdown(self, grace_period_seconds: float = 30.0) -> None:
        for agent in list(self._agents.values()):
            await agent.shutdown(grace_period_seconds)
        self._agents.clear()
        logger.info("AIKernel shut down")

    async def emit_events(self) -> AsyncIterator[Event]:
        return
        yield  # pragma: no cover

    async def handle_event(self, event: Event) -> Optional[EventResponse]:
        self._last_event_time = datetime.now(timezone.utc).isoformat()
        self._update_world_state_from_event(event)
        for agent in self._agents.values():
            await agent.handle_event(event)
        return None

    async def register_agent(self, agent: BaseAgent) -> None:
        self._require_initialized()
        self._agents[agent.module_id] = agent
        self._world_state.registered_agents = list(self._agents.keys())
        logger.info("agent registered", extra={"aitos_extra": {"agent_id": agent.module_id}})

    async def deregister_agent(self, agent_id: str) -> None:
        self._require_initialized()
        if agent_id not in self._agents:
            raise AgentNotRegisteredError(f"Agent '{agent_id}' is not registered")
        del self._agents[agent_id]
        self._world_state.registered_agents = list(self._agents.keys())
        logger.info("agent deregistered", extra={"aitos_extra": {"agent_id": agent_id}})

    async def get_world_state(self) -> WorldState:
        self._require_initialized()
        return self._world_state

    async def request_decision(self, context: DecisionContext) -> FusedDecision:
        """Fuse scanner/module evidence when supplied; otherwise preserve the
        existing agent-consensus behavior for backward compatibility.

        A scanner can pass:
        {"direction": "long", "component_scores": {"trend_strength": 8, ...}}
        and the evidence engine becomes the authoritative confidence gate.
        """
        self._require_initialized()

        evidence = self._fusion_engine.fuse_context(context.context)
        if evidence is not None:
            contributions = [c.to_dict() for c in evidence.contributions]
            if evidence.missing_components:
                contributions.append({
                    "source": "missing_components",
                    "components": list(evidence.missing_components),
                })
            return FusedDecision(
                symbol=context.symbol,
                direction=evidence.direction,
                confidence=evidence.confidence,
                contributions=contributions,
                conflicting_evidence=(
                    [f"insufficient evidence: {', '.join(evidence.missing_components)}"]
                    if evidence.direction == "neutral" and evidence.missing_components
                    else []
                ),
            )

        if not self._agents:
            raise DecisionFusionError("No agents registered and no component evidence supplied")

        decisions: List[AgentDecision] = []
        for agent in self._agents.values():
            try:
                decisions.append(await agent.contribute_decision(context.context | {"symbol": context.symbol}))
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "agent failed to contribute decision",
                    extra={"aitos_extra": {"agent_id": agent.module_id, "error": str(exc)}},
                )

        if not decisions:
            raise DecisionFusionError("All agents failed to contribute a decision")

        direction_scores: Dict[str, float] = {"long": 0.0, "short": 0.0, "neutral": 0.0}
        total_weight = 0.0
        conflicting_evidence: List[str] = []
        for decision in decisions:
            agent = self._agents[decision.agent_id]
            weight = agent.consensus_weight * decision.confidence
            direction_scores[decision.direction] = direction_scores.get(decision.direction, 0.0) + weight
            total_weight += agent.consensus_weight

        fused_direction = max(direction_scores, key=direction_scores.get)
        fused_confidence = direction_scores[fused_direction] / total_weight if total_weight > 0 else 0.0
        directions_present = {d.direction for d in decisions}
        if len(directions_present) > 1:
            conflicting_evidence = [
                f"{d.agent_id} voted {d.direction} ({d.confidence:.2f}): {d.rationale}"
                for d in decisions if d.direction != fused_direction
            ]

        return FusedDecision(
            symbol=context.symbol,
            direction=fused_direction,
            confidence=round(min(fused_confidence, 1.0), 4),
            contributions=[d.to_dict() for d in decisions],
            conflicting_evidence=conflicting_evidence,
        )

    async def enforce_governance(self, action: Action) -> GovernanceResult:
        self._require_initialized()
        if action.is_production and self._require_human_approval_for_prod and not action.approved_by:
            result = GovernanceResult(
                approved=False,
                reason="Production action requires explicit human approval (approved_by is empty).",
                requires_human_approval=True,
            )
            logger.warning("governance rejected action", extra={"aitos_extra": {"action_type": action.action_type}})
            return result
        return GovernanceResult(approved=True, reason="Approved.", requires_human_approval=False)

    async def require_approval_or_raise(self, action: Action) -> None:
        result = await self.enforce_governance(action)
        if not result.approved:
            raise GovernanceViolationError(result.reason)

    def _update_world_state_from_event(self, event: Event) -> None:
        self._world_state.updated_at = datetime.now(timezone.utc).isoformat()
        symbol = event.payload.get("symbol")
        if symbol and symbol not in self._world_state.active_symbols:
            self._world_state.active_symbols.append(symbol)
        if event.topic.startswith("risk.score"):
            score = event.payload.get("score")
            if isinstance(score, (int, float)):
                self._world_state.risk_score = float(score)
        if event.topic.startswith("regime."):
            regime = event.payload.get("regime")
            if isinstance(regime, str):
                self._world_state.regime = regime

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise ModuleNotInitializedError("AIKernel.initialize() must be called first")
