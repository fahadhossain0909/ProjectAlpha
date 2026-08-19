"""Replayable lifecycle for historical limit-order queue simulation."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

Side = Literal["buy", "sell"]
Status = Literal["open", "partial", "filled", "cancelled", "expired"]

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
    last_queue_update: datetime | None = None
    ttl: timedelta | None = None

@dataclass(frozen=True)
class LifecycleFill:
    order_id: str
    quantity: float
    price: float
    timestamp: datetime
    maker: bool = True

class QueueOrderLifecycle:
    """Conservative passive-order lifecycle with queue aging and expiry."""
    def __init__(self) -> None:
        self.orders: dict[str, SimulatedOrder] = {}

    def place(self, order: SimulatedOrder) -> None:
        if order.quantity <= 0 or order.remaining <= 0:
            raise ValueError("order quantity must be positive")
        if order.queue_ahead < 0:
            raise ValueError("queue_ahead must be non-negative")
        if order.order_id in self.orders:
            raise ValueError("duplicate order_id")
        order.last_queue_update = order.created_at
        self.orders[order.order_id] = order

    def cancel(self, order_id: str) -> bool:
        order = self.orders.get(order_id)
        if order is None or order.status in {"filled", "cancelled", "expired"}:
            return False
        order.status = "cancelled"
        return True

    def age(self, timestamp: datetime) -> list[str]:
        """Expire orders whose TTL elapsed; queue priority otherwise remains FIFO."""
        expired: list[str] = []
        for order in self.orders.values():
            if order.status not in {"open", "partial"} or order.ttl is None:
                continue
            if timestamp >= order.created_at + order.ttl:
                order.status = "expired"
                order.last_queue_update = timestamp
                expired.append(order.order_id)
        return expired

    def update_queue(self, order_id: str, queue_ahead_reduction: float, timestamp: datetime) -> bool:
        order = self.orders.get(order_id)
        if order is None or order.status not in {"open", "partial"}:
            return False
        if queue_ahead_reduction < 0:
            raise ValueError("queue_ahead_reduction must be non-negative")
        order.queue_ahead = max(0.0, order.queue_ahead - queue_ahead_reduction)
        order.last_queue_update = timestamp
        return True

    def consume(self, side: Side, price: float, traded_qty: float, timestamp: datetime) -> list[LifecycleFill]:
        if traded_qty <= 0:
            return []
        self.age(timestamp)
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
            order.last_queue_update = timestamp
            fills.append(LifecycleFill(order.order_id, fill_qty, price, timestamp, maker=True))
        return fills

@dataclass(frozen=True)
class FeeSchedule:
    maker_rate: float = 0.0002
    taker_rate: float = 0.0004

    def __post_init__(self) -> None:
        if self.maker_rate < 0 or self.taker_rate < 0:
            raise ValueError("fee rates must be non-negative")

    def fee(self, notional: float, maker: bool) -> float:
        if notional < 0:
            raise ValueError("notional must be non-negative")
        return notional * (self.maker_rate if maker else self.taker_rate)
