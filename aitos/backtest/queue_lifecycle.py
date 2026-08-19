"""Replayable lifecycle for historical limit-order queue simulation."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Side = Literal["buy", "sell"]
Status = Literal["open", "partial", "filled", "cancelled"]

@dataclass
class SimulatedOrder:
    order_id: str
    side: Side
    price: float
    quantity: float
    remaining: float
    queue_ahead: float
    created_at: datetime
    status: Status = "open"

@dataclass(frozen=True)
class LifecycleFill:
    order_id: str
    quantity: float
    price: float
    timestamp: datetime

class QueueOrderLifecycle:
    """Approximate passive-order lifecycle from displayed trade consumption."""
    def __init__(self) -> None:
        self.orders: dict[str, SimulatedOrder] = {}

    def place(self, order: SimulatedOrder) -> None:
        if order.quantity <= 0 or order.remaining <= 0:
            raise ValueError("order quantity must be positive")
        if order.order_id in self.orders:
            raise ValueError("duplicate order_id")
        self.orders[order.order_id] = order

    def cancel(self, order_id: str) -> bool:
        order = self.orders.get(order_id)
        if order is None or order.status in {"filled", "cancelled"}:
            return False
        order.status = "cancelled"
        return True

    def consume(self, side: Side, price: float, traded_qty: float, timestamp: datetime) -> list[LifecycleFill]:
        if traded_qty <= 0:
            return []
        fills: list[LifecycleFill] = []
        remaining_trade = traded_qty
        candidates = [o for o in self.orders.values() if o.status in {"open", "partial"} and o.side == side and o.price == price]
        candidates.sort(key=lambda o: (o.created_at, o.order_id))
        for order in candidates:
            if remaining_trade <= 0:
                break
            if order.queue_ahead > 0:
                consumed = min(order.queue_ahead, remaining_trade)
                order.queue_ahead -= consumed
                remaining_trade -= consumed
            if order.queue_ahead > 0 or remaining_trade <= 0:
                continue
            fill_qty = min(order.remaining, remaining_trade)
            order.remaining -= fill_qty
            remaining_trade -= fill_qty
            order.status = "filled" if order.remaining <= 1e-12 else "partial"
            fills.append(LifecycleFill(order.order_id, fill_qty, price, timestamp))
        return fills
