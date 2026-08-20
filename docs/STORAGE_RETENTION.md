# AITOS storage and retention policy

## Goals

- ClickHouse is the primary historical source for market data retained from paper/live operation.
- Backtest downloads are a bounded, re-downloadable cache, not an ever-growing archive.
- Trade/decision/risk/model/experience data is protected from automatic deletion.
- High-volume market history such as L2/order-book data is evictable and can be downloaded again when absent.
- The default ClickHouse storage budget is **100 GiB**, with a **90 GiB cleanup target**.
- The default backtest download cache is capped at **20 GiB**.

## Retention ladder

The controller selects from:

`90 days → 30 days → 15 days → 10 days → 7 days`

It shortens retention only when the configured ClickHouse target is exceeded. Exact achievable days depend on the real VPS footprint: L2 volume varies with exchange, symbols, depth and update frequency. The controller estimates the observed daily footprint and chooses the longest configured window that fits the available evictable budget.

## Protected vs evictable data

The controller never automatically deletes protected trading/learning records such as:

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

Unknown tables are **not** deleted automatically. A new evictable table must be deliberately added to `aitos/storage/maintenance.py` with its event-time column.

## ClickHouse sizing

The number of days that 100 GiB can hold is measured from real VPS data, not guessed from a generic benchmark. The maintenance log reports `total_gb`, `protected_gb`, `evictable_gb` and the selected `retention_days`.

The 100 GiB figure is a **persistent disk/storage budget, not RAM**. VPS sizing must separately account for RAM used by ClickHouse queries/merges, Redis, Neo4j, trading processes, Docker and OS headroom.

## Backtest download cache

The cache is mounted at `/data/backtest` and capped at 20 GiB by default. When it exceeds the cap, the oldest eligible files are removed first. It is disposable: a missing historical partition can be downloaded again later.

Downloaded Parquet files are consumed **directly by the backtest engine**. They are **not ingested into ClickHouse** and are not treated as a second long-term database.

## ClickHouse-first backtest rule

The intended data-source order is:

1. Query ClickHouse for the requested historical range.
2. If the required data is present and complete, backtest directly from ClickHouse.
3. Only if it is absent, download the missing history externally as Parquet.
4. Run the backtest directly from that Parquet dataset.
5. **Do not ingest the downloaded Parquet into ClickHouse.**

```text
Backtest request
      |
      v
ClickHouse has requested data?
      | yes                     | no
      v                         v
Read ClickHouse          Download Parquet
      |                         |
      |                         v
      |                   Backtest from Parquet
      |                         |
      +-----------+-------------+
                  v
             Backtest result
```

## Current implementation boundary

This PR implements the bounded storage controller and the storage policy. The actual ClickHouse-first backtest resolver is a separate data-layer integration: the backtest command must query ClickHouse first and use the existing Parquet downloader/cache only for missing history. This policy intentionally does not claim that that resolver is already wired if it is not.

## Dry run

For a non-destructive maintenance run, set:

```text
STORAGE_MAINTENANCE_DRY_RUN=true
```

The production Compose service uses `false` and runs once per day.
