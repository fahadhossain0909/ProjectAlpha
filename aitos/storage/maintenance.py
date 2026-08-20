"""Bounded storage maintenance for ClickHouse and backtest download cache.

The policy intentionally protects decision/trade/risk/model data and only
retains evictable market-history datasets for a bounded window. Retention is
selected from 90/30/15/10/7 days when the configured ClickHouse budget is
under pressure. The controller is conservative: unknown tables are never
mutated automatically.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable

import clickhouse_connect

RETENTION_LADDER = (90, 30, 15, 10, 7)
DEFAULT_DB = "aitos"
DEFAULT_BUDGET_GB = 100
DEFAULT_TARGET_GB = 90
DEFAULT_CACHE_GB = 20

# Only these tables are eligible for automatic historical eviction. Add new
# high-volume market tables here deliberately; unknown tables are protected.
EVICTABLE_TABLES = {
    "order_book_snapshots": "time",
    "order_book_updates": "time",
    "market_ohlcv": "time",
}

# These are never automatically deleted by this controller.
PROTECTED_TABLE_TOKENS = (
    "trade", "order", "fill", "position", "decision", "risk", "model",
    "experience", "journal", "strategy", "execution", "portfolio",
)


@dataclass(frozen=True)
class StorageConfig:
    clickhouse_budget_gb: float = DEFAULT_BUDGET_GB
    clickhouse_target_gb: float = DEFAULT_TARGET_GB
    backtest_cache_gb: float = DEFAULT_CACHE_GB
    interval_seconds: int = 86400
    dry_run: bool = False

    @classmethod
    def from_env(cls) -> "StorageConfig":
        return cls(
            clickhouse_budget_gb=float(os.getenv("CLICKHOUSE_STORAGE_BUDGET_GB", DEFAULT_BUDGET_GB)),
            clickhouse_target_gb=float(os.getenv("CLICKHOUSE_STORAGE_TARGET_GB", DEFAULT_TARGET_GB)),
            backtest_cache_gb=float(os.getenv("BACKTEST_CACHE_MAX_GB", DEFAULT_CACHE_GB)),
            interval_seconds=int(os.getenv("STORAGE_MAINTENANCE_INTERVAL_SECONDS", 86400)),
            dry_run=os.getenv("STORAGE_MAINTENANCE_DRY_RUN", "false").lower() in {"1", "true", "yes"},
        )


def _gb(value: int | float) -> float:
    return float(value) / (1024 ** 3)


def _protected(table: str) -> bool:
    name = table.lower()
    return any(token in name for token in PROTECTED_TABLE_TOKENS)


def _candidate_days(current_gb: float, target_gb: float, estimated_daily_gb: float) -> int:
    if current_gb <= target_gb or estimated_daily_gb <= 0:
        return RETENTION_LADDER[0]
    for days in RETENTION_LADDER:
        if estimated_daily_gb * days <= target_gb:
            return days
    return RETENTION_LADDER[-1]


def _table_inventory(client, database: str) -> list[tuple[str, int, datetime | None, datetime | None]]:
    rows = client.query(
        """
        SELECT table, sum(bytes_on_disk) AS bytes,
               min(min_time) AS min_time, max(max_time) AS max_time
        FROM system.parts
        WHERE database = {db:String} AND active
        GROUP BY table
        ORDER BY bytes DESC
        """,
        parameters={"db": database},
    ).result_rows
    return [(str(r[0]), int(r[1] or 0), r[2], r[3]) for r in rows]


def choose_retention_days(current_gb: float, target_gb: float, evictable_daily_gb: float) -> int:
    return _candidate_days(current_gb, target_gb, evictable_daily_gb)


def enforce_clickhouse(client, config: StorageConfig, database: str = DEFAULT_DB) -> dict:
    inventory = _table_inventory(client, database)
    total_bytes = sum(row[1] for row in inventory)
    evictable = [row for row in inventory if row[0] in EVICTABLE_TABLES and not _protected(row[0])]
    evictable_bytes = sum(row[1] for row in evictable)

    if not evictable:
        return {"total_gb": _gb(total_bytes), "retention_days": 90, "evicted": [], "reason": "no configured evictable tables"}

    # Estimate recent daily footprint from each table's observed time span.
    daily_bytes = 0.0
    for table, size, min_time, max_time in evictable:
        if min_time and max_time:
            span_days = max(1.0, (max_time - min_time).total_seconds() / 86400.0)
            daily_bytes += size / span_days
    retention = choose_retention_days(_gb(total_bytes), config.clickhouse_target_gb, _gb(daily_bytes))

    evicted: list[str] = []
    if _gb(total_bytes) > config.clickhouse_target_gb:
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention)
        for table, _size, _min_time, _max_time in evictable:
            time_column = EVICTABLE_TABLES[table]
            sql = f"ALTER TABLE `{database}`.`{table}` DELETE WHERE {time_column} < {{cutoff:DateTime64(3)}}"
            if not config.dry_run:
                client.command(sql, parameters={"cutoff": cutoff})
            evicted.append(f"{table}<{cutoff.isoformat()}")

    return {
        "total_gb": _gb(total_bytes),
        "evictable_gb": _gb(evictable_bytes),
        "retention_days": retention,
        "evicted": evicted,
        "dry_run": config.dry_run,
    }


def _files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return ()
    return (p for p in root.rglob("*") if p.is_file())


def enforce_backtest_cache(root: Path, max_gb: float, dry_run: bool = False) -> dict:
    files = sorted(_files(root), key=lambda p: p.stat().st_mtime if p.exists() else 0)
    total = sum(p.stat().st_size for p in files if p.exists())
    removed: list[str] = []
    limit = max_gb * (1024 ** 3)
    while total > limit and files:
        path = files.pop(0)
        if not path.exists():
            continue
        size = path.stat().st_size
        if not dry_run:
            path.unlink()
        total -= size
        removed.append(str(path))
    return {"cache_gb": _gb(total), "max_gb": max_gb, "removed": removed, "dry_run": dry_run}


def run_once(config: StorageConfig) -> dict:
    host = os.getenv("CLICKHOUSE_HOST", "clickhouse")
    port = int(os.getenv("CLICKHOUSE_PORT", "8123"))
    user = os.getenv("CLICKHOUSE_USER", "default")
    password = os.getenv("CLICKHOUSE_PASSWORD", "")
    database = os.getenv("CLICKHOUSE_DB", DEFAULT_DB)
    cache_root = Path(os.getenv("BACKTEST_DATA_DIR", "/data/backtest"))

    client = clickhouse_connect.get_client(host=host, port=port, username=user, password=password, database=database)
    try:
        clickhouse_result = enforce_clickhouse(client, config, database)
    finally:
        client.close()
    cache_result = enforce_backtest_cache(cache_root, config.backtest_cache_gb, config.dry_run)
    return {"clickhouse": clickhouse_result, "backtest_cache": cache_result}


def main() -> None:
    config = StorageConfig.from_env()
    while True:
        try:
            print(run_once(config), flush=True)
        except Exception as exc:  # pragma: no cover - operational guard
            print(f"storage maintenance failed: {exc}", flush=True)
        time.sleep(config.interval_seconds)


if __name__ == "__main__":
    main()
