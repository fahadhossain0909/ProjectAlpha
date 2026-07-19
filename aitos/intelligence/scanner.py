"""OpportunityScanner — spec section 32.

Scans a symbol universe, scores each across the ten spec §32.1 dimensions
(each 0-10), and turns the ones that clear a threshold into ``Opportunity``
objects the Trade Lifecycle already knows how to validate, size, and open.

Six dimensions are computed from real market data already flowing through
this codebase (trend/ADX, volatility/ATR, order flow/CVD, market
structure/BOS, liquidity/order book, market regime). Funding rate and open
interest trend use live exchange data too. Lead-lag uses a real (if
simplified) cross-symbol correlation. RL confidence is the one dimension
with no model behind it yet — see ``rl_policy.py`` for that seam.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from aitos.core.contracts import AITOSModule, Event, EventResponse, HealthStatus, ModuleStatus
from aitos.core.exceptions import ModuleNotInitializedError
from aitos.eventbus.redis_bus import EventBus
from aitos.exchange.base import ExchangeAdapter
from aitos.intelligence import indicators
from aitos.intelligence.funding import funding_rate_score
from aitos.intelligence.liquidity import liquidity_quality_score
from aitos.intelligence.open_interest import oi_trend_score
from aitos.intelligence.rl_policy import NeutralRLScorer, RLPolicyScorer
from aitos.logging_setup import get_logger
from aitos.models.market import OpenInterest
from aitos.models.trade import Opportunity, TradeSide

logger = get_logger("aitos.intelligence.scanner")

TOPIC_SCAN_COMPLETE = "market.opportunity_scanned"

# Spec §32.1's ten ranking dimensions, weighted to sum to 1.0.
DEFAULT_WEIGHTS: Dict[str, float] = {
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

REGIME_FIT_SCORE = {"trending": 9.0, "ranging": 4.0, "volatile": 3.0, "unknown": 5.0}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ScanCandidate:
    symbol: str
    direction: TradeSide
    composite_score: float  # 0-100
    component_scores: Dict[str, float]
    rationale: List[str]
    entry_price: float
    atr: float
    regime: str
    scanned_at: str = field(default_factory=_utc_now_iso)


def _volatility_fitness(atr_percentile: float, sweet_spot: float = 60.0, tolerance: float = 6.0) -> float:
    """Peaks at ``sweet_spot`` (some movement, not chaos), tapers at extremes."""
    return round(max(0.0, min(10.0, 10.0 - abs(atr_percentile - sweet_spot) / tolerance)), 2)


def determine_direction(structure_direction: str, cvd_score: float) -> Optional[TradeSide]:
    """Combine structure break + order-flow bias into a single directional
    call. Returns ``None`` when there's no clear edge (candidate is dropped)."""
    if structure_direction == "bullish_bos" and cvd_score >= 5.0:
        return TradeSide.LONG
    if structure_direction == "bearish_bos" and cvd_score <= 5.0:
        return TradeSide.SHORT
    if structure_direction == "none":
        if cvd_score >= 6.5:
            return TradeSide.LONG
        if cvd_score <= 3.5:
            return TradeSide.SHORT
    return None


class OpportunityScanner(AITOSModule):
    def __init__(
        self,
        event_bus: EventBus,
        exchange: ExchangeAdapter,
        symbols: List[str],
        timeframe: str = "15m",
        reference_symbol: str = "BTCUSDT",
        rl_scorer: Optional[RLPolicyScorer] = None,
        weights: Optional[Dict[str, float]] = None,
        min_score_threshold: float = 60.0,
        top_n: int = 5,
        kline_lookback: int = 100,
    ) -> None:
        self._event_bus = event_bus
        self._exchange = exchange
        self._symbols = symbols
        self._timeframe = timeframe
        self._reference_symbol = reference_symbol
        self._rl_scorer = rl_scorer or NeutralRLScorer()
        self._weights = weights or DEFAULT_WEIGHTS
        self._min_score_threshold = min_score_threshold
        self._top_n = top_n
        self._kline_lookback = kline_lookback
        self._initialized = False
        self._last_oi: Dict[str, OpenInterest] = {}
        self._last_scan_at: Optional[str] = None
        self._last_candidate_count = 0

    # -- AITOSModule contract -------------------------------------------------

    @property
    def module_id(self) -> str:
        return "opportunity-scanner"

    @property
    def version(self) -> str:
        return "1.0.0"

    async def initialize(self, config: Dict[str, Any]) -> None:
        if self._initialized:
            return
        await self._exchange.connect()
        self._initialized = True
        logger.info("OpportunityScanner initialized", extra={"aitos_extra": {"symbols": self._symbols}})

    async def health_check(self) -> HealthStatus:
        return HealthStatus(
            module_id=self.module_id,
            status=ModuleStatus.HEALTHY if self._initialized else ModuleStatus.UNHEALTHY,
            latency_ms=0.0,
            last_event_time=self._last_scan_at,
            details={"last_candidate_count": self._last_candidate_count, "symbols_tracked": len(self._symbols)},
        )

    async def shutdown(self, grace_period_seconds: float = 30.0) -> None:
        await self._exchange.close()
        logger.info("OpportunityScanner shut down")

    async def emit_events(self) -> AsyncIterator[Event]:
        return
        yield  # pragma: no cover

    async def handle_event(self, event: Event) -> Optional[EventResponse]:
        return None

    # -- Public API ---------------------------------------------------------------

    async def scan_symbol(self, symbol: str, reference_klines: Optional[list] = None) -> Optional[ScanCandidate]:
        self._require_initialized()
        klines = await self._exchange.fetch_klines(symbol, self._timeframe, limit=self._kline_lookback)
        if len(klines) < 20:
            return None

        order_book = await self._exchange.fetch_order_book(symbol, limit=20)
        funding = await self._exchange.fetch_funding_rate(symbol)
        oi_current = await self._exchange.fetch_open_interest(symbol)
        oi_previous = self._last_oi.get(symbol)

        atr = indicators.average_true_range(klines)
        vol_percentile = indicators.atr_percentile(klines)
        regime = indicators.classify_regime(klines)
        structure_direction, structure_strength = indicators.detect_structure_break(klines)
        cvd_score = indicators.cvd_trend_score(klines)

        direction = determine_direction(structure_direction, cvd_score)
        self._last_oi[symbol] = oi_current
        if direction is None:
            return None

        trend_score = min(10.0, indicators.adx(klines) / 10.0)
        volatility_score = _volatility_fitness(vol_percentile)
        regime_score = REGIME_FIT_SCORE.get(regime, 5.0)
        liquidity_score = liquidity_quality_score(order_book)
        lead_lag = (
            indicators.lead_lag_score(klines, reference_klines)
            if reference_klines and symbol != self._reference_symbol
            else 5.0
        )
        funding_score = funding_rate_score(funding, direction)
        price_moved_up = klines[-1].close > klines[0].close
        oi_score = oi_trend_score(oi_current, oi_previous, direction, price_moved_up)
        rl_context = {
            "regime": regime,
            "direction": direction.value,
            "trend_strength": round(trend_score, 2),
            "liquidity_quality": liquidity_score,
            "order_flow_bias": cvd_score,
            "auction_context": structure_strength,
            "volatility": volatility_score,
            "market_regime": regime_score,
            "lead_lag": lead_lag,
            "funding_rate": funding_score,
            "open_interest_trend": oi_score,
        }
        rl_score = await self._rl_scorer.score(symbol, rl_context)

        component_scores = {
            "trend_strength": round(trend_score, 2),
            "liquidity_quality": liquidity_score,
            "order_flow_bias": cvd_score,
            "auction_context": structure_strength,
            "volatility": volatility_score,
            "market_regime": regime_score,
            "lead_lag": lead_lag,
            "funding_rate": funding_score,
            "open_interest_trend": oi_score,
            "rl_confidence": round(rl_score, 2),
        }
        composite = sum(component_scores[k] * self._weights.get(k, 0.0) for k in component_scores) * 10

        rationale = [f"{k.replace('_', ' ')}={v:.1f}/10" for k, v in component_scores.items()]
        rationale.append(f"regime={regime}, structure={structure_direction}, direction={direction.value}")

        return ScanCandidate(
            symbol=symbol,
            direction=direction,
            composite_score=round(composite, 2),
            component_scores=component_scores,
            rationale=rationale,
            entry_price=klines[-1].close,
            atr=atr,
            regime=regime,
        )

    async def scan_all(self) -> List[ScanCandidate]:
        self._require_initialized()
        reference_klines = None
        if self._reference_symbol:
            try:
                reference_klines = await self._exchange.fetch_klines(
                    self._reference_symbol, self._timeframe, limit=self._kline_lookback
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("failed to fetch reference symbol klines: %s", exc)

        candidates: List[ScanCandidate] = []
        for symbol in self._symbols:
            try:
                candidate = await self.scan_symbol(symbol, reference_klines)
                if candidate is not None:
                    candidates.append(candidate)
            except Exception as exc:  # noqa: BLE001
                logger.error("scan failed for symbol", extra={"aitos_extra": {"symbol": symbol, "error": str(exc)}})

        self._last_scan_at = _utc_now_iso()
        self._last_candidate_count = len(candidates)
        await self._event_bus.publish(
            Event(
                topic=TOPIC_SCAN_COMPLETE,
                payload={"symbols_scanned": len(self._symbols), "candidates_found": len(candidates)},
                source_module=self.module_id,
            )
        )
        return candidates

    async def rank(self, candidates: List[ScanCandidate], top_n: Optional[int] = None) -> List[ScanCandidate]:
        effective_top_n = top_n if top_n is not None else self._top_n
        filtered = [c for c in candidates if c.composite_score >= self._min_score_threshold]
        return sorted(filtered, key=lambda c: c.composite_score, reverse=True)[:effective_top_n]

    def to_opportunity(
        self,
        candidate: ScanCandidate,
        risk_reward_multiples: Tuple[float, ...] = (1.0, 2.0, 3.0),
        atr_stop_multiplier: float = 1.5,
        strategy_id: str = "opportunity-scanner",
        is_production: bool = False,
        approved_by: Optional[str] = None,
    ) -> Opportunity:
        """Bridge a scored candidate into a Trade-Lifecycle-ready ``Opportunity``.

        Stop distance is ATR-based (never a fixed pip amount, per spec
        §30.2's Smart SL principle); take-profit levels are placed at
        1R/2R/3R by default, which lines up with the Trade Lifecycle's
        multi-level partial-close handling out of the box.
        """
        entry = candidate.entry_price
        stop_distance = candidate.atr * atr_stop_multiplier if candidate.atr > 0 else entry * 0.01

        if candidate.direction == TradeSide.LONG:
            stop_loss_price = entry - stop_distance
            take_profit_levels = [entry + stop_distance * r for r in risk_reward_multiples]
        else:
            stop_loss_price = entry + stop_distance
            take_profit_levels = [entry - stop_distance * r for r in risk_reward_multiples]

        return Opportunity(
            symbol=candidate.symbol,
            side=candidate.direction,
            entry_price=entry,
            stop_loss_price=stop_loss_price,
            take_profit_levels=take_profit_levels,
            confidence=round(candidate.composite_score / 100.0, 4),
            strategy_id=strategy_id,
            rationale="; ".join(candidate.rationale),
            agent_consensus=dict(candidate.component_scores),
            is_production=is_production,
            approved_by=approved_by,
            trailing_sl_enabled=True,
            regime=candidate.regime,
        )

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise ModuleNotInitializedError("OpportunityScanner.initialize() must be called first")
