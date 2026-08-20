# ProjectAlpha Continual Learning Architecture

ProjectAlpha uses **execution isolation with shared learning**. Backtest, paper, and live execution are separate operational stages, but their durable experiences feed the same learning/evaluation lifecycle.

## Data flow

```text
Historical / Paper / Live
          |
          v
      ClickHouse
          |
          +--> Backtest / Replay
          |
          +--> Experience Store
                    |
                    +--> RL feedback
                    +--> Evolution proposals
                    +--> Evaluation
                    +--> Model registry
```

ClickHouse is the long-lived source of market history and learning evidence. Redis remains the real-time event/cache layer; Neo4j remains the relationship/knowledge-graph layer.

## Experience records

`learning_experiences` is append-only. A paper/live decision is recorded when `journal.decision_recorded` is emitted, and the later outcome is recorded when `journal.outcome_attributed` is emitted. The records retain source, symbol, decision, confidence, features, market state, risk state, strategy/model versions, and identifiers linking the evidence.

Backtests can also persist a completed-run summary with `--persist-learning`.

## Historical data for backtests

The canonical backtest CLI supports both files and ClickHouse:

```bash
# Local JSONL/Parquet
python3 -m aitos.backtest.cli --source file --data /data/events.jsonl

# Persisted ClickHouse OHLCV
python3 -m aitos.backtest.cli \
  --source clickhouse \
  --symbol BTCUSDT \
  --table ohlcv \
  --timeframe 15m \
  --start 2026-01-01T00:00:00+00:00 \
  --end 2026-06-01T00:00:00+00:00 \
  --persist-learning
```

`trades` and `orderbook` are also supported. The source only reads historical rows; it never mutates production trading state.

## Evolution and validation

Evolution models **propose** changes; they do not deploy them. A candidate must be evaluated with the same canonical `BacktestEngine` used by the rest of the project. The validation layer checks return, drawdown, Sharpe, trade count, and improvement against the current champion.

For leakage-resistant evaluation, `aitos.learning.walk_forward.WalkForwardValidator` provides sequential unseen test windows plus an optional locked holdout. The holdout must not be used to tune a candidate.

```text
Experience
   -> Evolution proposal
   -> Candidate strategy/model
   -> Canonical BacktestEngine
   -> Walk-forward / locked holdout
   -> Paper/shadow validation
   -> Promotion gate
```

No automatic production promotion is enabled by the continual-learning foundation.

## Continual model learning

The existing `RLFeedbackLoop` updates the configured trainable scorer after every closed trade. This is an online learning path, not a one-shot training job. The repository also contains the neural `DeepValueRLScorer`; it is a value-function approximator trained online from realized R-multiple outcomes, not a claim of full actor-critic/policy-gradient RL.

Model artifacts are versioned in the model registry. Candidate/champion/archived states make promotion and rollback auditable.

## VPS operation

Backtest remains an opt-in Compose profile:

```bash
docker compose --profile backtest run --rm aitos-backtest \
  python3 -m aitos.backtest.cli \
  --source clickhouse \
  --symbol BTCUSDT \
  --table ohlcv \
  --timeframe 15m
```

Paper trading does not need to be stopped for this command. The backtest container is execution-isolated and historical input is read-only.

## Production safety boundary

Continual learning is allowed to collect experience, train candidate models, and propose strategy/weight changes. Production deployment remains a separate governance step. A model must pass the configured validation pipeline before it can become a champion; this foundation does not silently replace a live strategy.
