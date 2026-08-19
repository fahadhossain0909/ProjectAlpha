"""Command-line entry point for deterministic ProjectAlpha backtests."""
from __future__ import annotations
import argparse
import json
from datetime import datetime
from pathlib import Path


def _load_events(path: Path):
    from aitos.models.market import TradeTick, OrderBookSnapshot, TradeSide

    events = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        ts = datetime.fromisoformat(row["timestamp"])
        if row["type"] == "trade":
            side = TradeSide(row["side"].lower())
            events.append(
                TradeTick(
                    trade_id=str(row["trade_id"]),
                    symbol=row["symbol"],
                    price=float(row["price"]),
                    quantity=float(row["quantity"]),
                    side=side,
                    timestamp=ts,
                    is_buyer_maker=bool(row.get("is_buyer_maker", False)),
                )
            )
        elif row["type"] == "book":
            events.append(
                OrderBookSnapshot(
                    symbol=row["symbol"],
                    bids=row.get("bids", []),
                    asks=row.get("asks", []),
                    last_update_id=int(row["last_update_id"]),
                    timestamp=ts,
                )
            )
        else:
            raise ValueError(f"unsupported event type: {row['type']}")
    return events


def main() -> int:
    parser = argparse.ArgumentParser(description="Load and validate a ProjectAlpha historical replay dataset")
    parser.add_argument("dataset", type=Path, help="JSONL market-event dataset")
    parser.add_argument("--cash", type=float, default=10_000.0)
    parser.add_argument("--leverage", type=float, default=1.0)
    parser.add_argument("--fee-rate", type=float, default=0.0004)
    parser.add_argument("--slippage-bps", type=float, default=0.0)
    args = parser.parse_args()
    events = _load_events(args.dataset)
    symbols = sorted({event.symbol for event in events})
    print(json.dumps({"events": len(events), "symbols": symbols, "initial_cash": args.cash, "leverage": args.leverage, "fee_rate": args.fee_rate, "slippage_bps": args.slippage_bps}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
