"""Owns the position/cash book: current holdings, cash, and realized PnL.

The book is the mutable state of a run's portfolio. It applies fills one at a time
(so partial fills need no special handling) and tracks each position's quantity and
average cost. Realized PnL is computed on the average-cost basis: reducing or closing
a position realizes `(exit_price - avg_price) x closed_qty`; a fill that crosses
through zero is split into a closing portion (which realizes) and an opening portion
(which establishes a new basis at the fill price). Marking positions to market and
deriving equity/unrealized PnL live in `accounting.py`, not here.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Position:
    """A single symbol's holding: signed quantity and average cost per unit.

    `qty` is positive for long, negative for short. `avg_price` is the average cost
    of the currently-open position and is 0.0 when flat.
    """

    qty: float = 0.0
    avg_price: float = 0.0


@dataclass(slots=True)
class Book:
    """Cash, positions, and cumulative realized PnL for one run."""

    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    realized_pnl: float = 0.0

    def position(self, symbol: str) -> Position:
        """The current Position for `symbol` (a flat Position if none is held)."""
        return self.positions.get(symbol, Position())

    def apply_fill(self, symbol: str, qty: float, price: float, cost: float = 0.0) -> float:
        """Apply one fill and return the realized PnL it produced.

        `qty` is signed: positive buys, negative sells. `cost` is commission/fees on
        this fill (always a positive drag). Updates cash, the position's quantity and
        average cost, and the cumulative realized PnL.
        """
        pos = self.positions.get(symbol, Position())
        old_qty = pos.qty

        # Cash flow: buying spends cash, selling receives it; costs always drain.
        self.cash -= qty * price
        self.cash -= cost

        new_qty = old_qty + qty
        realized = 0.0

        opening_or_adding = old_qty == 0.0 or (old_qty > 0.0) == (qty > 0.0)
        if opening_or_adding:
            # Same direction (or from flat): blend into a new weighted-average cost.
            if new_qty != 0.0:
                pos.avg_price = (old_qty * pos.avg_price + qty * price) / new_qty
        else:
            # Opposing the existing position: realize PnL on the closed portion.
            closed_qty = min(abs(qty), abs(old_qty))
            direction = 1.0 if old_qty > 0.0 else -1.0
            realized = (price - pos.avg_price) * closed_qty * direction
            if abs(qty) > abs(old_qty):
                # Crossed through zero: the remainder opens a new position at price.
                pos.avg_price = price
            elif new_qty == 0.0:
                pos.avg_price = 0.0
            # (a partial close in the same direction keeps the existing avg_price)

        # Fees are expensed immediately (not capitalized into basis), so they count
        # as a realized cost. This keeps realized + unrealized == equity - initial.
        realized -= cost

        pos.qty = new_qty
        self.realized_pnl += realized

        if new_qty == 0.0:
            # Drop flat positions so they don't linger with a stale avg_price.
            self.positions.pop(symbol, None)
        else:
            self.positions[symbol] = pos

        return realized
