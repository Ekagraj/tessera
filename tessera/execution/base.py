"""Owns the execution interfaces: the `Fill` record and the `FillModel` / `CostModel`
protocols (ARCHITECTURE seams 4 and 5).

A `FillModel` turns submitted orders into fills. Orders are submitted with the time
they were placed; each is held in a pending queue until the clock reaches its
`arrival_ts = submit_ts + latency_ns`, then filled against a market event. A
`CostModel` prices the friction (commission/spread/impact) of a fill.

Note: seam 4 declares `CostModel.cost(..., ctx: MarketCtx)`, but `MarketCtx` does not
exist yet. We deliberately narrow the signature to what a basis-points cost needs now
and defer `ctx` until a cost model requires market state (spread/impact, a later week).
This is a flagged, temporary narrowing of the seam, not silent drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from tessera.core.events import Event
from tessera.strategy.base import Order


@dataclass(frozen=True, slots=True)
class Fill:
    """The outcome of an order: what actually traded, and the cost it incurred.

    `qty` is the (positive) quantity filled; `side` carries the direction (+1 buy,
    -1 sell). `ts` is the market time at which the fill occurred.
    """

    ts: int
    symbol: str
    side: int
    qty: float
    price: float
    cost: float
    order_tag: str = ""


class CostModel(Protocol):
    """Prices the friction of a fill: commission, spread, impact, etc."""

    def cost(self, order: Order, fill_price: float, qty: float) -> float: ...


class FillModel(Protocol):
    """Accepts submitted orders and produces fills as market events arrive."""

    def submit(self, order: Order, ts: int) -> None: ...

    def on_event(self, event: Event) -> list[Fill]: ...
