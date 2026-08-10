"""Owns the strategy surface: the `Order` intent, the `Strategy` protocol, and the
read-only `Context` (ARCHITECTURE seams 2 and 3).

This is the seam that makes look-ahead *structurally impossible*. A strategy only
ever receives `on_event(event, ctx)`: one event, plus a context that holds only
present-time state and carries no reference to the data source, the queue, or any
future event. There is nothing future to reach, so it cannot be reached. A strategy
that needs history (e.g. a 50-day average) maintains its own rolling state as
instance attributes, updated as events arrive — it is never handed a window of bars,
because that window would contain the future.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from tessera.core.events import Event


@dataclass(frozen=True, slots=True)
class Order:
    """A strategy's *intent* to trade. The engine — never the strategy — decides the
    fill price, cost, and resulting position (ARCHITECTURE seam 3)."""

    symbol: str
    side: int  # +1 buy, -1 sell
    qty: float
    type: str  # "market" | "limit"
    limit_price: float | None = None
    tag: str = ""  # free-form, for attribution


@dataclass(frozen=True, slots=True)
class Context:
    """A read-only, point-in-time snapshot handed to the strategy each event.

    Built fresh per event and frozen, so it reflects exactly one instant and cannot
    be tampered with. `positions` is snapshotted and wrapped read-only, so a strategy
    can neither mutate the book through it nor see it change after the fact.
    """

    ts: int
    cash: float
    positions: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Copy then wrap read-only: a true immutable snapshot of this instant.
        object.__setattr__(self, "positions", MappingProxyType(dict(self.positions)))

    def position(self, symbol: str) -> float:
        """Current signed quantity held in `symbol`; 0.0 when flat."""
        return self.positions.get(symbol, 0.0)


@runtime_checkable
class Strategy(Protocol):
    """Reacts to one event at a time and emits orders. Holds its own rolling state."""

    def on_event(self, event: Event, ctx: Context) -> list[Order]: ...
