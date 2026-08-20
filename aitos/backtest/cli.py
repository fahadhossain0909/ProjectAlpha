"""CLI for isolated deterministic ProjectAlpha historical backtests."""
from __future__ import annotations

import argparse
import importlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from .engine import BacktestEngine


@dataclass(frozen=True)
class HistoricalEvent:
    timestamp: datetime
    price: float
    fields: dict[str, Any]

    def __getattr__(self, name: str) -> Any:
        try:
            return self.fields[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        seconds = float(value) / 1000.0 if abs(float(value)) > 10_000_000_000 else float(value)
        dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _event(row: dict[str, Any]) -> HistoricalEvent:
    if "timestamp" not in row or "price" not in row:
        raise ValueError("Each historical row must contain 'timestamp' and 'price'")
    return HistoricalEvent(_timestamp(row["timestamp"]), float(row["price"]), row)


def read_events(path: str | Path, fmt: str = "auto") -> Iterator[HistoricalEvent]:
    """Stream JSONL or Parquet events without loading the full dataset."""
    source = Path(path)
    if fmt == "auto":
        fmt = "parquet" if source.is_dir() or source.suffix.lower() in {".parquet", ".pq"} else "jsonl"

    if fmt == "jsonl":
        with source.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    yield _event(json.loads(line))
                except Exception as exc:
                    raise ValueError(f"Invalid JSONL row at line {line_no}: {exc}") from exc
        return

    if fmt == "parquet":
        try:
            import pyarrow.dataset as ds
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Parquet input requires pyarrow") from exc
        dataset = ds.dataset(str(source), format="parquet", partitioning="hive")
        for batch in dataset.scanner(batch_size=50_000).to_batches():
            for row in batch.to_pylist():
                yield _event(row)
        return

    raise ValueError(f"Unsupported input format: {fmt}")


def load_strategy(spec: str) -> Callable[[Any, Any], None]:
    module_name, separator, attr = spec.partition(":")
    if not separator:
        module_name, separator, attr = spec.rpartition(".")
    if not module_name or not attr:
        raise ValueError("Strategy must be specified as module:function")
    strategy = getattr(importlib.import_module(module_name), attr)
    if not callable(strategy):
        raise TypeError(f"Strategy is not callable: {spec}")
    return strategy


def buy_and_hold(event: Any, execution: Any) -> None:
    """Reference strategy: buy one unit on the first event and then hold."""
    if not getattr(execution, "_cli_bought", False):
        execution.execute("buy", 1.0, float(event.price))
        execution._cli_bought = True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an isolated ProjectAlpha historical backtest")
    parser.add_argument("--data", required=True, help="JSONL file or Parquet file/dataset")
    parser.add_argument("--format", choices=("auto", "jsonl", "parquet"), default="auto")
    parser.add_argument("--strategy", default="aitos.backtest.cli:buy_and_hold", help="Strategy callable: module:function")
    parser.add_argument("--initial-cash", type=float, default=10_000.0)
    parser.add_argument("--fee-rate", type=float, default=0.0004)
    parser.add_argument("--slippage-bps", type=float, default=0.0)
    parser.add_argument("--symbol", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    strategy = load_strategy(args.strategy)
    result = BacktestEngine(
        initial_cash=args.initial_cash,
        fee_rate=args.fee_rate,
        slippage_bps=args.slippage_bps,
    ).run(read_events(args.data, args.format), strategy, lambda event: float(event.price))
    metrics = result.metrics
    print(json.dumps({
        "symbol": args.symbol,
        "strategy": args.strategy,
        "events": len(result.equity_curve),
        "initial_equity": metrics.initial_equity,
        "final_equity": metrics.final_equity,
        "total_return": metrics.total_return,
        "max_drawdown": metrics.max_drawdown,
        "sharpe": metrics.sharpe,
        "total_fees": metrics.total_fees,
        "trades": metrics.trades,
        "wins": metrics.wins,
        "losses": metrics.losses,
        "win_rate": metrics.win_rate,
        "profit_factor": metrics.profit_factor,
    }, indent=2, allow_nan=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
