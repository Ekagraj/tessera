"""Owns the DataSource protocol: anything that yields a time-ordered Iterator[Event].

A data source is the one place human timestamps become integer nanoseconds — the
conversion boundary from ARCHITECTURE seam 1 / hard rule 2. Each source yields its own
events in non-decreasing `ts` order; the queue merges several sources into one stream.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from tessera.core.events import Event


class DataSource(Protocol):
    """A source of events for one symbol (or feed), already sorted by time."""

    def events(self) -> Iterator[Event]: ...
