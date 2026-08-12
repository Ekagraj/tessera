"""Owns the moving-average crossover example strategy (self-maintained rolling state).

Long when the fast simple moving average is above the slow one, flat otherwise. It keeps
its own two ring buffers of recent closes — it never asks the context for history, so it
cannot look ahead (ARCHITECTURE seam 2).
"""

from __future__ import annotations

from collections import deque

from tessera.core.events import Bar, Event
from tessera.strategy.base import Context, Order


class MaCrossover:
    """Buy when the fast SMA crosses above the slow SMA; go flat when it crosses back."""

    def __init__(self, fast: int, slow: int, qty: float = 100.0) -> None:
        if fast >= slow:
            raise ValueError(f"fast ({fast}) must be shorter than slow ({slow})")
        self.qty = qty
        self._fast: deque[float] = deque(maxlen=fast)
        self._slow: deque[float] = deque(maxlen=slow)

    def on_event(self, event: Event, ctx: Context) -> list[Order]:
        if not isinstance(event, Bar):
            return []
        self._fast.append(event.close)
        self._slow.append(event.close)
        if len(self._slow) < self._slow.maxlen:  # type: ignore[operator]
            return []  # not enough history yet

        fast_ma = sum(self._fast) / len(self._fast)
        slow_ma = sum(self._slow) / len(self._slow)
        held = ctx.position(event.symbol)

        if fast_ma > slow_ma and held == 0.0:
            return [Order(event.symbol, +1, self.qty, "market", tag="ma_cross_up")]
        if fast_ma <= slow_ma and held > 0.0:
            return [Order(event.symbol, -1, held, "market", tag="ma_cross_down")]
        return []
