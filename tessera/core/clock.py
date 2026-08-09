"""Owns the simulated clock: monotonic time sourced only from the event stream.

The clock never reads the wall clock (ARCHITECTURE seam 9). Simulated time is
advanced by the engine as it pulls each event off the queue. Time may stay on the
same timestamp (many events can share a `ts`) but it can never move backward;
attempting that raises rather than silently reordering, because out-of-order time
would mean a strategy could observe the future.
"""

from __future__ import annotations


class ClockError(Exception):
    """Raised when time is read before it starts or asked to move backward."""


class Clock:
    """A monotonic non-decreasing simulated clock.

    Not started until the first `advance`. `ts` before then is an error rather
    than a silent default, so nothing can accidentally act as if time were zero.
    """

    __slots__ = ("_ts",)

    def __init__(self) -> None:
        self._ts: int | None = None

    @property
    def started(self) -> bool:
        """Whether any event has advanced the clock yet."""
        return self._ts is not None

    @property
    def ts(self) -> int:
        """Current simulated time in integer nanoseconds. Raises if not started."""
        if self._ts is None:
            raise ClockError("clock has not started; no event has been seen yet")
        return self._ts

    def advance(self, ts: int) -> None:
        """Move the clock to `ts`. Equal `ts` is allowed; a lower `ts` raises."""
        if isinstance(ts, bool) or not isinstance(ts, int):
            raise TypeError(f"timestamp must be int nanoseconds, got {type(ts).__name__}")
        if self._ts is not None and ts < self._ts:
            raise ClockError(f"time moved backward: {ts} < {self._ts}")
        self._ts = ts
