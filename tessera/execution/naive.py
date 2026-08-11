"""Owns NaiveFillModel: market orders fill at the next bar's open, behind a pending
queue that gives every order an `arrival_ts` (ARCHITECTURE seams 4 and 5).

Why next open and not the current close: a strategy only knows a bar's close once the
bar has completed, so filling at that same close would trade at a price that was only
knowable at the instant it became unusable — look-ahead. The first price a decision
made at bar N's close can actually transact at is bar N+1's open.

The pending/latency queue is built now even though `latency_ns` defaults to 0 (so
`arrival_ts == submit_ts` and behaviour is unchanged). Threading it through later would
be invasive; building it now makes adding real latency a one-line config change.

Limit orders get one attempt at the next open: they fill if the open crosses the limit
price, otherwise they are cancelled (not left lingering).
"""

from __future__ import annotations

from dataclasses import dataclass

from tessera.core.events import Bar, Event
from tessera.execution.base import CostModel, Fill
from tessera.strategy.base import Order


@dataclass(frozen=True, slots=True)
class _Pending:
    """An order awaiting fill, with the time it was placed and when it may fill."""

    order: Order
    submit_ts: int
    arrival_ts: int


class NaiveFillModel:
    """Fills market orders at the next bar's open, after `latency_ns` has elapsed."""

    def __init__(self, cost_model: CostModel, latency_ns: int = 0) -> None:
        self._cost_model = cost_model
        self._latency_ns = latency_ns
        self._pending: list[_Pending] = []

    def submit(self, order: Order, ts: int) -> None:
        self._pending.append(_Pending(order, ts, ts + self._latency_ns))

    def on_event(self, event: Event) -> list[Fill]:
        # The naive model only transacts at bar opens.
        if not isinstance(event, Bar):
            return []

        fills: list[Fill] = []
        still_pending: list[_Pending] = []
        for p in self._pending:
            # An order fills only against a later bar of its own symbol, and not
            # before its arrival time. Otherwise it keeps waiting.
            ready = (
                p.order.symbol == event.symbol
                and event.ts > p.submit_ts
                and event.ts >= p.arrival_ts
            )
            if not ready:
                still_pending.append(p)
                continue

            price = event.open
            if p.order.type == "limit":
                limit = p.order.limit_price
                if limit is None:
                    raise ValueError("limit order without a limit_price")
                crosses = (p.order.side > 0 and price <= limit) or (
                    p.order.side < 0 and price >= limit
                )
                if not crosses:
                    # One shot missed: cancel (drop) rather than linger.
                    continue

            cost = self._cost_model.cost(p.order, price, p.order.qty)
            fills.append(
                Fill(
                    ts=event.ts,
                    symbol=p.order.symbol,
                    side=p.order.side,
                    qty=p.order.qty,
                    price=price,
                    cost=cost,
                    order_tag=p.order.tag,
                )
            )

        self._pending = still_pending
        return fills
