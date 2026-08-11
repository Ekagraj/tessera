"""Tests for the naive fill model and cost model (tessera.execution).

Covers the Task-5 acceptance points: an order submitted on bar N fills at bar N+1's
open (never bar N's close), latency beyond one bar delays the fill, fills only match
their own symbol, costs are charged in basis points, and limit orders fill only when
the next open crosses them.
"""

from __future__ import annotations

import pytest

from tessera.core.events import Bar, Quote
from tessera.execution.costs import BpsCostModel
from tessera.execution.naive import NaiveFillModel
from tessera.strategy.base import Order

DAY = 86_400_000_000_000  # one day in nanoseconds


def _bar(day: int, open_: float, symbol: str = "AAPL") -> Bar:
    # close deliberately differs from open so we can prove we never fill at the close.
    return Bar(day * DAY, symbol, open_, open_ + 1.0, open_ - 1.0, open_ + 0.5, 1_000.0)


def test_market_order_fills_at_next_open_not_current_close() -> None:
    fm = NaiveFillModel(cost_model=BpsCostModel(0.0))
    bar0 = _bar(0, 10.0)  # close is 10.5
    bar1 = _bar(1, 11.0)

    assert fm.on_event(bar0) == []  # nothing pending yet
    fm.submit(Order("AAPL", +1, 100.0, "market"), ts=bar0.ts)  # decided at bar0
    assert fm.on_event(bar0) == []  # must NOT fill on the same bar

    fills = fm.on_event(bar1)
    assert len(fills) == 1
    assert fills[0].price == 11.0  # next open, not bar0's close of 10.5
    assert fills[0].side == +1
    assert fills[0].qty == 100.0


def test_latency_beyond_one_bar_delays_the_fill() -> None:
    fm = NaiveFillModel(cost_model=BpsCostModel(0.0), latency_ns=DAY + DAY // 2)  # 1.5 days
    bar0, bar1, bar2 = _bar(0, 10.0), _bar(1, 11.0), _bar(2, 12.0)

    fm.submit(Order("AAPL", +1, 10.0, "market"), ts=bar0.ts)
    assert fm.on_event(bar1) == []  # arrival at 1.5 days not yet reached
    fills = fm.on_event(bar2)  # 2 days >= 1.5 days
    assert len(fills) == 1
    assert fills[0].price == 12.0  # fills at bar2's open


def test_fill_only_matches_its_own_symbol() -> None:
    fm = NaiveFillModel(cost_model=BpsCostModel(0.0))
    fm.submit(Order("AAPL", +1, 5.0, "market"), ts=_bar(0, 10.0).ts)
    assert fm.on_event(_bar(1, 50.0, symbol="MSFT")) == []  # wrong symbol
    fills = fm.on_event(_bar(1, 11.0, symbol="AAPL"))
    assert len(fills) == 1
    assert fills[0].symbol == "AAPL"


def test_cost_is_charged_in_basis_points() -> None:
    fm = NaiveFillModel(cost_model=BpsCostModel(10.0))  # 10 bps = 0.001
    fm.submit(Order("AAPL", +1, 100.0, "market"), ts=_bar(0, 10.0).ts)
    fills = fm.on_event(_bar(1, 11.0))
    assert fills[0].cost == pytest.approx(10.0 * 1e-4 * 11.0 * 100.0)  # 1.1


def test_non_bar_events_never_fill() -> None:
    fm = NaiveFillModel(cost_model=BpsCostModel(0.0))
    fm.submit(Order("AAPL", +1, 1.0, "market"), ts=_bar(0, 10.0).ts)
    quote = Quote(1 * DAY, "AAPL", 10.9, 1.0, 11.1, 1.0)
    assert fm.on_event(quote) == []  # a quote is not a tradeable open here


def test_limit_order_fills_only_when_open_crosses() -> None:
    # Buy limit at 11.5: next open 11.0 <= 11.5 -> crosses -> fills at the open.
    fm = NaiveFillModel(cost_model=BpsCostModel(0.0))
    fm.submit(Order("AAPL", +1, 10.0, "limit", limit_price=11.5), ts=_bar(0, 10.0).ts)
    fills = fm.on_event(_bar(1, 11.0))
    assert len(fills) == 1
    assert fills[0].price == 11.0

    # Buy limit at 10.5: next open 11.0 > 10.5 -> does not cross -> cancelled.
    fm2 = NaiveFillModel(cost_model=BpsCostModel(0.0))
    fm2.submit(Order("AAPL", +1, 10.0, "limit", limit_price=10.5), ts=_bar(0, 10.0).ts)
    assert fm2.on_event(_bar(1, 11.0)) == []
    assert fm2.on_event(_bar(2, 9.0)) == []  # one shot only: not retried later
