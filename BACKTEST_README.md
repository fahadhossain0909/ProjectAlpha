# VPS Paper Trading & Backtesting Operations Guide

This guide explains how to safely stop the ProjectAlpha paper-trading process on the VPS, run a historical backtest, and start paper trading again.

## 1. Important: paper trading and backtesting are separate modes

ProjectAlpha currently starts the paper-trading application as the `aitos-paper` Docker Compose service. The Compose definition uses `restart: unless-stopped`, so stopping the service with `docker compose stop aitos-paper` is the safest temporary pause: the container is stopped but its data volumes are preserved.

The repository already contains the historical backtest engine under `aitos/backtest/`, including the end-to-end `ProjectAlphaHistoricalRunner`. The current runner is a Python library/API rather than a dedicated `backtest` command-line service. Do not invent a CLI command that is not present in the repository; use the project's eventual backtest entry point once the CLI wrapper is added.

## 2. Connect to the VPS

SSH into the production VPS and move to the ProjectAlpha deployment directory, for example:

```bash
cd /opt/aitos
```

If your checkout is elsewhere, use that directory instead.

## 3. Check whether paper trading is running

```bash
docker compose ps
```

You should see `aitos-paper` with a running state when paper trading is active.

You can also check the health endpoint:

```bash
curl http://localhost:8090/health
```

And watch the paper-trading log:

```bash
docker compose logs -f aitos-paper
```

Press `Ctrl+C` to leave the log view; this does **not** stop the application.

## 4. Safely stop paper trading before a backtest

Use:

```bash
docker compose stop aitos-paper
```

Then verify:

```bash
docker compose ps
```

The `aitos-paper` service should no longer be running.

### Do not use this for a normal temporary pause

```bash
docker compose down -v
```

`down -v` deletes the Compose volumes and can permanently remove Redis/ClickHouse/Neo4j data. It is a cleanup/destructive operation, not a routine way to pause paper trading.

`docker compose down` without `-v` also stops the stack, but it is broader than necessary because it stops the supporting services too. Prefer `docker compose stop aitos-paper` when the goal is only to pause paper trading.

## 5. Run the backtest

The repository's historical engine is implemented in `aitos/backtest/projectalpha_runner.py` as `ProjectAlphaHistoricalRunner`. It consumes historical `TradeTick`/`OrderBookSnapshot` events and returns metrics such as final equity, return, fees, funding, liquidation state, fills, and passive-order statistics.

At the moment, the repository does **not** expose a verified top-level `backtest` CLI command. Therefore, do not run a guessed command such as:

```bash
python3 backtest.py
```

unless that entry point has been added and verified.

Once the dedicated backtest CLI wrapper is present, the recommended operational pattern is:

```bash
python3 <verified-backtest-entrypoint> <options>
```

Run it from the ProjectAlpha directory so that the same code, configuration, and historical-data paths are used by the deployment.

## 6. Verify that the backtest completed

After the command finishes, check its exit status:

```bash
echo $?
```

`0` means the process exited successfully.

Also verify that paper trading is still stopped while you inspect the results:

```bash
docker compose ps
```

## 7. Start paper trading again

When the backtest is complete and you are ready to resume paper trading:

```bash
docker compose start aitos-paper
```

Then verify:

```bash
docker compose ps
curl http://localhost:8090/health
```

Follow the logs if needed:

```bash
docker compose logs -f aitos-paper
```

## 8. If the container was removed/rebuilt

If the container does not exist because the deployment was recreated, use the normal deployment command instead:

```bash
docker compose up -d --build
```

This can also restart the paper-trading service. Be careful with this command while a backtest is running because it can bring `aitos-paper` back online.

## 9. Recommended daily workflow

```text
Paper trading running
        |
        v
Check status
        |
        v
Stop only aitos-paper
        |
        v
Run historical backtest
        |
        v
Inspect results
        |
        v
Start aitos-paper again
        |
        v
Verify health + logs
        |
        v
Paper trading running again
```

## 10. CD workflow warning

The GitHub-to-VPS CD workflow may run deployment commands after a new commit. If the deployment step executes `docker compose up -d --build`, it can start `aitos-paper` again even if you previously stopped it manually.

Therefore, when doing a backtest on a production VPS:

1. Stop `aitos-paper`.
2. Do not trigger a deployment during the backtest unless you intentionally want the service restarted.
3. Run the backtest.
4. Start `aitos-paper` again.
5. Verify health and logs.

## 11. Safety rules

- Never use `docker compose down -v` just to pause paper trading.
- Never run the live-trading profile while intending to paper trade.
- Confirm `aitos-paper` is stopped before running a backtest that could compete for CPU/RAM/ClickHouse resources.
- Confirm `aitos-paper` is healthy after restarting it.
- Keep `.env` and exchange credentials out of Git.
- Before a future backtest CLI is introduced, verify its data source and output path so historical data and production data are not accidentally overwritten.

## Quick reference

### Stop paper trading

```bash
docker compose stop aitos-paper
```

### Check status

```bash
docker compose ps
```

### Start paper trading

```bash
docker compose start aitos-paper
```

### Follow paper-trading logs

```bash
docker compose logs -f aitos-paper
```

### Health check

```bash
curl http://localhost:8090/health
```

### Full stack restart/rebuild — use deliberately

```bash
docker compose up -d --build
```
