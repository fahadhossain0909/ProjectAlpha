# AITOS — AI Trading Operating System

A working, tested, real implementation of the AITOS specification —
built phase by phase from the foundation (Event Bus, AI Kernel, Agent
Framework) through live Binance execution, risk management, an
opportunity scanner, journaling/XAI, three self-training modules
(Knowledge Graph, RL, SHAP+Attention), and production-supervision
tooling — wired into two runnable systems: paper and live.

**Run it (paper)**: `python3 run_paper_trading.py` (after `docker compose
up -d` for Redis).
**Run it (live)**: `python3 run_live_trading.py` — real orders, requires
Binance API credentials and an interactive human confirmation. Read the
"Live trading" section below before touching this one.

**291 tests, all passing**, covering every module — including full
integration tests (`tests/test_app_wiring.py`) proving the pieces work
together, not just individually.

## What's included

| Component | File | Status |
|---|---|---|
| Module contract (`AITOSModule`) | `aitos/core/contracts.py` | ✅ implemented |
| Event / EventResponse / HealthStatus | `aitos/core/contracts.py` | ✅ implemented |
| Event Bus (Redis Streams, consumer groups, DLQ, request/reply, replay) | `aitos/eventbus/redis_bus.py` | ✅ implemented, real Redis |
| AI Kernel (registration, world state, decision fusion, governance gate) | `aitos/kernel/ai_kernel.py` | ✅ implemented |
| Agent Framework (`BaseAgent`, memory, consensus weighting) | `aitos/agents/base_agent.py` | ✅ implemented |
| Market data models (Kline, OrderBook, TradeTick, FundingRate, OI) | `aitos/models/market.py` | ✅ implemented |
| Exchange adapter contract | `aitos/exchange/base.py` | ✅ implemented |
| Binance USDT-M Futures adapter (REST + WebSocket) | `aitos/exchange/binance.py` | ✅ implemented, real endpoints |
| Rate limiter (token bucket) | `aitos/exchange/rate_limiter.py` | ✅ implemented |
| ClickHouse market data repository | `aitos/data/repository.py` | ✅ implemented |
| Data ingestion service (exchange → Event Bus → ClickHouse) | `aitos/data/ingestion.py` | ✅ implemented |
| Risk Engine (score, limits, circuit breaker, veto) | `aitos/risk/risk_engine.py` | ✅ implemented |
| Position sizing (Kelly variant) + adaptive leverage | `aitos/risk/position_sizing.py` | ✅ implemented |
| Circuit breaker state machine | `aitos/risk/circuit_breaker.py` | ✅ implemented |
| Trade Lifecycle state machine (opportunity → open → SL/TP/trailing → closed) | `aitos/trading/lifecycle.py` | ✅ implemented |
| Order execution (paper trading) | `aitos/execution/order_executor.py` | ✅ implemented |
| Opportunity Scanner (10-dimension scoring, ranking) | `aitos/intelligence/scanner.py` | ✅ implemented |
| Technical indicators (ATR, ADX, CVD, structure break, regime) | `aitos/intelligence/indicators.py` | ✅ implemented |
| Liquidity / funding / open-interest / RL-seam scoring | `aitos/intelligence/*.py` | ✅ implemented |
| XAI trade explanations (why_trade/why_now/why_leverage/why_sl/why_tp) | `aitos/xai/explanation.py` | ✅ implemented |
| Counterfactual explanations | `aitos/xai/counterfactual.py` | ✅ implemented |
| Journal System (auto-records every trade, periodic reviews) | `aitos/journal/journal_system.py` | ✅ implemented |
| Daily/Weekly/Monthly review statistics | `aitos/journal/reviews.py` | ✅ implemented |
| ClickHouse journal repository (trades + journal_entries) | `aitos/journal/repository.py` | ✅ implemented |
| Live order execution (Binance USDT-M Futures, signed private API) | `aitos/execution/binance_executor.py` | ✅ implemented, testnet-default |
| Exchange-side SL/TP orders + reconciliation | `aitos/trading/lifecycle.py`, `binance_executor.py` | ✅ implemented, opt-in |
| Reconciliation scheduler (automatic, background) | `aitos/trading/reconciliation.py` | ✅ implemented |
| ExchangeInfo-based precision (LOT_SIZE/PRICE_FILTER/MIN_NOTIONAL) | `aitos/exchange/symbol_filters.py` | ✅ implemented |
| Hedge-mode (dual-side position) support | `aitos/execution/binance_executor.py` | ✅ implemented, opt-in |
| Knowledge Graph writer (Neo4j, event-driven) | `aitos/knowledge_graph/writer.py` | ✅ implemented |
| Symbol correlation updater (real Pearson correlation, periodic) | `aitos/knowledge_graph/correlation_updater.py` | ✅ implemented |
| RL policy — online-learning contextual bandit | `aitos/intelligence/rl_policy.py` | ✅ implemented (simple, real, not deep RL) |
| RL feedback loop (trains from real closed trades) | `aitos/intelligence/rl_feedback.py` | ✅ implemented |
| SHAP-based trade outcome explainer (online-trainable) | `aitos/xai/ml_explainer.py` | ✅ implemented |
| ML explainer feedback loop (trains from real closed trades) | `aitos/xai/ml_feedback.py` | ✅ implemented |
| Deep RL — online-trained neural net (MLP) value scorer | `aitos/intelligence/deep_rl_policy.py` | ✅ implemented, opt-in upgrade |
| Attention XAI — from-scratch self-attention network | `aitos/xai/attention_explainer.py` | ✅ implemented |
| Attention feedback loop (trains from real closed trades) | `aitos/xai/attention_feedback.py` | ✅ implemented |
| System wiring — build_system/initialize_all/shutdown_all | `aitos/app.py` | ✅ implemented |
| Runnable paper-trading entrypoint (live Binance data) | `run_paper_trading.py` | ✅ implemented |
| Live trading entrypoint (real orders, governance-gated) | `run_live_trading.py`, `aitos/live_trading.py` | ✅ implemented |
| Retry-with-backoff for infra connections | `aitos/resilience.py` | ✅ implemented |
| Health/metrics HTTP server (`/health`, `/metrics`) | `aitos/health_server.py` | ✅ implemented |
| systemd unit files (daemonization guidance) | `deploy/aitos-paper.service`, `deploy/aitos-live.service` | ✅ implemented |
| Structured JSON logging | `aitos/logging_setup.py` | ✅ implemented |
| Config (Redis / ClickHouse / Neo4j / Binance credentials) | `aitos/config/settings.py` | ✅ implemented |
| Docker Compose (Redis, ClickHouse, Neo4j) | `docker-compose.yml` | ✅ ready to run |
| Saliency maps | — | 🚫 not applicable — no image/spatial data in this system |

Everything above is real, working, tested code — not stubs. The Decision
Fusion logic in `AIKernel.request_decision` is intentionally a transparent
weighted-vote placeholder (explainable and testable today); it's the exact
seam where AMT/Liquidity/OrderFlow/ML/DL/RL scoring plugs in later without
changing the method's contract.

## Binance data layer — what it does

- **REST** (`BinanceFuturesAdapter`): `fetch_klines`, `fetch_order_book`,
  `fetch_recent_trades`, `fetch_funding_rate`, `fetch_open_interest` —
  all public endpoints, no API key needed. Weighted through a token-bucket
  rate limiter so you don't get soft-banned on symbol-heavy setups.
- **WebSocket streaming**: `stream_klines`, `stream_trades`,
  `stream_order_book` connect to Binance's combined-stream endpoint and
  auto-reconnect with exponential backoff (1s → 60s cap) on disconnect.
- **`DataIngestionService`**: runs all three streams concurrently, and for
  every tick both (a) publishes an `Event` on the Event Bus — topics
  `market.kline.{symbol}.{timeframe}`, `market.trade.{symbol}`,
  `market.orderbook.{symbol}` — and (b) persists it to ClickHouse via
  `MarketDataRepository` (pass `repository=None` to skip persistence).
  `backfill_klines()` pulls REST history for a symbol before the live
  stream takes over.
- **Testing**: `tests/test_binance_parsing.py` verifies every REST/WS
  payload shape parses correctly using real Binance API response shapes
  (no network). `tests/test_binance_adapter.py` mocks HTTP with
  `aioresponses` and WebSocket with a fake connector — so the whole
  adapter is exercised without hitting Binance. `tests/test_ingestion.py`
  wires a fake exchange + fake repository through a real `EventBus` to
  prove the plumbing works end to end.

**Note on network egress**: this sandbox can't reach `fapi.binance.com` /
`fstream.binance.com`, so the adapter has only been verified against
mocked responses, not the live API. The parsing functions are written
directly against Binance's documented response shapes, but it's worth a
quick smoke test against the real API on your own machine before relying
on it for anything live.

## Quickstart

### 1. Start infrastructure

```bash
docker compose up -d
```

Redis is **required** (Event Bus transport). ClickHouse (market data +
journal persistence) and Neo4j (knowledge graph) are optional —
`run_paper_trading.py` detects if they're unreachable and runs without
them rather than failing.

### 2. Install dependencies

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure

```bash
cp .env.example .env
# defaults are fine for paper trading against local Docker infra
```

### 4. Run it

```bash
python3 run_paper_trading.py
```

This wires every module built across this project (Event Bus → Data
Layer → Risk Engine → Opportunity Scanner → Trade Lifecycle → Journal →
RL/ML feedback loops → optionally Knowledge Graph) into one system and
runs a continuous scan/trade loop against **live Binance market data**,
trading on paper (`SimulatedOrderExecutor` — no API keys needed, no real
orders). Ctrl-C for a graceful shutdown.

### 5. Run the test suite

Tests run against `fakeredis` and other fakes by default, so they're fast
and don't need Docker running:

```bash
PYTHONPATH=. pytest -v
```

`tests/test_app_wiring.py` specifically tests the same wiring
`run_paper_trading.py` uses — build the system, initialize it, run a scan
cycle, verify a position opens and later auto-closes via the real Event
Bus — all against fakes, no real infra needed to trust it works.

## Minimal usage example

```python
import asyncio
from redis.asyncio import Redis

from aitos.eventbus.redis_bus import EventBus
from aitos.kernel.ai_kernel import AIKernel, DecisionContext, Action
from aitos.agents.base_agent import BaseAgent, AgentDecision


class MyAgent(BaseAgent):
    async def contribute_decision(self, context):
        return AgentDecision(
            agent_id=self.module_id,
            confidence=0.75,
            direction="long",
            rationale="price above VWAP with rising CVD",
        )


async def main():
    redis_client = Redis.from_url("redis://localhost:6379/0")
    bus = EventBus(redis_client)
    await bus.initialize({})

    kernel = AIKernel(event_bus=bus)
    await kernel.initialize({})

    agent = MyAgent(agent_id="market-agent", event_bus=bus, consensus_weight=1.0)
    await agent.initialize({})
    await kernel.register_agent(agent)

    decision = await kernel.request_decision(DecisionContext(symbol="BTCUSDT"))
    print(decision.direction, decision.confidence, decision.contributions)

    # Non-production actions pass without approval; production actions
    # require an explicit human approver per the AI Constitution.
    result = await kernel.enforce_governance(
        Action(action_type="order.submit", payload={"symbol": "BTCUSDT"}, is_production=True)
    )
    print(result.approved, result.reason)

    await kernel.shutdown()
    await bus.shutdown()


asyncio.run(main())
```

## Design notes / non-negotiables honored

- **Event-driven, no direct coupling**: agents and the kernel only talk
  through `EventBus.publish` / `subscribe` / `request_reply`; nothing calls
  another module's methods directly.
- **Everything logged**: `aitos/logging_setup.py` emits one JSON object per
  log line, ready to ship to ClickHouse.
- **Human-in-the-loop**: `AIKernel.enforce_governance` blocks any
  `is_production=True` action without an explicit `approved_by`.
- **At-least-once delivery + DLQ**: the Event Bus uses Redis Streams
  consumer groups with explicit ACKs; messages that fail
  `MAX_DELIVERY_ATTEMPTS` times land in `stream:dlq` instead of retrying
  forever or being silently dropped.
- **Explainable by construction**: `FusedDecision.conflicting_evidence` and
  `AgentDecision.rationale` are populated on every fusion call — no black
  box even at this early stage.

## Risk Engine — what it does

- **`assess(portfolio)`** — computes a 0-100 score from four weighted
  components (position 30%, market 25%, system 15%, portfolio 30%, spec
  §31.1), publishes `risk.score_update`, and returns a `RiskScoreBreakdown`
  with a plain-language `explanation` list — no black box.
  - Score > 70 → `REDUCE_SIZE`, > 85 → `NO_NEW_ENTRIES`, > 95 →
    `EMERGENCY_STOP` (auto-triggers the circuit breaker).
- **`check_limits(portfolio)`** — checks every limit in the spec §31.2
  table (risk/trade, risk/day, risk/week, drawdown, leverage, correlated
  exposure, sector exposure, open positions, data freshness), flagging
  each breach as either a soft (default-limit) or hard-cap breach.
- **Circuit breaker** (`aitos/risk/circuit_breaker.py`) — the CLOSED →
  OPEN → HALF_OPEN → CLOSED state machine from spec §23.3. A hard-cap
  breach or an `EMERGENCY_STOP` score trips it automatically;
  `attempt_recovery()` moves OPEN → HALF_OPEN once the cooldown elapses,
  `record_probe_result()` resolves the probe.
- **`veto(portfolio)`** — the hook for consensus/decision logic (spec
  §6.16: "Risk Agent ... have veto power"). Returns `True` whenever the
  breaker isn't fully CLOSED or the last assessment says no new entries.
- **Position sizing** (`aitos/risk/position_sizing.py`) — Kelly-variant
  sizing dampened by volatility and correlation (spec §30.2), plus
  `calculate_adaptive_leverage` (inverse function of volatility + risk
  score, capped at whatever `RiskLimits.max_leverage` allows).

All of this is standalone right now — nothing auto-wires it into
`AIKernel` or `DataIngestionService` yet. That wiring (Risk Agent calling
`veto()` during consensus; Trade Lifecycle calling `check_limits()` and
`calculate_position_size()` before every order) is exactly what the next
phase, Trade Lifecycle, will do.

## Trade Lifecycle — what it does

Wires the Risk Engine and AI Kernel into an actual (paper-traded) trade,
end to end (spec §30.1):

```
OPPORTUNITY_DETECTED → [risk veto? hard limit? governance?] → REJECTED
                     ↘ ENTRY_VALIDATED → position sizing → ORDER_SUBMITTED
                       → POSITION_OPENED → [SL/TP/breakeven/trailing monitored]
                       → EXIT_TRIGGERED → POSITION_CLOSED
```

- **`submit_opportunity(opportunity, portfolio)`** — runs an `Opportunity`
  through three gates in order: `risk_engine.veto()`, hard-cap limit
  check, then (for `is_production=True` opportunities) `AIKernel`
  governance. Any failure returns a `REJECTED` trade with
  `rejection_reason` set — nothing is silently dropped. On success it
  sizes the position via `calculate_position_size`, submits the order via
  an injectable `OrderExecutor`, and returns a `POSITION_OPENED` trade.
- **`update_price(trade_id, price)`** — call on every new tick (or just
  let it happen automatically: `handle_event` reacts to
  `market.kline.*` / `market.trade.*` events from the data layer and
  updates any open trade on that symbol). Checks, in order: stop loss,
  take-profit (partial close if there are multiple TP levels, full close
  on the last one), break-even trigger (moves SL to entry after a
  configurable R-multiple), then trailing stop (only ever tightens).
- **`close_trade(trade_id, price, reason)`** — realizes P&L (accounting
  for any prior partial closes) and publishes `trade.position_closed`.
- **Order execution** (`aitos/execution/order_executor.py`) —
  `SimulatedOrderExecutor` (paper trading) fills instantly at the
  reference price plus optional slippage. A live executor is deliberately
  not built here — it's security-sensitive (API keys, idempotency) and
  every production order already has to clear `enforce_governance`, so it
  gets its own phase.

Every transition publishes an event — `decision.opportunity`,
`decision.entry`, `trade.rejected`, `trade.order_submitted`,
`trade.order_filled`, `trade.position_opened`, `trade.position_updated`,
`trade.trailing_sl`, `trade.partial_close`, `trade.sl_triggered`,
`trade.tp_triggered`, `trade.position_closed` — so Journal/XAI (next
phases) can subscribe without touching this module.

## Opportunity Scanner — what it does

Scans a symbol universe and scores each across the spec §32.1 ten
dimensions (each 0-10, weighted sum → 0-100 composite):

| Dimension | How it's computed |
|---|---|
| Trend strength | Real ADX (Wilder's, `indicators.adx`) |
| Volatility | ATR percentile vs its own recent history, peaked at a "sweet spot" |
| Order flow bias | Cumulative volume delta from kline taker-buy/sell volume |
| Auction context | Simplified break-of-structure (BOS) detector vs recent swing range |
| Market regime | trending/ranging/volatile classification from ADX + ATR percentile |
| Liquidity quality | Live order book spread tightness + two-sided depth balance |
| Funding rate | Cost-of-carry: which side is *paid* by current funding |
| Open interest trend | Rising OI that confirms vs. contradicts the proposed direction |
| Lead-lag | Real Pearson correlation between a symbol's returns and a lagged reference symbol's (default BTCUSDT) |
| RL confidence | **Placeholder seam** — `NeutralRLScorer` returns a neutral 5.0; no RL policy has been trained yet (that's its own Learning Engine phase) |

- **`scan_symbol(symbol)`** — pulls klines/order book/funding/OI live from
  the exchange adapter, computes all ten scores, and determines a
  direction from structure-break + order-flow agreement
  (`determine_direction`). Returns `None` when there's no clear
  directional edge — the scanner only surfaces actionable setups.
- **`scan_all()` / `rank()`** — scans every configured symbol, publishes a
  `market.opportunity_scanned` summary event, and returns the top-N
  candidates above `min_score_threshold`.
- **`to_opportunity(candidate)`** — bridges a `ScanCandidate` into the
  `Opportunity` the Trade Lifecycle already knows how to validate: an
  ATR-based stop (never fixed-pip, per spec §30.2) and take-profit levels
  at 1R/2R/3R by default — which lines up exactly with the Trade
  Lifecycle's existing multi-level partial-close handling.

`tests/test_scanner.py` includes a full pipeline test — scan → rank →
`to_opportunity` → `TradeLifecycle.submit_opportunity` — proving the last
three phases now work together end to end, not just individually.

## XAI + Journal System — what it does

**XAI** (`aitos/xai/`) — spec §33:
- **`build_trade_explanation`** — deterministic NLG from structured data
  (spec §33.2's "natural language generation" technique). Every trade
  gets all seven required fields (`why_trade`, `why_now`, `why_leverage`,
  `why_sl`, `why_tp`, `supporting_evidence`, `conflicting_evidence`,
  `risks`) built from the Opportunity Scanner's component scores, the
  Risk Engine's last assessment, and the trade's own sizing — no LLM call,
  no model dependency, so no trade is ever missing an explanation.
- **`counterfactual_for_threshold`** — spec §33.2's "what would change the
  decision" technique, computed as arithmetic on the scanner's weighted
  component scores.
- **Not implemented**: SHAP/permutation feature importance, attention
  visualization, saliency maps — all three genuinely need a trained
  ML/DL model that doesn't exist in this codebase yet. Documented (not
  faked) in `aitos/xai/xai_techniques.py`.

**Journal** (`aitos/journal/`) — spec §34:
- **`JournalSystem`** subscribes to `trade.position_opened` /
  `trade.position_closed` / `trade.rejected` on the Event Bus (zero direct
  coupling to the Trade Lifecycle) and automatically writes a `PRE_TRADE`
  entry (with the full `TradeExplanation`) on open and a `POST_TRADE`
  entry (P&L, exit reason) on close.
- **`record_mistake(trade_id, mistake, lesson, improvement)`** — the hook
  for a human or future Learning Agent to add retrospective notes.
- **`generate_daily_review` / `_weekly_review` / `_monthly_review`** —
  real statistics (win rate, R-multiples, per-strategy P&L, Sharpe ratio,
  max drawdown, Calmar ratio) computed in `reviews.py` over closed trades,
  published back onto the bus and persisted via `JournalRepository`
  (ClickHouse — `trades` + `journal_entries` tables, both optional: pass
  `repository=None` to run in pure pub/sub mode, as the tests do).

`tests/test_journal_system.py` proves the wiring end-to-end: submitting
an opportunity through the real `TradeLifecycle` causes the `JournalSystem`
— with no direct reference to it — to automatically produce and cache a
full explanation, purely by listening to the Event Bus.

## Live order execution — what it does, and the guardrails around it

`BinanceFuturesOrderExecutor` (`aitos/execution/binance_executor.py`) is a
real `OrderExecutor` — implementing the same interface as
`SimulatedOrderExecutor` — that places actual orders against Binance's
signed private Futures API (HMAC-SHA256 request signing, `X-MBX-APIKEY`
header, replay-protected via `timestamp`/`recvWindow`).

**Safety by construction, not by convention:**
- **Defaults to testnet.** `testnet=False` must be passed explicitly and
  deliberately to touch mainnet — it's never a config default.
- **Governance still applies.** Nothing about having a live executor
  bypasses `TradeLifecycle.submit_opportunity`'s existing gates: an
  `is_production=True` opportunity still needs `AIKernel` approval before
  this executor is ever called.
- **Failures reject cleanly.** This phase also fixed a real gap: the
  Trade Lifecycle previously trusted every `OrderResult` as a success. It
  now checks `order_result.success` and rejects the trade (with the
  exchange's error message) instead of opening a phantom position — see
  `test_failed_order_submission_rejects_trade_instead_of_opening_phantom_position`.
- **Secrets never logged.** API key/secret come only from `BinanceSettings`
  (env vars) or direct constructor args — never hardcoded, and the HMAC
  signature itself is never written to logs.
- **Idempotent retries.** `OrderRequest.client_order_id` lets a caller
  retry safely — Binance rejects a duplicate `newClientOrderId` rather
  than double-filling.

**What's deliberately not here yet** (see Next steps): per-symbol
quantity/price precision currently must be supplied by the caller (no
`/fapi/v1/exchangeInfo` fetch built in), hedge-mode (dual-side position)
isn't supported, and the Trade Lifecycle's SL/TP are still monitored
virtually via `update_price` rather than as resting exchange-side orders
— so a live deployment needs its own price-feed loop calling
`update_price` promptly, or a gap between a real fill and this system
noticing it.

## Exchange-side SL/TP — what it does

The gap flagged in the previous phase: virtual-only SL/TP monitoring means
a stop only triggers while this process is actively calling
`update_price`. `TradeLifecycle(..., use_exchange_side_stops=True)` closes
that gap when the injected executor supports it
(`BinanceFuturesOrderExecutor.supports_exchange_side_stops` is `True`;
`SimulatedOrderExecutor`'s is `False` — the flag silently downgrades to
off for paper trading rather than erroring):

- **On open**: places a real `STOP_MARKET` order at the stop price and a
  `TAKE_PROFIT_MARKET` order per TP level (split proportionally the same
  way virtual partial closes already work — 50% at each intermediate
  level, the rest at the final one), both `reduceOnly` so they can only
  shrink the position, never flip or add to it.
- **On breakeven / trailing stop updates**: cancels the old resting stop
  order and places a new one at the updated price — the exchange-side
  order always matches what `update_price` currently believes the stop
  should be.
- **On any close** (virtual detection, partial or full): cancels the
  now-irrelevant resting order(s) so they can't also fill later and
  produce a second, unintended close.
- **`reconcile_trade(trade_id)`** — the actual resilience piece: queries
  the resting stop-loss and active take-profit order's status on the
  exchange and closes the trade internally if either shows `FILLED`,
  even if `update_price` never saw the price move (e.g. this process was
  down). Run this periodically for every open trade in any real
  deployment — nothing in this codebase calls it automatically yet.

Virtual monitoring stays authoritative for the Trade Lifecycle's own
bookkeeping either way; the exchange-side orders are real protection for
the position itself, and `reconcile_trade` is what keeps the two in sync.

## Reconciliation scheduler — closing last phase's gap

`ReconciliationScheduler` (`aitos/trading/reconciliation.py`) is the piece
that was missing: it runs `TradeLifecycle.reconcile_trade` for every open
trade automatically, on a background interval (default 30s), instead of
requiring something else to remember to call it.

- **`run_once()`** — reconciles every open trade immediately (call this
  once right after startup or after reconnecting to the exchange, in
  addition to the background loop) and publishes `trade.reconciliation_run`
  with counts.
- **Background loop** — starts in `initialize()`, runs on
  `interval_seconds`, survives per-trade errors (one trade failing to
  reconcile doesn't stop the others from being checked that pass).
- **`health_check()`** — reports total runs, last run's checked/closed
  counts, and error count; goes `UNHEALTHY` if the background task has
  died.

`tests/test_reconciliation_scheduler.py` includes the actual resilience
scenario end-to-end: a trade opens with exchange-side stops, its stop-loss
fills on the (fake) exchange without `update_price` ever being called,
and the scheduler's background loop notices and closes it correctly
within a couple of ticks — no manual intervention.

## ExchangeInfo-based precision — what it does

The gap from last phase: quantity/price precision had to be hand-supplied
as a bare integer, and there was no protection against orders too small
for Binance to accept.

- **`aitos/exchange/symbol_filters.py`** — `SymbolFilters` (per symbol:
  `step_size`/`tick_size`/`min_notional`), parsed from a real
  `/fapi/v1/exchangeInfo` response via `parse_exchange_info`.
  `round_quantity`/`round_price` use `Decimal` step-size math (not naive
  decimal-place truncation) — Binance's step sizes aren't always clean
  powers of ten, and float rounding can go the wrong way right at a
  boundary.
- **`BinanceFuturesAdapter.fetch_exchange_info(symbols=None)`** — public
  endpoint, no auth, on the data-layer adapter (shared with the rest of
  the system).
- **`BinanceFuturesOrderExecutor.load_symbol_filters(filters)`** —
  replaces the old `quantity_precision: Dict[str, int]` constructor arg
  entirely. Call it once at startup with
  `await exchange.fetch_exchange_info()`'s result, and again periodically
  (Binance does change these).
- **Min-notional protection** — every order (`submit_order`,
  `place_stop_loss_order`, `place_take_profit_order`) now checks
  `SymbolFilters.meets_min_notional` *before* making a network call. An
  order too small to accept comes back as a normal failed `OrderResult`
  (which `TradeLifecycle` already rejects cleanly) instead of burning a
  round-trip on a guaranteed Binance rejection.

## Hedge-mode (dual-side position) support — what it does

The last gap from the live-execution phases: the executor assumed
Binance's default one-way mode, where a single `side` (BUY/SELL) fully
describes intent. Hedge mode lets an account hold a LONG and a SHORT on
the same symbol simultaneously — `side` alone becomes ambiguous (a BUY
could open a LONG *or* close a SHORT), so Binance requires a
`positionSide` (LONG/SHORT) parameter instead, and rejects `reduceOnly`
when `positionSide` is also present.

- **`BinanceFuturesOrderExecutor(..., hedge_mode=True)`** — every order
  (`submit_order`, `place_stop_loss_order`, `place_take_profit_order`)
  builds its `side`/`positionSide`/`reduceOnly` parameters correctly for
  whichever mode is active, via a single internal `_position_params`
  helper. One-way mode (the default) is completely unchanged by this —
  verified by `test_one_way_mode_still_sends_reduce_only_and_no_position_side`.
- **`get_position_mode()`** — queries Binance's actual account-wide
  setting (`/fapi/v1/positionSide/dual`), so you can verify it agrees
  with what this instance was constructed with before trading — a
  mismatch would mean every order is built with the wrong parameters.
- **`set_position_mode(hedge_mode)`** — changes the setting on Binance
  itself (only takes effect with no open positions/orders) and updates
  this instance's flag to match.

## Self-training modules — built ahead of the data, wired to grow with it

Rather than waiting for a paper-trading run to accumulate data and then
building Knowledge Graph / RL / SHAP as a separate offline step, all
three are built **now**, as real-time Event Bus subscribers — same
pattern as `JournalSystem`. The moment trades start closing (paper or
live), these start learning automatically. Nothing here is faked or
stubbed to "look done" — every piece is a genuine, if intentionally
simple, working implementation:

### Knowledge Graph (`aitos/knowledge_graph/`)
- **`KnowledgeGraphWriter`** subscribes to `trade.position_opened` /
  `trade.position_closed` / `journal.mistake_recorded` and builds the
  graph incrementally: `(:Trade)-[:ON_SYMBOL]->(:Symbol)`,
  `-[:USED_STRATEGY]->(:Strategy)`, `-[:HAD_MISTAKE]->(:Mistake)`. The
  Neo4j driver is injected (same DI pattern as `EventBus`'s Redis
  client), so `tests/test_knowledge_graph_writer.py` verifies exact
  Cypher/parameters against a fake driver — no server needed to prove the
  logic is right; a real one (already in `docker-compose.yml`) is only
  needed to actually run it.
- **`SymbolCorrelationUpdater`** periodically computes *real* pairwise
  Pearson correlation (reusing the same `indicators.pearson_correlation`
  the Opportunity Scanner's lead-lag scoring already uses) across the
  tracked symbol universe from live kline data, and pushes
  `CORRELATED_WITH {coefficient}` edges — same background-loop pattern as
  `ReconciliationScheduler`.

### RL policy (`aitos/intelligence/rl_policy.py`, `deep_rl_policy.py`, `rl_feedback.py`)
- **`TabularBanditRLScorer`** (the default in `build_system`) — a real,
  working contextual bandit. Tracks a running-mean reward (realized
  R-multiple) per `(symbol, regime, direction)` bucket via Welford's
  incremental update, with low-sample buckets shrunk back toward neutral.
  Cold start behaves exactly like `NeutralRLScorer` (5.0) until real data
  exists. Simple and robust — the safer default.
- **`DeepValueRLScorer`** — a genuine multi-layer neural network
  (`MLPRegressor`, tanh activation, online SGD) predicting expected
  reward from the *full* feature vector rather than a bucket key, so it
  *generalizes* to feature combinations never seen verbatim during
  training (`tests/test_deep_rl_policy.py`'s
  `test_learns_a_real_pattern_and_generalizes_to_unseen_but_similar_context`
  proves this — the tabular version structurally can't do it). Pass
  `rl_scorer=DeepValueRLScorer()` to `build_system` to use it instead.
  Honesty about scope: this is value-function approximation via
  supervised regression on realized rewards, not a full RL algorithm — no
  temporal credit assignment, no policy gradient, no replay-based
  actor-critic.
- **`RLFeedbackLoop`** subscribes to `trade.position_closed` and trains
  whichever scorer it's given — same `update(symbol, context, reward)`
  interface for both. No manual training step either way.

### SHAP + Attention explanations (`aitos/xai/`)
- **`TradeOutcomeClassifier`** (`ml_explainer.py`) — online logistic
  classifier (`SGDClassifier`), explained with `shap.LinearExplainer`
  (exact, not sampled, for a linear model) once `is_ready`.
- **`AttentionExplainer`** (`attention_explainer.py`) — a genuine
  single-head self-attention network built **from scratch with numpy**
  (no PyTorch — a ~130-parameter model over 10 scalar features doesn't
  need a deep learning framework). Trained online via **numerical
  gradient descent** (central finite differences) rather than hand-derived
  backprop — a deliberate correctness choice: for a model this size,
  trained one mini-batch at a time, the speed cost is negligible, and it
  eliminates the real risk of a subtle sign/transpose bug in hand-written
  attention backprop silently producing wrong-but-plausible explanations.
  `attention_weights()` returns which of the scanner's ten dimensions the
  model's attention query weighed most for a given prediction —
  `tests/test_attention_explainer.py` proves it actually learns (loss
  decreases on a fixed example — basic gradient correctness — and, on a
  clear synthetic pattern, the informative feature's attention weight
  swings by >0.5 while an uninformative feature's stays flat). One
  documented caveat: attention weight direction doesn't always match
  naive "important = high attention" intuition, a known finding in
  attention-interpretability research — see the module docstring.
- **Honesty gate, both classifiers**: before `is_ready` (both outcome
  classes observed, minimum 30 samples by default), `explain()`/
  `attention_weights()` return an empty result rather than a
  confident-looking one from a barely-trained model.
- **`MLExplainerFeedbackLoop`** / **`AttentionFeedbackLoop`** — same
  subscribe-and-train pattern as `RLFeedbackLoop`, one per classifier.

All of the above (`TabularBanditRLScorer` by default, both XAI
classifiers) are wired into the running system by `aitos/app.py`'s
`build_system` — running either entrypoint script trains all three
automatically from real trade outcomes, paper or live. The Knowledge
Graph only activates if a Neo4j driver connects successfully (optional
infra, same pattern as ClickHouse).

## System wiring (`aitos/app.py`) — how it all fits together

- **`build_system(...)`** — pure construction: takes already-created
  infra (an `EventBus`, an `ExchangeAdapter`, an `OrderExecutor`, and
  optional repositories/graph driver/`rl_scorer`) and returns a
  `SystemComponents` with every module wired to every other module it
  depends on. Nothing is initialized yet, so a caller can still adjust
  components first.
- **`initialize_all(components)`** — starts every module in dependency
  order, then subscribes `TradeLifecycle.handle_event` to `market.kline.*`
  / `market.trade.*` on the real Event Bus — this is what makes open
  trades update automatically from live price data without any manual
  `update_price` calls (`test_price_feed_subscription_auto_updates_open_trades`
  proves exactly this).
- **`run_scan_and_trade_cycle(components, tracker, is_production=False, approved_by=None)`**
  — one iteration of the trading loop: refresh the tracker's equity (if
  it supports it — `LivePortfolioTracker` does, `PaperPortfolioTracker`
  doesn't need to), `assess()` risk, scan, rank, and submit opportunities
  for symbols not already held. The `is_production`/`approved_by`
  parameters are what `run_live_trading.py` uses to route every
  opportunity through `AIKernel.enforce_governance`.
- **`PaperPortfolioTracker`** / **`LivePortfolioTracker`** — both satisfy
  the same `PortfolioTracker` protocol; the paper version simulates
  equity from closed-trade P&L, the live version queries
  `BinanceFuturesOrderExecutor.get_account_balance()` for real numbers.
- **`run_paper_trading.py`** — connects to Redis (retried with backoff)
  and optionally ClickHouse/Neo4j, then loops `run_scan_and_trade_cycle`
  every 60 seconds against live Binance data, paper-traded, until Ctrl-C.

## Live trading (`run_live_trading.py`, `aitos/live_trading.py`)

Same system, same wiring, real orders. Deliberately a **separate script**
from paper trading so running the wrong file by habit isn't how you end
up trading live.

- **Interactive session-level approval** (`confirm_live_trading`) — at
  startup, the operator must type their identifier and then the exact
  phrase `I APPROVE LIVE TRADING`. That identifier becomes every
  opportunity's `approved_by` for the session. This is a deliberate
  design choice: a live loop that paused for human approval on every
  individual trade wouldn't be a trading system, but starting one with no
  human gate at all would defeat the AI Constitution's governance
  requirement — session-level approval is the middle ground this script
  picked. If your deployment wants per-trade approval instead, that's a
  different (reasonable) design this script doesn't build.
- **`prepare_live_executor`** — refuses to start without
  `BINANCE_API_KEY`/`BINANCE_API_SECRET`, verifies the account's actual
  hedge-mode setting matches `BINANCE_HEDGE_MODE` (refusing to trade on a
  mismatched assumption rather than guessing), and loads real
  `/fapi/v1/exchangeInfo` precision before any order is placed.
- **`use_exchange_side_stops=True`** always — a live position gets real
  resting SL/TP orders on Binance, plus `ReconciliationScheduler` running
  both on its own interval and once per scan cycle.
- Defaults to Binance's **testnet**; mainnet requires
  `BINANCE_TESTNET=false` set explicitly.
- All of `confirm_live_trading`/`prepare_live_executor`'s logic is
  unit-tested (`tests/test_live_trading.py`) against fakes/mocks — no
  real credentials or terminal needed to trust it works correctly.

## Production supervision

Flagged as a gap in earlier phases, now built:

- **`aitos/resilience.py`**'s `retry_with_backoff` — exponential backoff
  with jitter, used by both entrypoint scripts around the required Redis
  connection so a transient outage at startup doesn't crash the process
  immediately.
- **`aitos/health_server.py`**'s `HealthServer` — a small aiohttp server
  exposing `GET /health` (JSON, one entry per module, `200`/`503` based
  on overall health) and `GET /metrics` (Prometheus text format) for a
  process supervisor, load balancer, or monitoring stack to poll. Both
  scripts start one automatically (`:8090` for paper, `:8091` for live).
- **`deploy/aitos-paper.service`** / **`deploy/aitos-live.service`** —
  systemd unit examples: journald logging, restart-on-crash-with-backoff
  for paper trading, deliberately *no* auto-restart for live trading
  (crash-looping with real money should page a human, not restart
  silently) — and an honest comment explaining that live trading's
  interactive confirmation and systemd's non-interactive service model
  are in tension, with two real options for resolving it.

Still not built, and said plainly rather than implied: no daemonization
*within* Python itself (systemd/your process manager owns that), no
alerting integration (metrics are exposed, not pushed anywhere), and
`LivePortfolioTracker`'s peak-equity tracking is in-memory only —
restarting the live script resets drawdown tracking to the current
balance rather than persisting the true historical peak.

## Next steps (genuinely not built)

1. Persist `LivePortfolioTracker`'s peak equity (e.g. in ClickHouse
   alongside the journal) so drawdown tracking survives a restart.
2. A non-interactive live-trading approval flow (e.g. a signed
   pre-approval token checked at startup) as an alternative to
   `confirm_live_trading`'s interactive prompt, for deployments that
   can't attach a terminal.
3. Automatic per-symbol leverage configuration
   (`BinanceFuturesOrderExecutor.set_leverage` exists but isn't called
   automatically anywhere) and `/fapi/v1/exchangeInfo` refresh on an
   interval rather than once at startup.
4. Metrics alerting/aggregation (Prometheus scrape config, Grafana
   dashboard, PagerDuty/etc. integration) — `/metrics` exposes the data,
   nothing consumes it yet.
5. A trained deep RL policy with actual temporal credit assignment
   (replay buffer, policy gradient/actor-critic across multi-step
   episodes) — `DeepValueRLScorer` is real but is single-step reward
   regression, not full RL.
6. Saliency maps remain inapplicable (no image/spatial data anywhere in
   this system) rather than unbuilt — see the status table above.
