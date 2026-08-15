"""Owns the main simulation loop and the `Recorder` protocol it emits to.

The loop pulls events from a time-ordered stream and processes each one in a fixed
order. That order is the whole point: past orders fill against this event *before* the
strategy decides on it, and the strategy's new orders are submitted *after*, so they
can only ever fill on a later event. This makes same-bar look-ahead structurally
impossible (ARCHITECTURE seams 2, 3, 6).

Per iteration, for event E at time t:

  1. advance the clock to t
  2. fill PAST orders against E (arrival-gated inside the fill model) -> fills at E's open
  3. apply those fills to the book, record each
  4. update the latest price from E (for marking)
  5. call the strategy on E with a post-fill Context -> new orders
  6. submit the new orders (they cannot fill on E), record each
  7. record the portfolio snapshot marked at E

The engine emits records to a `Recorder`; it never returns a result (seam 6). The
`Recorder` protocol lives here, in core, so the engine owns the abstraction it depends
on and never imports from `runner`; `runner/recorder.py` provides implementations.

Rust-seam constraint: only plain dataclasses of primitives cross this loop
(`Event`, `Order`, `Fill`), and the only callback into user code is `Strategy.on_event`.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

from tessera.core.clock import Clock
from tessera.core.events import Bar, Event
from tessera.execution.base import FillModel
from tessera.portfolio import accounting
from tessera.portfolio.book import Book
from tessera.strategy.base import Context, Strategy


class Recorder(Protocol):
    """Sink for the record stream the engine produces (fills, orders, portfolio)."""

    def record(self, kind: str, payload: dict[str, Any]) -> None: ...

    def close(self) -> None: ...


def run(
    events: Iterable[Event],
    strategy: Strategy,
    fill_model: FillModel,
    book: Book,
    recorder: Recorder,
) -> None:
    """Run one backtest to completion, emitting records. Returns nothing (seam 6)."""
    clock = Clock()
    prices: dict[str, float] = {}

    for event in events:
        # 1. Time moves to this event.
        clock.advance(event.ts)

        # 2. Past orders fill against this event (at its open), then 3. apply + record.
        # A fill that would breach the book's leverage cap is rejected (dropped, not
        # partially filled) and recorded as a `reject` instead of applied. `prices` here
        # still holds prior marks (updated at step 4), so the margin check never looks ahead.
        for fill in fill_model.on_event(event):
            signed_qty = fill.side * fill.qty
            admitted = accounting.admits_fill(
                book, fill.symbol, signed_qty, fill.price, prices, fill.cost
            )
            if not admitted:
                recorder.record(
                    "reject",
                    {
                        "ts": fill.ts,
                        "symbol": fill.symbol,
                        "side": fill.side,
                        "qty": fill.qty,
                        "price": fill.price,
                        "notional": fill.price * fill.qty,
                        "reason": "max_leverage",
                        "limit": book.max_leverage,
                        "tag": fill.order_tag,
                    },
                )
                continue
            book.apply_fill(fill.symbol, signed_qty, fill.price, fill.cost)
            recorder.record(
                "fill",
                {
                    "ts": fill.ts,
                    "symbol": fill.symbol,
                    "side": fill.side,
                    "qty": fill.qty,
                    "price": fill.price,
                    "cost": fill.cost,
                    "tag": fill.order_tag,
                },
            )

        # 4. Latest price for marking (only bars carry a price here).
        if isinstance(event, Bar):
            prices[event.symbol] = event.close

        # 5. The strategy decides from post-fill state only.
        positions = {symbol: pos.qty for symbol, pos in book.positions.items()}
        ctx = Context(ts=clock.ts, cash=book.cash, positions=positions)
        orders = strategy.on_event(event, ctx)

        # 6. Route new orders. Submitted after step 2, so they cannot fill on E.
        for order in orders:
            fill_model.submit(order, clock.ts)
            recorder.record(
                "order",
                {
                    "ts": clock.ts,
                    "symbol": order.symbol,
                    "side": order.side,
                    "qty": order.qty,
                    "type": order.type,
                    "limit_price": order.limit_price,
                    "tag": order.tag,
                },
            )

        # 7. Snapshot the portfolio, marked at this event.
        recorder.record(
            "portfolio",
            {
                "ts": clock.ts,
                "cash": book.cash,
                "equity": accounting.equity(book, prices),
                "realized_pnl": book.realized_pnl,
                "unrealized_pnl": accounting.unrealized_pnl(book, prices),
            },
        )

    recorder.close()
