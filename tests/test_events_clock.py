"""Tests for event types (tessera.core.events) and the clock (tessera.core.clock).

Covers the two Task-1 acceptance points: the clock only moves forward (feeding
time out of order raises), and identical-timestamp events order deterministically.
"""

from __future__ import annotations

import random
from dataclasses import FrozenInstanceError

import pytest

from tessera.core.clock import Clock, ClockError
from tessera.core.events import Bar, Event, Quote, Trade, ordering_key


def test_clock_moves_forward_and_allows_equal_ts() -> None:
    c = Clock()
    assert not c.started
    c.advance(100)
    assert c.started
    assert c.ts == 100
    c.advance(100)  # many events can share one timestamp
    assert c.ts == 100
    c.advance(250)
    assert c.ts == 250


def test_clock_rejects_backward_time() -> None:
    c = Clock()
    c.advance(500)
    with pytest.raises(ClockError):
        c.advance(499)


def test_clock_ts_before_start_raises() -> None:
    c = Clock()
    with pytest.raises(ClockError):
        _ = c.ts


def test_clock_rejects_non_int_timestamp() -> None:
    c = Clock()
    with pytest.raises(TypeError):
        c.advance(100.0)  # type: ignore[arg-type]  # float ts is a boundary bug
    with pytest.raises(TypeError):
        c.advance(True)  # type: ignore[arg-type]  # bool sneaks through as int


def test_identical_timestamp_events_order_deterministically() -> None:
    # Three sources each emit an event at the SAME ts. source_priority is the
    # source's index in the run; seq is per-source read order.
    ts = 1_600_000_000_000_000_000
    keyed = [
        (ordering_key(ts, 0, 0), Bar(ts, "AAPL", 1.0, 1.0, 1.0, 1.0, 1.0)),
        (ordering_key(ts, 1, 0), Trade(ts, "AAPL", 10.0, 5.0, 1)),
        (ordering_key(ts, 2, 0), Quote(ts, "AAPL", 9.9, 1.0, 10.1, 1.0)),
    ]
    expected = [event for _, event in keyed]

    # Shuffling the input must never change the sorted output.
    for seed in range(25):
        shuffled = keyed[:]
        random.Random(seed).shuffle(shuffled)
        ordered = [event for _, event in sorted(shuffled, key=lambda ke: ke[0])]
        assert ordered == expected


def test_ordering_key_breaks_within_source_ties_by_seq() -> None:
    ts = 42
    keys = [ordering_key(ts, 0, s) for s in (2, 0, 1)]
    assert sorted(keys) == [(ts, 0, 0), (ts, 0, 1), (ts, 0, 2)]


def test_ordering_key_prefers_lower_source_priority() -> None:
    ts = 42
    assert ordering_key(ts, 0, 999) < ordering_key(ts, 1, 0)


def test_events_are_frozen_and_slotted() -> None:
    bar = Bar(1, "AAPL", 1.0, 2.0, 0.5, 1.5, 100.0)
    assert isinstance(bar, Event)
    with pytest.raises(FrozenInstanceError):
        bar.close = 2.0  # type: ignore[misc]  # frozen: mutation must fail
    assert not hasattr(bar, "__dict__")  # slots: no per-instance dict
