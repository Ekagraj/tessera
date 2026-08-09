"""Owns the event type hierarchy and the total order over co-timestamped events.

Events are frozen, slotted dataclasses carrying only primitive fields, so the
loop boundary stays narrow enough for a later Rust port (ARCHITECTURE seam 1 and
section 3). Timestamps are integer nanoseconds since the UTC epoch, always;
conversion from human time happens in the data layer, never here.

`BookUpdate` is intentionally absent: order-book replay is scheduled for later
(ARCHITECTURE section 6) and is not built speculatively.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Event:
    """Base of every event. `ts` is integer nanoseconds since the UTC epoch."""

    ts: int
    symbol: str


@dataclass(frozen=True, slots=True)
class Bar(Event):
    """An OHLCV bar for `symbol` over the interval ending at `ts`."""

    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class Trade(Event):
    """A single executed trade. `aggressor` is +1 for a buy, -1 for a sell."""

    price: float
    size: float
    aggressor: int


@dataclass(frozen=True, slots=True)
class Quote(Event):
    """A top-of-book quote: best bid/ask and their sizes."""

    bid: float
    bid_size: float
    ask: float
    ask_size: float


def ordering_key(ts: int, source_priority: int, seq: int) -> tuple[int, int, int]:
    """Return the total order key for an event: ``(ts, source_priority, seq)``.

    `ts` is the primary key. When two events share a timestamp, the source with
    the lower `source_priority` (its fixed index in the run's source list) comes
    first; within a single source, the lower `seq` (read order) comes first.

    Because this is a *total* order over all events, a merge is reproducible
    regardless of how a heap happens to break ties on equal keys. That is what
    makes cross-source ordering deterministic (ARCHITECTURE seam 9). The queue
    (built next) assigns `source_priority` and `seq` and sorts by this key.
    """
    return (ts, source_priority, seq)
