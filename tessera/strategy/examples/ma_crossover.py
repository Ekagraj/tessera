"""Owns the moving-average crossover example strategy (self-maintained rolling state).

Long when the fast simple moving average is above the slow one, flat otherwise. It keeps
its own two ring buffers of recent closes — it never asks the context for history, so it
cannot look ahead (ARCHITECTURE seam 2).

Sizing is **fixed-fractional notional**: a long position targets `target_frac` of the
account's starting capital (`initial_cash`), converted to shares at the current price the
event already carries (`qty = target_frac * initial_cash / close`). This keeps exposure
comparable across symbols regardless of price level — a fixed share count would make a $0.95
stock a 0.1% position and a $250 stock a 25% one, so results would measure price, not signal.
"""

from __future__ import annotations

from collections import deque

from tessera.core.events import Bar, Event
from tessera.strategy.base import Context, Order


class MaCrossover:
    """Buy when the fast SMA crosses above the slow SMA; go flat when it crosses back."""

    def __init__(
        self, fast: int, slow: int, target_frac: float = 0.10, initial_cash: float = 100_000.0
    ) -> None:
        if fast >= slow:
            raise ValueError(f"fast ({fast}) must be shorter than slow ({slow})")
        # Dollar exposure to target when long; converted to shares at order time.
        self._notional = target_frac * initial_cash
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
            qty = self._notional / event.close  # target notional -> shares at today's price
            return [Order(event.symbol, +1, qty, "market", tag="ma_cross_up")]
        if fast_ma <= slow_ma and held > 0.0:
            return [Order(event.symbol, -1, held, "market", tag="ma_cross_down")]
        return []
