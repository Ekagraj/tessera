"""Owns the buy-and-hold benchmark strategy (buy once on the first bar, never sell).

The simplest possible strategy and the natural baseline every other strategy is measured
against: it goes fully invested on the first bar it sees and holds forever. It sizes at the
first bar's close (the only price it knows then); the fill lands at the next bar's open, so a
run's buy-and-hold equity reproduces the underlying's own return up to that single entry-fill
timing difference. Used by `tests/test_validation.py` to check the engine against an
independently computable ground truth.

Note: fully invested (`target_frac=1.0`) sized at the prior close can momentarily read above
1x gross at the next-open fill on a gap-up day, which the Task-12 leverage cap would reject.
That is an entry-timing artifact, not real leverage; the validation runs it with the cap
relaxed. Lower `target_frac` if running under the default 1x cap.
"""

from __future__ import annotations

from tessera.core.events import Bar, Event
from tessera.strategy.base import Context, Order


class BuyAndHold:
    """Buy on the first bar (sized to `target_frac` of capital) and hold forever."""

    def __init__(self, target_frac: float = 1.0, initial_cash: float = 100_000.0) -> None:
        self._notional = target_frac * initial_cash
        self._entered = False

    def on_event(self, event: Event, ctx: Context) -> list[Order]:
        if not isinstance(event, Bar) or self._entered:
            return []
        self._entered = True
        qty = self._notional / event.close  # size at the known close; fills at next open
        return [Order(event.symbol, +1, qty, "market", tag="buy_and_hold")]
