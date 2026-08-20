"""Durable continual-learning worker for paper/live/backtest experiences.

The worker is intentionally conservative: it learns continuously from closed
experiences, persists its model state, and never promotes a strategy or model
into production. Candidate evolution remains behind the canonical validation
and governance path.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import clickhouse_connect

from aitos.intelligence.deep_rl_policy import DeepValueRLScorer


class ContinualLearningWorker:
    """Poll ClickHouse and incrementally train the online value scorer.

    Decision records provide the state/features; outcome records provide the
    reward. The pair is joined by ``metadata_json.decision_id``. Processed
    outcome IDs are persisted so a restart does not retrain the same experience.
    """

    def __init__(
        self,
        *,
        host: str = "localhost",
        port: int = 8123,
        user: str = "default",
        password: str = "",
        database: str = "aitos",
        state_path: str = "models/online_rl/worker_state.json",
        model_path: str = "models/online_rl/deep_value.pkl",
        lookback_hours: int = 168,
        batch_limit: int = 5000,
        poll_seconds: int = 60,
    ) -> None:
        self.client = clickhouse_connect.get_client(
            host=host, port=port, username=user, password=password, database=database
        )
        self.database = database
        self.state_path = Path(state_path)
        self.batch_limit = batch_limit
        self.poll_seconds = poll_seconds
        self.lookback = timedelta(hours=lookback_hours)
        self.scorer = DeepValueRLScorer(state_path=model_path)
        self.scorer.load_state(model_path)
        self._processed: set[str] = set()
        self._load_state()

    def close(self) -> None:
        self.client.close()

    def _load_state(self) -> None:
        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            self._processed = set(str(x) for x in data.get("processed_outcomes", []))
        except (OSError, ValueError, TypeError):
            self._processed = set()

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "processed_outcomes": sorted(self._processed)[-10000:],
            "n_samples_seen": self.scorer.n_samples_seen,
        }
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    def _rows(self) -> list[dict[str, Any]]:
        start = datetime.now(timezone.utc) - self.lookback
        sql = f"""
        SELECT experience_id, timestamp, source, symbol, decision, outcome,
               reward, features_json, metadata_json
        FROM {self.database}.learning_experiences
        WHERE timestamp >= {{start:DateTime64(3)}}
        ORDER BY timestamp ASC
        LIMIT {{limit:UInt32}}
        """
        result = self.client.query(sql, parameters={"start": start, "limit": self.batch_limit})
        return [dict(zip(result.column_names, row)) for row in result.result_rows]

    @staticmethod
    def _json_object(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        try:
            parsed = json.loads(value or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}

    @staticmethod
    def _numeric_features(features: dict[str, Any]) -> dict[str, float]:
        result: dict[str, float] = {}
        for key, value in features.items():
            if isinstance(value, bool):
                result[key] = float(value)
            elif isinstance(value, (int, float)):
                result[key] = float(value)
        return result

    def run_once(self) -> int:
        rows = self._rows()
        decisions: dict[str, dict[str, Any]] = {}
        for row in rows:
            metadata = self._json_object(row["metadata_json"])
            decision_id = str(metadata.get("decision_id", ""))
            if row["outcome"] is None:
                if decision_id:
                    decisions[decision_id] = row
                continue
            outcome_id = str(row["experience_id"])
            if outcome_id in self._processed or not decision_id:
                continue
            decision = decisions.get(decision_id)
            if decision is None:
                continue
            features = self._numeric_features(self._json_object(decision["features_json"]))
            if not features:
                continue
            self.scorer.update(str(row["symbol"]), features, float(row["reward"] or 0.0))
            self._processed.add(outcome_id)

        if rows:
            self.scorer.save_state()
            self._save_state()
        return len(self._processed)

    def run_forever(self) -> None:
        try:
            while True:
                self.run_once()
                time.sleep(self.poll_seconds)
        finally:
            self.close()
