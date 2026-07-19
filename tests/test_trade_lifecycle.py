import asyncio

import pytest

from aitos.core.contracts import Event
from aitos.core.exceptions import TradeNotFoundError
from aitos.execution.order_executor import SimulatedOrderExecutor
from aitos.kernel.ai_kernel import AIKernel
from aitos.models.trade import Opportunity, TradeLifecycleState, TradeSide
from aitos.risk.models import PortfolioState
from aitos.trading.lifecycle import TradeLifecycle


def make_portfolio(**overrides) -> PortfolioState:
    defaults = dict(equity_usd=10_000.0, peak_equity_usd=10_000.0, volatility_percentile=30.0, max_pairwise_correlation=0.1)
    defaults.update(overrides)
    return PortfolioState(**defaults)


def make_opportunity(**overrides) -> Opportunity:
    defaults = dict(
        symbol="BTCUSDT",
        side=TradeSide.LONG,
        entry_price=100.0,
        stop_loss_price=98.0,
        take_profit_levels=[104.0],
        confidence=0.8,
        strategy_id="test-strategy",
        rationale="test rationale",
    )
    defaults.update(overrides)
    return Opportunity(**defaults)


@pytest.mark.asyncio
async def test_healthy_opportunity_opens_a_position(event_bus, risk_engine):
    lifecycle = TradeLifecycle(event_bus=event_bus, risk_engine=risk_engine)
    await lifecycle.initialize({})

    trade = await lifecycle.submit_opportunity(make_opportunity(), make_portfolio())

    assert trade.state == TradeLifecycleState.POSITION_OPENED
    assert trade.entry_price == 100.0
    assert trade.sl_price == 98.0
    assert trade.quantity > 0
    assert trade.leverage >= 1.0
    assert trade.trade_id in [t.trade_id for t in lifecycle.get_open_trades()]


@pytest.mark.asyncio
async def test_full_lifecycle_publishes_expected_event_sequence(event_bus, risk_engine):
    received_topics = []

    async def handler(event: Event):
        received_topics.append(event.topic)

    for topic in [
        "decision.opportunity", "decision.entry", "trade.order_submitted",
        "trade.order_filled", "trade.position_opened",
    ]:
        await event_bus.subscribe(topic, handler, group="test")

    lifecycle = TradeLifecycle(event_bus=event_bus, risk_engine=risk_engine)
    await lifecycle.initialize({})
    await lifecycle.submit_opportunity(make_opportunity(), make_portfolio())

    for _ in range(30):
        if len(received_topics) >= 5:
            break
        await asyncio.sleep(0.1)

    assert set(received_topics) == {
        "decision.opportunity", "decision.entry", "trade.order_submitted",
        "trade.order_filled", "trade.position_opened",
    }


@pytest.mark.asyncio
async def test_risk_veto_rejects_opportunity(event_bus, risk_engine):
    await risk_engine.trigger_emergency_stop("manual test trip")
    lifecycle = TradeLifecycle(event_bus=event_bus, risk_engine=risk_engine)
    await lifecycle.initialize({})

    trade = await lifecycle.submit_opportunity(make_opportunity(), make_portfolio())

    assert trade.state == TradeLifecycleState.REJECTED
    assert "risk veto" in trade.rejection_reason
    assert trade.trade_id not in [t.trade_id for t in lifecycle.get_open_trades()]


@pytest.mark.asyncio
async def test_hard_limit_breach_rejects_opportunity(event_bus, risk_engine):
    lifecycle = TradeLifecycle(event_bus=event_bus, risk_engine=risk_engine)
    await lifecycle.initialize({})

    # 30% drawdown vs a 20% hard cap
    breached_portfolio = make_portfolio(equity_usd=7000.0, peak_equity_usd=10_000.0)
    trade = await lifecycle.submit_opportunity(make_opportunity(), breached_portfolio)

    assert trade.state == TradeLifecycleState.REJECTED
    assert "hard limit breach" in trade.rejection_reason


@pytest.mark.asyncio
async def test_production_opportunity_without_approval_is_rejected(event_bus, risk_engine, kernel):
    lifecycle = TradeLifecycle(event_bus=event_bus, risk_engine=risk_engine, kernel=kernel)
    await lifecycle.initialize({})

    opportunity = make_opportunity(is_production=True, approved_by=None)
    trade = await lifecycle.submit_opportunity(opportunity, make_portfolio())

    assert trade.state == TradeLifecycleState.REJECTED
    assert "governance denied" in trade.rejection_reason


@pytest.mark.asyncio
async def test_production_opportunity_with_approval_opens(event_bus, risk_engine, kernel):
    lifecycle = TradeLifecycle(event_bus=event_bus, risk_engine=risk_engine, kernel=kernel)
    await lifecycle.initialize({})

    opportunity = make_opportunity(is_production=True, approved_by="fahad")
    trade = await lifecycle.submit_opportunity(opportunity, make_portfolio())

    assert trade.state == TradeLifecycleState.POSITION_OPENED


@pytest.mark.asyncio
async def test_stop_loss_hit_closes_trade_at_loss(event_bus, risk_engine):
    lifecycle = TradeLifecycle(event_bus=event_bus, risk_engine=risk_engine)
    await lifecycle.initialize({})
    trade = await lifecycle.submit_opportunity(make_opportunity(breakeven_at_r_multiple=None), make_portfolio())

    closed = await lifecycle.update_price(trade.trade_id, 97.5)  # below SL of 98

    assert closed.state == TradeLifecycleState.POSITION_CLOSED
    assert closed.exit_reason == "sl_triggered"
    assert closed.pnl < 0
    assert closed.trade_id not in [t.trade_id for t in lifecycle.get_open_trades()]
    assert closed.trade_id in [t.trade_id for t in lifecycle.get_closed_trades()]


@pytest.mark.asyncio
async def test_take_profit_hit_closes_trade_at_profit(event_bus, risk_engine):
    lifecycle = TradeLifecycle(event_bus=event_bus, risk_engine=risk_engine)
    await lifecycle.initialize({})
    trade = await lifecycle.submit_opportunity(make_opportunity(breakeven_at_r_multiple=None), make_portfolio())

    closed = await lifecycle.update_price(trade.trade_id, 105.0)  # above single TP of 104

    assert closed.state == TradeLifecycleState.POSITION_CLOSED
    assert closed.exit_reason == "tp_triggered"
    assert closed.pnl > 0


@pytest.mark.asyncio
async def test_multi_tp_partial_close_then_final_close(event_bus, risk_engine):
    opportunity = make_opportunity(take_profit_levels=[102.0, 106.0], breakeven_at_r_multiple=None)
    lifecycle = TradeLifecycle(event_bus=event_bus, risk_engine=risk_engine)
    await lifecycle.initialize({})
    trade = await lifecycle.submit_opportunity(opportunity, make_portfolio())
    original_size = trade.position_size_usd

    still_open = await lifecycle.update_price(trade.trade_id, 102.5)  # hits first TP
    assert still_open.state == TradeLifecycleState.POSITION_OPENED
    assert len(still_open.partial_exits) == 1
    assert still_open.position_size_usd < original_size
    assert still_open.take_profit_levels == [106.0]

    closed = await lifecycle.update_price(trade.trade_id, 107.0)  # hits second (final) TP
    assert closed.state == TradeLifecycleState.POSITION_CLOSED
    assert closed.exit_reason == "tp_triggered"
    assert closed.pnl > 0  # both partial + final legs were profitable


@pytest.mark.asyncio
async def test_breakeven_trigger_moves_stop_to_entry(event_bus, risk_engine):
    opportunity = make_opportunity(breakeven_at_r_multiple=1.0)  # 1R = 2.0 price units (100 -> 98 SL)
    lifecycle = TradeLifecycle(event_bus=event_bus, risk_engine=risk_engine)
    await lifecycle.initialize({})
    trade = await lifecycle.submit_opportunity(opportunity, make_portfolio())

    updated = await lifecycle.update_price(trade.trade_id, 102.5)  # +1.25R, past the 1R breakeven trigger, below TP of 104

    assert updated.state == TradeLifecycleState.POSITION_OPENED
    assert updated.sl_price == 100.0  # moved to entry
    assert updated.breakeven_triggered is True


@pytest.mark.asyncio
async def test_trailing_sl_tightens_as_price_moves_favorably(event_bus, risk_engine):
    opportunity = make_opportunity(
        trailing_sl_enabled=True, breakeven_at_r_multiple=None, take_profit_levels=[150.0]  # far TP so trailing dominates
    )
    lifecycle = TradeLifecycle(event_bus=event_bus, risk_engine=risk_engine)
    await lifecycle.initialize({})
    trade = await lifecycle.submit_opportunity(opportunity, make_portfolio())
    initial_sl = trade.sl_price

    updated = await lifecycle.update_price(trade.trade_id, 110.0)

    assert updated.sl_price > initial_sl  # trailing stop tightened upward
    assert updated.state == TradeLifecycleState.POSITION_OPENED


@pytest.mark.asyncio
async def test_failed_order_submission_rejects_trade_instead_of_opening_phantom_position(event_bus, risk_engine):
    from aitos.execution.order_executor import OrderExecutor, OrderResult

    class FailingExecutor(OrderExecutor):
        async def submit_order(self, request):
            return OrderResult(
                order_id="failed-1", symbol=request.symbol, side=request.side,
                filled_quantity=0.0, fill_price=0.0, success=False, error="insufficient margin",
            )

    lifecycle = TradeLifecycle(event_bus=event_bus, risk_engine=risk_engine, order_executor=FailingExecutor())
    await lifecycle.initialize({})

    trade = await lifecycle.submit_opportunity(make_opportunity(), make_portfolio())

    assert trade.state == TradeLifecycleState.REJECTED
    assert "order submission failed" in trade.rejection_reason
    assert "insufficient margin" in trade.rejection_reason
    assert lifecycle.get_open_trades() == []


@pytest.mark.asyncio
async def test_update_price_on_unknown_trade_raises(event_bus, risk_engine):
    lifecycle = TradeLifecycle(event_bus=event_bus, risk_engine=risk_engine)
    await lifecycle.initialize({})
    with pytest.raises(TradeNotFoundError):
        await lifecycle.update_price("nonexistent", 100.0)


@pytest.mark.asyncio
async def test_close_trade_twice_raises_not_found(event_bus, risk_engine):
    lifecycle = TradeLifecycle(event_bus=event_bus, risk_engine=risk_engine)
    await lifecycle.initialize({})
    trade = await lifecycle.submit_opportunity(make_opportunity(), make_portfolio())
    await lifecycle.close_trade(trade.trade_id, 101.0, "manual")
    with pytest.raises(TradeNotFoundError):
        await lifecycle.close_trade(trade.trade_id, 101.0, "manual")


@pytest.mark.asyncio
async def test_handle_event_auto_updates_matching_open_trade(event_bus, risk_engine):
    lifecycle = TradeLifecycle(event_bus=event_bus, risk_engine=risk_engine)
    await lifecycle.initialize({})
    trade = await lifecycle.submit_opportunity(make_opportunity(breakeven_at_r_multiple=None), make_portfolio())

    kline_event = Event(topic=f"market.kline.BTCUSDT.1m", payload={"symbol": "BTCUSDT", "close": 97.0}, source_module="test")
    await lifecycle.handle_event(kline_event)

    assert trade.trade_id not in [t.trade_id for t in lifecycle.get_open_trades()]
    assert trade.trade_id in [t.trade_id for t in lifecycle.get_closed_trades()]
