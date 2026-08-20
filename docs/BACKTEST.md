# ProjectAlpha VPS Backtesting

The backtest runner is an **opt-in Compose service**. It is separate from
`aitos-paper`, has no restart policy, reads historical data read-only, and is
resource-limited so a replay is less likely to starve a running paper bot.

## 1. Prepare historical data

Place a JSONL file or Parquet dataset under the VPS data directory. The
Compose service mounts `${BACKTEST_DATA_DIR:-./data}` as `/data:ro`.

For the CLI's generic deterministic engine, each event needs at least:

```json
{"timestamp":"2026-01-01T00:00:00Z","price":100.0}
```

Additional fields are preserved on the event object and can be consumed by a
custom strategy.

## 2. Build the image

From the repository directory:

```bash
docker compose build aitos-backtest
```

This does **not** start paper trading.

## 3. Run a backtest while paper trading is running

Paper trading does not need to be stopped for this isolated service.

```bash
docker compose --profile backtest run --rm aitos-backtest \
  --data /data/events.jsonl \
  --strategy aitos.backtest.cli:buy_and_hold \
  --initial-cash 10000 \
  --fee-rate 0.0004 \
  --slippage-bps 0
```

For a Parquet dataset:

```bash
docker compose --profile backtest run --rm aitos-backtest \
  --data /data/events.parquet \
  --format parquet \
  --strategy aitos.backtest.cli:buy_and_hold
```

The command prints JSON containing final equity, return, drawdown, Sharpe,
fees, trades, win rate, and profit factor.

## 4. Use a custom strategy

A strategy is a Python callable with this contract:

```python
def strategy(event, execution):
    # inspect event.price and any other event fields
    # use execution.execute("buy" or "sell", quantity, price)
    pass
```

Pass it as `module:function`, for example:

```bash
docker compose --profile backtest run --rm aitos-backtest \
  --data /data/events.jsonl \
  --strategy my_strategy:strategy
```

The module must be included in the image/repository or otherwise installed in
the container.

## 5. Paper-trading safety

The backtest service does not depend on Redis, ClickHouse, Neo4j, or
`aitos-paper`. It has no network ports and mounts historical data read-only.
It also has a 2 CPU / 3 GiB memory limit in Compose.

Therefore the normal workflow is:

```text
Paper trading:  RUNNING  ───────────────────────────────►
Backtest:                 START ───── RUN ───── END
```

Do not use `docker compose down -v` just to run a backtest; that command can
delete persistent Compose volumes.

## 6. Check status and logs

```bash
docker compose ps
docker compose logs -f aitos-paper
```

The backtest uses `--rm`, so its container is removed automatically after the
run. If a run fails, inspect the command output first; the paper container is
not restarted by the backtest service.

## Important limitation

The CLI currently drives the deterministic `BacktestEngine` using timestamp /
price events. ProjectAlpha also contains the richer L2/futures historical
runner (`ProjectAlphaHistoricalRunner`). Wiring that runner into the CLI is a
separate step and should be done once the exact historical dataset schema and
strategy-decision interface are finalized.
