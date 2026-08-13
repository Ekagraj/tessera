"""Owns the mean-reversion example strategy (buy after down days, sell after up days).

Targets a long position the day after a down close and a short position the day after an
up close, emitting whatever order moves the current position to that target (the engine's
accounting handles flips). It remembers only the previous close — its own state, never
the context's history.

Sizing is **fixed-fractional notional**: the target is `+/- target_frac * initial_cash`
worth of stock, converted to shares at the current price (`target_frac * initial_cash /
close`). Crucially the order is the **delta** from the current position to that target, so an
up day after a long flips long -> short in a single order whose size closes the long *and*
opens the short — the exact zero-crossing that `Book.apply_fill` splits (Task 4).
"""

from __future__ import annotations

from tessera.core.events import Bar, Event
from tessera.strategy.base import Context, Order


class Reversal:
    """Go long after a down day, short after an up day."""

    def __init__(self, target_frac: float = 0.10, initial_cash: float = 100_000.0) -> None:
        # Dollar exposure to target (long or short); converted to shares at order time.
        self._notional = target_frac * initial_cash
        self._prev_close: float | None = None

    def on_event(self, event: Event, ctx: Context) -> list[Order]:
        if not isinstance(event, Bar):
            return []
        prev = self._prev_close
        self._prev_close = event.close
        if prev is None or event.close == prev:
            return []  # need a prior close, and a flat day is no signal

        size = self._notional / event.close  # target notional -> shares at today's price
        target = size if event.close < prev else -size
        delta = target - ctx.position(event.symbol)  # order the move, not the target
        if delta > 0.0:
            return [Order(event.symbol, +1, delta, "market", tag="reversal_long")]
        if delta < 0.0:
            return [Order(event.symbol, -1, -delta, "market", tag="reversal_short")]
        return []
