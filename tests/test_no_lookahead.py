"""A strategy reaching beyond the current clock must raise, not silently succeed.

This is one of the three load-bearing tests. It proves the Task-3 seam: the Context
gives a strategy no channel to the future, no way to forge state, and a legitimate
strategy must maintain its *own* rolling window rather than asking for history.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator
from dataclasses import FrozenInstanceError

import pytest

from tessera.core.clock import Clock
from tessera.core.events import Bar, Event
from tessera.strategy.base import Context, Order, Strategy

# --- a tiny harness that plays bars through a strategy, building Context per event ---

def _bars(closes: list[float], symbol: str = "AAPL", start_ts: int = 1_000) -> Iterator[Bar]:
    for i, close in enumerate(closes):
        yield Bar(start_ts + i, symbol, close, close, close, close, 1_000.0)


def _run(
    strategy: Strategy,
    bars: Iterator[Bar],
    cash: float = 100_000.0,
    positions: dict[str, float] | None = None,
) -> list[Order]:
    clock = Clock()
    orders: list[Order] = []
    for bar in bars:
        clock.advance(bar.ts)
        ctx = Context(ts=clock.ts, cash=cash, positions=positions or {})
        orders.extend(strategy.on_event(bar, ctx))
    return orders


# --- cheating strategies: each must fail loudly ---------------------------------

class CheatByPeekingFuture:
    """Tries to reach a data feed / future bars through the context."""

    def on_event(self, event: Event, ctx: Context) -> list[Order]:
        _ = ctx.future_bars  # type: ignore[attr-defined]  # no such channel exists
        return []


class CheatByForgingCash:
    """Tries to rewrite cash to fake buying power."""

    def on_event(self, event: Event, ctx: Context) -> list[Order]:
        ctx.cash = 1_000_000_000.0  # type: ignore[misc]  # frozen -> raises
        return []


class CheatByMutatingPositions:
    """Tries to write a position directly instead of emitting an Order."""

    def on_event(self, event: Event, ctx: Context) -> list[Order]:
        ctx.positions["AAPL"] = 999.0  # type: ignore[index]  # read-only -> raises
        return []


def test_cheating_by_peeking_future_raises() -> None:
    with pytest.raises(AttributeError):
        _run(CheatByPeekingFuture(), _bars([10.0, 11.0]))


def test_cheating_by_forging_cash_raises() -> None:
    with pytest.raises(FrozenInstanceError):
        _run(CheatByForgingCash(), _bars([10.0, 11.0]))


def test_cheating_by_mutating_positions_raises() -> None:
    with pytest.raises(TypeError):
        _run(CheatByMutatingPositions(), _bars([10.0, 11.0]))


# --- a legitimate strategy: keeps its OWN rolling window -------------------------

class RollingMean:
    """Buys when price is above its own trailing average. Maintains its own deque;
    never asks the context for history."""

    def __init__(self, window: int) -> None:
        self.window = window
        self.prices: deque[float] = deque(maxlen=window)

    def on_event(self, event: Event, ctx: Context) -> list[Order]:
        assert isinstance(event, Bar)
        # A strategy can only ever act on the event it was handed and its own memory.
        assert ctx.ts == event.ts  # the context is exactly this instant, never ahead
        self.prices.append(event.close)
        if len(self.prices) < self.window:
            return []
        avg = sum(self.prices) / len(self.prices)
        if event.close > avg and ctx.position(event.symbol) == 0.0:
            return [Order(event.symbol, +1, 1.0, "market", tag="above_mean")]
        return []


def test_legitimate_rolling_strategy_works() -> None:
    strat = RollingMean(window=3)
    # rising series: once the window fills, price sits above its own average -> buys
    orders = _run(strat, _bars([10.0, 11.0, 12.0, 13.0]))
    assert len(orders) >= 1
    assert all(o.side == +1 and o.type == "market" for o in orders)


def test_rolling_strategy_satisfies_protocol() -> None:
    assert isinstance(RollingMean(3), Strategy)


# --- the snapshot guarantee -----------------------------------------------------

def test_context_is_an_immutable_snapshot() -> None:
    book = {"AAPL": 5.0}
    ctx = Context(ts=1_000, cash=100.0, positions=book)
    # Mutating the underlying book afterwards must not change the snapshot.
    book["AAPL"] = 999.0
    assert ctx.position("AAPL") == 5.0
    # And the exposed mapping itself is read-only.
    with pytest.raises(TypeError):
        ctx.positions["AAPL"] = 1.0  # type: ignore[index]
