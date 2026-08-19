"""Partitioned Parquet writer for canonical market events."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable, Any

import pyarrow as pa
import pyarrow.parquet as pq


class CanonicalParquetWriter:
    """Write canonical events into exchange/market/symbol/date partitions.

    Files are written atomically via a temporary sibling file. Existing
    partitions are appended by creating a new part file, avoiding accidental
    replacement of historical data.
    """

    def __init__(self, root: str | Path, compression: str = "zstd"):
        self.root = Path(root)
        self.compression = compression

    def write(self, events: Iterable[Any]) -> list[Path]:
        groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for event in events:
            ts: datetime = event.timestamp
            key = (event.exchange, event.market, event.symbol, ts.date().isoformat())
            row = dict(event.__dict__)
            row["timestamp"] = ts
            groups[key].append(row)

        written: list[Path] = []
        for (exchange, market, symbol, day), rows in groups.items():
            directory = self.root / f"exchange={exchange}" / f"market={market}" / f"symbol={symbol}" / f"date={day}"
            directory.mkdir(parents=True, exist_ok=True)
            index = len(list(directory.glob("part-*.parquet")))
            target = directory / f"part-{index:06d}.parquet"
            temp = directory / f".part-{index:06d}.tmp.parquet"
            table = pa.Table.from_pylist(rows)
            pq.write_table(table, temp, compression=self.compression)
            temp.replace(target)
            written.append(target)
        return written
