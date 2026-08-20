# AITOS storage and retention policy

## Goals

- ClickHouse is the long-term source of truth for data that the trading system can regenerate from its own live/paper history.
- Backtest downloads are a bounded cache, not an ever-growing archive.
- Trade/decision/risk/model/experience data is protected from automatic deletion.
- High-volume market-history data such as L2/order-book data is evictable and can be re-downloaded when absent.
- The default VPS ClickHouse budget is **100 GiB**, with a **90 GiB target** so cleanup has headroom.
- The backtest download cache is capped at **20 GiB** by default.

## Retention ladder

The storage controller selects from:

`90 days → 30 days → 15 days → 10 days → 7 days`

It only shortens retention when the configured ClickHouse target is exceeded. Exact achievable days cannot be known before the VPS has real data because L2 volume depends heavily on exchange, symbols, snapshot depth and update frequency. The controller estimates the observed daily footprint and chooses the longest ladder value that fits the target budget.

## Protected vs evictable data

Protected by the controller:

- trades / trade ticks
- orders and fills
- positions / portfolio state
- decisions and journals
- risk records
- model versions / model outputs
- experience/replay data
- strategy/execution records

Currently eligible for automatic historical eviction:

- `order_book_snapshots`
- `order_book_updates` (when present)
- `market_ohlcv`

Unknown tables are **not** deleted automatically. New evictable tables must be deliberately added to `aitos/storage/maintenance.py` with their event-time column.

## ClickHouse sizing

The number of days that 100 GiB can hold is measured from the real VPS data, not guessed from a generic benchmark. After the stack has accumulated data, inspect the maintenance logs to see `total_gb`, `evictable_gb` and the selected `retention_days`.

This distinction matters: **GiB here means disk/storage capacity, not RAM.** ClickHouse also uses RAM for queries and merges, but the 100 GiB budget is for its persistent volume.

## Backtest download cache

The cache is mounted at `/data/backtest` and capped at 20 GiB. When it exceeds the cap, the oldest files are removed first. The downloader's manifest remains outside the eviction loop only when it is stored outside the cache; if the manifest is inside the cache, it should be treated as metadata and kept in a small dedicated directory in a later hardening pass.

When a required historical partition is absent locally, the downloader may fetch it again. This is intentional: the local directory is a cache, not the authoritative archive.

## ClickHouse-first rule

The long-term target architecture is:

1. Query ClickHouse for the requested historical partition.
2. If the required data is present and complete, backtest directly from ClickHouse.
3. Only if it is absent, download the missing partition from the external source.
4. Ingest the downloaded data into ClickHouse so the same request will not need another download later.
5. The local downloaded file is retained only until the cache policy evicts it.

This prevents duplicate long-term storage of data that already lives in ClickHouse.

## Important current-repository limitation

The repository already has a ClickHouse `MarketDataRepository` with `market_ohlcv`, `order_book_snapshots`, `trade_ticks`, `funding_rates` and `open_interest`, while the historical downloader currently writes canonical Parquet partitions. The final ClickHouse-first backtest resolver therefore must be connected to the actual historical ingestion path before it can truthfully claim that every downloaded dataset is automatically available from ClickHouse. This policy file deliberately does not pretend that connection already exists.

## Manual dry run

To inspect what would be removed without deleting anything, set:

```text
STORAGE_MAINTENANCE_DRY_RUN=true
```

The production Compose service uses `false` and runs once per day.
