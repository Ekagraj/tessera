"""Owns the ordered event queue: merges per-source-sorted streams into one stream.

The merge is a k-way heap merge over sources that are each already sorted by
non-decreasing `ts` (a CSV of daily bars is chronological; a live feed arrives in
order by nature). Only one pending event per source sits in the heap at a time, so
memory is O(k) in the number of sources rather than O(N) in total events — the
property that lets this scale to tens of millions of events and generalise to live
streaming later (a live feed is just another sorted iterator).

Ordering follows `ordering_key` from events.py, which is a *total* order, so the
merged stream is deterministic. A source that yields an event earlier than its own
previous event is a data bug and raises `QueueError` immediately rather than being
silently reordered.
"""

from __future__ import annotations

import heapq
from collections.abc import Iterable, Iterator

from tessera.core.events import Event, ordering_key

# One heap entry: (ordering_key, event). The key is globally unique — source
# priority differs across sources, seq differs within a source — so two entries
# never tie on the key, and the non-comparable Event is never compared.
_Entry = tuple[tuple[int, int, int], Event]


class QueueError(Exception):
    """Raised when a source yields events out of its own non-decreasing ts order."""


def _push_next(
    heap: list[_Entry],
    it: Iterator[Event],
    priority: int,
    last_ts: list[int | None],
    seq: list[int],
) -> None:
    """Pull the next event from source `priority` and push it onto the heap.

    Validates that the source is internally sorted: an event whose `ts` is below
    the source's previous `ts` raises `QueueError`. A exhausted source pushes
    nothing, so it simply drops out of the merge.
    """
    event = next(it, None)
    if event is None:
        return
    prev = last_ts[priority]
    if prev is not None and event.ts < prev:
        raise QueueError(
            f"source {priority} yielded ts {event.ts} after {prev}; "
            "each source must be sorted by non-decreasing ts"
        )
    last_ts[priority] = event.ts
    key = ordering_key(event.ts, priority, seq[priority])
    seq[priority] += 1
    heapq.heappush(heap, (key, event))


def merge(sources: Iterable[Iterable[Event]]) -> Iterator[Event]:
    """Merge one or more per-source-sorted event streams into one ordered stream.

    `source_priority` is each source's position in `sources` and breaks ties
    between sources sharing a timestamp; `seq` is a per-source counter breaking
    ties within one source. The result is yielded lazily, so the caller can stop
    early and the heap never holds more than one event per source.
    """
    iterators = [iter(s) for s in sources]
    heap: list[_Entry] = []
    last_ts: list[int | None] = [None] * len(iterators)
    seq: list[int] = [0] * len(iterators)

    # Prime the heap with the first event from each source.
    for priority, it in enumerate(iterators):
        _push_next(heap, it, priority, last_ts, seq)

    while heap:
        key, event = heapq.heappop(heap)
        priority = key[1]
        yield event
        _push_next(heap, iterators[priority], priority, last_ts, seq)
