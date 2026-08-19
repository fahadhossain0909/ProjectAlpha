"""Incremental download and canonical-Parquet-aware local-file validation."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import hashlib
import json
import tempfile
import urllib.request


@dataclass(frozen=True)
class FileRecord:
    key: str
    url: str
    path: str
    size: int
    sha256: str


class DownloadManifest:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.records: dict[str, dict] = {}
        if self.path.exists():
            self.records = json.loads(self.path.read_text(encoding="utf-8"))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.records, indent=2, sort_keys=True), encoding="utf-8")

    def valid(self, key: str, path: Path, expected_size: int | None = None, sha256: str | None = None) -> bool:
        rec = self.records.get(key)
        if not rec or not path.exists():
            return False
        if expected_size is not None and path.stat().st_size != expected_size:
            return False
        if sha256 is not None and rec.get("sha256") != sha256:
            return False
        return rec.get("size") == path.stat().st_size


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


class CanonicalDataIndex:
    """Answers whether a requested dataset partition already has Parquet.

    A valid canonical partition is the first download gate: if it exists,
    the raw archive must not be downloaded even when the raw file/manifest is
    absent. This keeps disk usage and network traffic bounded.
    """

    def __init__(self, parquet_root: str | Path):
        self.root = Path(parquet_root)

    def partition_path(self, key: str) -> Path:
        parts = key.strip("/").split("/")
        if len(parts) != 4:
            raise ValueError("partition key must be exchange/market/symbol/YYYY-MM-DD")
        exchange, market, symbol, day = parts
        return (
            self.root
            / f"exchange={exchange}"
            / f"market={market}"
            / f"symbol={symbol}"
            / f"date={day}"
        )

    def has_partition(self, key: str) -> bool:
        directory = self.partition_path(key)
        return any(directory.glob("part-*.parquet"))


class IncrementalDownloader:
    """Download only data absent from both canonical Parquet and raw storage."""

    def __init__(self, manifest: DownloadManifest, parquet_index: CanonicalDataIndex | None = None):
        self.manifest = manifest
        self.parquet_index = parquet_index

    def download(
        self,
        items: Iterable[tuple[str, str, Path]],
        overwrite: bool = False,
    ) -> list[Path]:
        downloaded: list[Path] = []
        for key, url, destination in items:
            # Canonical Parquet takes precedence over the raw download manifest.
            if not overwrite and self.parquet_index and self.parquet_index.has_partition(key):
                continue
            if not overwrite and self.manifest.valid(key, destination):
                continue

            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as tmp:
                tmp_path = Path(tmp.name)
            try:
                urllib.request.urlretrieve(url, tmp_path)
                digest = sha256_file(tmp_path)
                size = tmp_path.stat().st_size
                tmp_path.replace(destination)
                self.manifest.records[key] = {
                    "url": url,
                    "path": str(destination),
                    "size": size,
                    "sha256": digest,
                }
                self.manifest.save()
                downloaded.append(destination)
            finally:
                if tmp_path.exists():
                    tmp_path.unlink()
        return downloaded
