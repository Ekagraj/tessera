"""Same config and seed run twice must produce identical records.

This is one of the three load-bearing tests. At the engine level it asserts that two
runs with identical inputs produce an identical record *stream*. Task 7's `verify`
checks on-disk reproducibility by comparing parquet **content** (`DataFrame.equals`),
not raw bytes: correct parquet files can differ in incidental metadata while holding
identical rows (see decisions.md D33).
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


class _Recorder:
    def __init__(self) -> None:
        self.records: list[tuple[str, dict[str, Any]]] = []

    def record(self, kind: str, payload: dict[str, Any]) -> None:
        self.records.append((kind, dict(payload)))

    def close(self) -> None:
        pass


class FlipFlop:
    """Buys when flat, sells when long — generates a steady stream of trades."""

    def on_event(self, event: Event, ctx: Context) -> list[Order]:
        held = ctx.position(event.symbol)
        if held == 0.0:
            return [Order(event.symbol, +1, 10.0, "market", tag="buy")]
        return [Order(event.symbol, -1, 10.0, "market", tag="sell")]


def _bars() -> list[Bar]:
    opens = [10.0, 11.0, 10.5, 12.0, 11.5, 13.0, 12.5, 14.0]
    return [
        Bar(i * DAY, "AAPL", o, o + 1.0, o - 1.0, o + 0.5, 1_000.0)
        for i, o in enumerate(opens)
    ]


def _one_run() -> list[tuple[str, dict[str, Any]]]:
    rec = _Recorder()
    fm = NaiveFillModel(cost_model=BpsCostModel(5.0))
    run(_bars(), FlipFlop(), fm, Book(cash=100_000.0), rec)
    return rec.records


def test_two_identical_runs_produce_identical_records() -> None:
    assert _one_run() == _one_run()


def test_records_are_non_trivial() -> None:
    # Guard against the test passing because nothing happened.
    records = _one_run()
    kinds = {kind for kind, _ in records}
    assert {"fill", "order", "portfolio"} <= kinds
