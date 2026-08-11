"""Tests for the engine loop (tessera.core.engine).

Covers the Task-6 acceptance: an end-to-end run completes on synthetic data, an order
decided on bar N fills at bar N+1's open (never the same bar), and the portfolio
snapshot reflects fills and marks.
"""

from __future__ import annotations

from typing import Any

from tessera.core.engine import run
from tessera.core.events import Bar, Event
from tessera.execution.costs import BpsCostModel
from tessera.execution.naive import NaiveFillModel
from tessera.portfolio.book import Book
from tessera.strategy.base import Context, Order

DAY = 86_400_000_000_000


class CollectingRecorder:
    """In-memory recorder: keeps every (kind, payload) for inspection."""

    def __init__(self) -> None:
        self.records: list[tuple[str, dict[str, Any]]] = []

    def record(self, kind: str, payload: dict[str, Any]) -> None:
        self.records.append((kind, dict(payload)))

    def close(self) -> None:
        pass

    def of_kind(self, kind: str) -> list[dict[str, Any]]:
        return [p for k, p in self.records if k == kind]


class BuyOnFirstBar:
    """Emits a single market buy on the first event it sees, then nothing."""

    def __init__(self) -> None:
        self._done = False

    def on_event(self, event: Event, ctx: Context) -> list[Order]:
        if self._done:
            return []
        self._done = True
        return [Order(event.symbol, +1, 100.0, "market", tag="entry")]


def _bars(opens: list[float], symbol: str = "AAPL") -> list[Bar]:
    # close = open + 0.5, so a same-bar fill would be detectable by price.
    return [
        Bar(i * DAY, symbol, o, o + 1.0, o - 1.0, o + 0.5, 1_000.0)
        for i, o in enumerate(opens)
    ]


def test_end_to_end_run_completes_and_records() -> None:
    rec = CollectingRecorder()
    book = Book(cash=100_000.0)
    fm = NaiveFillModel(cost_model=BpsCostModel(0.0))
    run(_bars([10.0, 11.0, 12.0]), BuyOnFirstBar(), fm, book, rec)

    # One portfolio snapshot per bar.
    assert len(rec.of_kind("portfolio")) == 3
    assert len(rec.of_kind("order")) == 1
    assert len(rec.of_kind("fill")) == 1


def test_order_on_bar0_fills_at_bar1_open() -> None:
    rec = CollectingRecorder()
    book = Book(cash=100_000.0)
    fm = NaiveFillModel(cost_model=BpsCostModel(0.0))
    run(_bars([10.0, 11.0, 12.0]), BuyOnFirstBar(), fm, book, rec)

    fill = rec.of_kind("fill")[0]
    assert fill["price"] == 11.0  # bar1 open, NOT bar0 close (10.5)
    assert fill["ts"] == 1 * DAY
    # After the fill the book holds 100 shares at the fill price.
    assert book.position("AAPL").qty == 100.0
    assert book.position("AAPL").avg_price == 11.0


def test_final_equity_reflects_fill_and_mark() -> None:
    rec = CollectingRecorder()
    book = Book(cash=100_000.0)
    fm = NaiveFillModel(cost_model=BpsCostModel(0.0))
    run(_bars([10.0, 11.0, 12.0]), BuyOnFirstBar(), fm, book, rec)

    # Bought 100 @ 11.0 (cost 1100 cash); last mark is bar2 close = 12.5.
    last = rec.of_kind("portfolio")[-1]
    assert last["cash"] == 100_000.0 - 1100.0
    assert last["equity"] == (100_000.0 - 1100.0) + 100.0 * 12.5
    assert last["unrealized_pnl"] == 100.0 * (12.5 - 11.0)
