"""Command-line entry point for deterministic ProjectAlpha backtests."""
from __future__ import annotations
import argparse
import json
from pathlib import Path


def _load_events(path: Path):
    from aitos.models.market import TradeTick, OrderBookSnapshot
    from datetime import datetime
    events = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        ts = datetime.fromisoformat(row["timestamp"])
        if row["type"] == "trade":
            events.append(TradeTick(symbol=row["symbol"], price=float(row["price"]), quantity=float(row["quantity"]), timestamp=ts, is_buyer_maker=bool(row.get("is_buyer_maker", False))))
        elif row["type"] == "book":
            events.append(OrderBookSnapshot(symbol=row["symbol"], timestamp=ts, bids=row.get("bids", []), asks=row.get("asks", [])))
        else:
            raise ValueError(f"unsupported event type: {row['type']}")
    return events


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a ProjectAlpha historical replay")
    parser.add_argument("dataset", type=Path, help="JSONL market-event dataset")
    parser.add_argument("--cash", type=float, default=10_000.0)
    parser.add_argument("--leverage", type=float, default=1.0)
    parser.add_argument("--fee-rate", type=float, default=0.0004)
    parser.add_argument("--slippage-bps", type=float, default=0.0)
    args = parser.parse_args()
    events = _load_events(args.dataset)
    print(json.dumps({"events": len(events), "initial_cash": args.cash, "leverage": args.leverage, "fee_rate": args.fee_rate, "slippage_bps": args.slippage_bps}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
