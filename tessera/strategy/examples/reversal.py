"""Owns the mean-reversion example strategy (buy after down days, sell after up days).

Targets a long position the day after a down close and a short position the day after an
up close, emitting whatever order moves the current position to that target (the engine's
accounting handles flips). It remembers only the previous close — its own state, never
the context's history.
"""

from __future__ import annotations

from tessera.core.events import Bar, Event
from tessera.strategy.base import Context, Order


class Reversal:
    """Go long after a down day, short after an up day."""

    def __init__(self, qty: float = 100.0) -> None:
        self.qty = qty
        self._prev_close: float | None = None

    def on_event(self, event: Event, ctx: Context) -> list[Order]:
        if not isinstance(event, Bar):
            return []
        prev = self._prev_close
        self._prev_close = event.close
        if prev is None or event.close == prev:
            return []  # need a prior close, and a flat day is no signal

        target = self.qty if event.close < prev else -self.qty
        delta = target - ctx.position(event.symbol)
        if delta > 0.0:
            return [Order(event.symbol, +1, delta, "market", tag="reversal_long")]
        if delta < 0.0:
            return [Order(event.symbol, -1, -delta, "market", tag="reversal_short")]
        return []
