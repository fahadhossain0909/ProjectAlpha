"""End-to-end historical runner for ProjectAlpha intelligence and execution."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Iterable
from aitos.backtest.market_adapter import HistoricalMarketAdapter, HistoricalMarketState
from aitos.backtest.replay import MarketReplay
from aitos.backtest.l2_execution import BookLevel, L2ExecutionModel
from aitos.backtest.execution import ExecutionSimulator
from aitos.backtest.queue_lifecycle import QueueOrderLifecycle, SimulatedOrder
from aitos.models.market import TradeTick, OrderBookSnapshot

@dataclass(frozen=True)
class HistoricalDecision:
    direction: str
    confidence: float
    quantity: float
    order_type: str = "market"
    limit_price: float | None = None
    queue_ahead: float = 0.0

@dataclass(frozen=True)
class HistoricalRunResult:
    states: int
    decisions: int
    fills: int
    requested_quantity: float
    filled_quantity: float
    final_equity: float
    total_return: float
    total_fees: float
    passive_orders: int = 0
    passive_fills: int = 0

class ProjectAlphaHistoricalRunner:
    """Run shared historical intelligence with market and passive execution."""
    def __init__(self, symbol: str, tick_size: float, initial_cash: float,
                 fee_rate: float = 0.0004, slippage_bps: float = 0.0,
                 trade_window: int = 500, max_book_levels: int | None = None) -> None:
        self.adapter = HistoricalMarketAdapter(symbol, tick_size, trade_window)
        self.execution = ExecutionSimulator(initial_cash, fee_rate, slippage_bps)
        self.l2 = L2ExecutionModel(max_levels=max_book_levels)
        self.queue = QueueOrderLifecycle()
        self.initial_cash = initial_cash
        self._order_seq = 0

    def run(self, events: Iterable[TradeTick | OrderBookSnapshot],
            decide: Callable[[HistoricalMarketState], HistoricalDecision]) -> HistoricalRunResult:
        ordered = MarketReplay(events)
        states = decisions = fills = passive_orders = passive_fills = 0
        requested = filled = 0.0
        last_price = 0.0
        for event in ordered.events:
            if isinstance(event, TradeTick):
                self.adapter.on_trade(event)
                last_price = event.price
                # A historical aggressor trade consumes passive orders resting on the opposite side.
                trade_side = "sell" if event.is_buyer_maker else "buy"
                passive_fills_found = self.queue.consume(trade_side, event.price, event.quantity, event.timestamp)
                for passive_fill in passive_fills_found:
                    order = self.queue.orders[passive_fill.order_id]
                    portfolio_side = "buy" if order.side == "buy" else "sell"
                    self.execution.execute(portfolio_side, passive_fill.quantity, passive_fill.price)
                    filled += passive_fill.quantity
                    fills += 1
                    passive_fills += 1
            else:
                self.adapter.on_order_book(event)
                if event.best_bid > 0 and event.best_ask > 0:
                    last_price = (event.best_bid + event.best_ask) / 2.0
            state = self.adapter.state()
            states += 1
            decision = decide(state)
            decisions += 1
            if decision.quantity <= 0 or decision.direction not in {"long", "short"}:
                continue
            side = "buy" if decision.direction == "long" else "sell"
            requested += decision.quantity
            if decision.order_type == "limit":
                price = decision.limit_price
                if price is None or price <= 0:
                    continue
                self._order_seq += 1
                order_id = f"bt-{self._order_seq}"
                self.queue.place(SimulatedOrder(order_id, side, price, decision.quantity,
                                                decision.quantity, max(0.0, decision.queue_ahead), event.timestamp))
                passive_orders += 1
                continue
            book = state.latest_order_book
            if book is None:
                continue
            bids = [BookLevel(level.price, level.quantity) for level in book.bids]
            asks = [BookLevel(level.price, level.quantity) for level in book.asks]
            result = self.l2.execute(side, decision.quantity, bids, asks)
            if result.filled_quantity > 0:
                self.execution.execute(side, result.filled_quantity, result.average_price)
                filled += result.filled_quantity
                fills += 1
                last_price = result.average_price
        final_equity = self.execution.snapshot(last_price).equity if last_price > 0 else self.initial_cash
        return HistoricalRunResult(states, decisions, fills, requested, filled,
                                   final_equity, final_equity / self.initial_cash - 1.0,
                                   self.execution.fees, passive_orders, passive_fills)
