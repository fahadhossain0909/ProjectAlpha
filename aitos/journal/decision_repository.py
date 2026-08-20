"""Persistent decision journal and outcome attribution store.

The store is deliberately append-only: a decision snapshot is immutable and
later linkage/outcome records are appended under the same decision_id. This
keeps the audit trail intact while remaining friendly to ClickHouse/MergeTree.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import clickhouse_connect

from aitos.core.contracts import AITOSModule, Event, EventResponse, HealthStatus, ModuleStatus
from aitos.core.exceptions import ModuleNotInitializedError
from aitos.logging_setup import get_logger

logger = get_logger("aitos.journal.decision_repository")

CREATE_DECISION_JOURNAL = """
CREATE TABLE IF NOT EXISTS decision_journal (
    recorded_at DateTime64(3, 'UTC'),
    decision_id String,
    record_type String,
    trade_id Nullable(String),
    symbol String,
    side String,
    strategy_id String,
    regime String,
    confidence Nullable(Float64),
    payload String,
    pnl Nullable(Float64),
    pnl_percent Nullable(Float64),
    risk_amount_usd Nullable(Float64),
    r_multiple Nullable(Float64),
    holding_seconds Nullable(Float64),
    exit_reason Nullable(String)
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(recorded_at)
ORDER BY (decision_id, recorded_at)
"""


class DecisionJournalRepository(AITOSModule):
    """Durable append-only store for decision snapshots and trade outcomes."""

    def __init__(self, host: str = "localhost", port: int = 8123, username: str = "default", password: str = "", database: str = "aitos") -> None:
        self._conn_params = dict(host=host, port=port, username=username, password=password, database=database)
        self._client = None
        self._initialized = False
        self._last_event_time: Optional[str] = None

    @property
    def module_id(self) -> str:
        return "decision-journal-repository"

    @property
    def version(self) -> str:
        return "1.0.0"

    async def initialize(self, config: Dict[str, Any]) -> None:
        if self._initialized:
            return
        self._client = await clickhouse_connect.get_async_client(**self._conn_params)
        await self._client.command(CREATE_DECISION_JOURNAL)
        self._initialized = True
        logger.info("DecisionJournalRepository initialized")

    async def health_check(self) -> HealthStatus:
        start = time.monotonic()
        try:
            await self._client.command("SELECT 1")
            status = ModuleStatus.HEALTHY
        except Exception as exc:  # noqa: BLE001
            status = ModuleStatus.UNHEALTHY
            logger.error("decision journal health check failed: %s", exc)
        return HealthStatus(module_id=self.module_id, status=status,
            latency_ms=(time.monotonic() - start) * 1000, last_event_time=self._last_event_time, details={})

    async def shutdown(self, grace_period_seconds: float = 30.0) -> None:
        if self._client is not None:
            await self._client.close()
        logger.info("DecisionJournalRepository shut down")

    async def emit_events(self):
        return
        yield  # pragma: no cover

    async def handle_event(self, event: Event) -> Optional[EventResponse]:
        return None

    async def save_decision(self, decision_id: str, snapshot: Dict[str, Any]) -> None:
        self._require_initialized()
        await self._insert(record_type="DECISION", decision_id=decision_id, trade_id=None,
            symbol=str(snapshot.get("symbol", "")), side=str(snapshot.get("side", "")),
            strategy_id=str(snapshot.get("strategy_id", "")), regime=str(snapshot.get("regime", "unknown")),
            confidence=snapshot.get("confidence"), payload=snapshot)

    async def link_trade(self, decision_id: str, trade: Dict[str, Any]) -> None:
        self._require_initialized()
        await self._insert(record_type="TRADE_LINK", decision_id=decision_id, trade_id=trade.get("trade_id"),
            symbol=str(trade.get("symbol", "")), side=str(trade.get("side", "")),
            strategy_id=str(trade.get("strategy_id", "")), regime=str(trade.get("regime", "unknown")),
            confidence=None, payload=trade)

    async def attribute_outcome(self, decision_id: str, trade: Dict[str, Any]) -> None:
        self._require_initialized()
        pnl = trade.get("pnl"); risk = trade.get("risk_amount_usd")
        r_multiple = (float(pnl) / float(risk)) if pnl is not None and risk not in (None, 0, 0.0) else None
        holding_seconds = self._holding_seconds(trade.get("entry_time"), trade.get("exit_time"))
        await self._insert(record_type="OUTCOME", decision_id=decision_id, trade_id=trade.get("trade_id"),
            symbol=str(trade.get("symbol", "")), side=str(trade.get("side", "")),
            strategy_id=str(trade.get("strategy_id", "")), regime=str(trade.get("regime", "unknown")),
            confidence=None, payload=trade, pnl=pnl, pnl_percent=trade.get("pnl_percent"),
            risk_amount_usd=risk, r_multiple=r_multiple, holding_seconds=holding_seconds,
            exit_reason=trade.get("exit_reason"))

    async def get_records(self, decision_id: str) -> List[Dict[str, Any]]:
        self._require_initialized()
        result = await self._client.query(
            "SELECT * FROM decision_journal WHERE decision_id = {decision_id:String} ORDER BY recorded_at",
            parameters={"decision_id": decision_id},
        )
        return [dict(zip(result.column_names, row)) for row in result.result_rows]

    async def _insert(self, *, record_type: str, decision_id: str, trade_id: Optional[str], symbol: str, side: str,
                      strategy_id: str, regime: str, confidence: Optional[float], payload: Dict[str, Any],
                      pnl: Optional[float] = None, pnl_percent: Optional[float] = None,
                      risk_amount_usd: Optional[float] = None, r_multiple: Optional[float] = None,
                      holding_seconds: Optional[float] = None, exit_reason: Optional[str] = None) -> None:
        recorded_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await self._client.insert(
            "decision_journal",
            [[recorded_at, decision_id, record_type, trade_id, symbol, side, strategy_id, regime, confidence,
              json.dumps(payload, default=str), pnl, pnl_percent, risk_amount_usd, r_multiple, holding_seconds, exit_reason]],
            column_names=["recorded_at", "decision_id", "record_type", "trade_id", "symbol", "side", "strategy_id", "regime",
                          "confidence", "payload", "pnl", "pnl_percent", "risk_amount_usd", "r_multiple", "holding_seconds", "exit_reason"],
        )
        self._last_event_time = recorded_at.isoformat()

    @staticmethod
    def _holding_seconds(entry_time: Optional[str], exit_time: Optional[str]) -> Optional[float]:
        if not entry_time or not exit_time:
            return None
        try:
            return max(0.0, (datetime.fromisoformat(exit_time) - datetime.fromisoformat(entry_time)).total_seconds())
        except (TypeError, ValueError):
            return None

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise ModuleNotInitializedError("DecisionJournalRepository.initialize() must be called first")
