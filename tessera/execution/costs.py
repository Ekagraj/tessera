"""Owns BpsCostModel: a fixed basis-points charge on traded notional.

The naive cost: `bps` basis points of the fill's notional (`fill_price x qty`),
charged as a positive drag on every fill regardless of size or direction. This lies
about reality — real costs are nonlinear in size and vary with liquidity and
volatility — but it makes transaction-cost sensitivity a single, legible knob.
"""

from __future__ import annotations

from tessera.strategy.base import Order


class BpsCostModel:
    """Charge `bps` basis points of notional on each fill (1 bp = 0.01%)."""

    def __init__(self, bps: float) -> None:
        self.bps = bps

    def cost(self, order: Order, fill_price: float, qty: float) -> float:
        return self.bps * 1e-4 * fill_price * abs(qty)
