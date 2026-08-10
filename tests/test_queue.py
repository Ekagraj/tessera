"""Tests for the ordered event queue (tessera.core.queue).

Covers the Task-2 acceptance points: three sources unsorted relative to each other
merge into one correctly ordered stream, identical timestamps break deterministically
by source priority, out-of-order source data raises, and the merge is lazy (so memory
stays flat rather than materialising every event).
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator

import pytest

from tessera.core.events import Bar, Event
from tessera.core.queue import QueueError, merge


def _bars(symbol: str, timestamps: list[int]) -> Iterator[Event]:
    for ts in timestamps:
        yield Bar(ts, symbol, 1.0, 1.0, 1.0, 1.0, 1.0)


def test_merges_three_sources_into_one_ordered_stream() -> None:
    a = _bars("A", [1, 4, 7])
    b = _bars("B", [2, 5, 8])
    c = _bars("C", [3, 6, 9])
    out = list(merge([a, b, c]))
    assert [e.ts for e in out] == [1, 2, 3, 4, 5, 6, 7, 8, 9]


def test_identical_timestamps_break_by_source_priority() -> None:
    a = _bars("A", [5, 5])  # priority 0
    b = _bars("B", [5])  # priority 1
    out = list(merge([a, b]))
    # Both A events (in seq order) come before B, since priority 0 < 1.
    assert [e.symbol for e in out] == ["A", "A", "B"]


def test_out_of_order_within_a_source_raises() -> None:
    with pytest.raises(QueueError):
        list(merge([_bars("A", [1, 3, 2])]))


def test_merge_is_lazy_and_flat_memory() -> None:
    # Two infinite sources. If the merge tried to read everything up front this
    # would never return; islice proves it streams with O(k) memory.
    evens = (Bar(ts, "E", 1.0, 1.0, 1.0, 1.0, 1.0) for ts in itertools.count(0, 2))
    odds = (Bar(ts, "O", 1.0, 1.0, 1.0, 1.0, 1.0) for ts in itertools.count(1, 2))
    first_ten = list(itertools.islice(merge([evens, odds]), 10))
    assert [e.ts for e in first_ten] == list(range(10))


def test_empty_inputs_yield_nothing() -> None:
    assert list(merge([])) == []
    assert list(merge([_bars("A", [])])) == []
