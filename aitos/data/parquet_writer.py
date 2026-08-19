"""Partitioned Parquet writer with manifest-backed deduplication."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable, Any
import hashlib
import json

import pyarrow as pa
import pyarrow.parquet as pq

from .parquet_manifest import ParquetManifest, PartitionRecord


class CanonicalParquetWriter:
    def __init__(self, root: str | Path, compression: str = "zstd", manifest: str | Path | None = None):
        self.root = Path(root)
        self.compression = compression
        self.manifest = ParquetManifest(manifest or self.root / "_manifest.json")

    @staticmethod
    def _fingerprint(rows: list[dict[str, Any]]) -> str:
        payload = json.dumps(rows, default=str, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(payload).hexdigest()

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
            partition_key = f"{exchange}/{market}/{symbol}/{day}"
            fingerprint = self._fingerprint(rows)
            if self.manifest.contains(partition_key, fingerprint):
                continue

            directory = self.root / f"exchange={exchange}" / f"market={market}" / f"symbol={symbol}" / f"date={day}"
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / "part-000000.parquet"
            temp = directory / ".part-000000.tmp.parquet"
            table = pa.Table.from_pylist(rows)
            pq.write_table(table, temp, compression=self.compression)
            temp.replace(target)
            self.manifest.record(PartitionRecord(partition_key, str(target), len(rows), fingerprint))
            written.append(target)
        return written
