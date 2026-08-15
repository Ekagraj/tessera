"""Regression tests produced by the week-1 audit (docs/AUDIT.md).

Each test pins down a finding: a disproven bug (Sharpe), an untested invariant
(fill-quantity), a verify failure mode, a property-based accounting sweep, and the
documented week-1 limitations (no margin check; silent no-op runs).
"""

from __future__ import annotations

import math
import random
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from tessera.core.engine import run
from tessera.core.events import Bar
from tessera.execution.costs import BpsCostModel
from tessera.execution.naive import NaiveFillModel
from tessera.portfolio import accounting
from tessera.portfolio.book import Book
from tessera.strategy.base import Context, Order

DAY = 86_400_000_000_000


def _bar(i: int, o: float) -> Bar:
    return Bar(i * DAY, "AAPL", o, o + 1.0, o - 1.0, o + 0.5, 1_000.0)


class _Rec:
    def __init__(self) -> None:
        self.records: list[tuple[str, dict]] = []

    def record(self, kind: str, payload: dict) -> None:
        self.records.append((kind, payload))

    def close(self) -> None:
        pass


# --- 1A: the Sharpe is NOT double-annualised (hand-computed value) --------------

def test_sharpe_matches_hand_computed_value() -> None:
    # daily returns exactly [+0.10, +0.20, -0.10]
    equities = [100.0, 110.0, 132.0, 118.8]
    rets = [0.10, 0.20, -0.10]
    mean = sum(rets) / 3
    std = math.sqrt(sum((r - mean) ** 2 for r in rets) / 2)  # ddof=1
    expected = (mean / std) * math.sqrt(252)  # single sqrt(252)

    with tempfile.TemporaryDirectory() as d:
        pd.DataFrame(
            {
                "ts": [i * DAY for i in range(4)],
                "cash": equities,
                "equity": equities,
                "realized_pnl": [0.0] * 4,
                "unrealized_pnl": [0.0] * 4,
            }
        ).to_parquet(Path(d) / "portfolio.parquet", index=False)
        from tessera.metrics.returns import compute_metrics

        got = compute_metrics(d)["sharpe"]
    assert got == pytest.approx(expected)
    # A double-annualisation would be this instead; make sure we are NOT that.
    assert got != pytest.approx(expected * math.sqrt(252))


# --- Invariant 5: total fill quantity never exceeds order quantity --------------

def test_order_fills_at_most_once_and_quantity_conserved() -> None:
    fm = NaiveFillModel(BpsCostModel(0.0))
    fm.submit(Order("AAPL", +1, 100.0, "market"), 0 * DAY)
    first = fm.on_event(_bar(1, 11.0))
    second = fm.on_event(_bar(2, 12.0))
    assert sum(f.qty for f in first) == 100.0  # exactly the order qty
    assert second == []  # consumed from the pending queue; cannot refill


def test_order_on_final_bar_never_fills_and_is_dropped_silently() -> None:
    class BuyOnBar:
        def __init__(self, target: int) -> None:
            self.i = 0
            self.target = target

        def on_event(self, event: Bar, ctx: Context) -> list[Order]:
            self.i += 1
            return [Order("AAPL", +1, 10.0, "market")] if self.i == self.target else []

    rec = _Rec()
    bars = [_bar(0, 10.0), _bar(1, 11.0), _bar(2, 12.0)]
    run(bars, BuyOnBar(3), NaiveFillModel(BpsCostModel(0.0)), Book(100_000.0), rec)
    kinds = [k for k, _ in rec.records]
    assert kinds.count("order") == 1
    assert kinds.count("fill") == 0  # never fills — no later bar
    # Documented limitation: no 'cancel'/'reject' record is emitted for it.
    assert "reject" not in kinds and "cancel" not in kinds


# --- Invariant 3: identities hold over random fill sequences (property-based) ----

def test_accounting_identities_hold_over_random_fill_sequences() -> None:
    initial = 100_000.0
    for seed in range(300):
        rng = random.Random(seed)
        book = Book(cash=initial)
        price = 100.0
        for _ in range(rng.randint(1, 25)):
            price = max(1.0, price + rng.uniform(-5.0, 5.0))
            qty = rng.choice([-1.0, 1.0]) * rng.uniform(1.0, 50.0)
            book.apply_fill("AAPL", qty, price, cost=rng.uniform(0.0, 2.0))
            prices = {"AAPL": price}
            eq = accounting.equity(book, prices)
            assert eq == pytest.approx(book.cash + accounting.market_value(book, prices))
            assert book.realized_pnl + accounting.unrealized_pnl(book, prices) == pytest.approx(
                eq - initial
            )


# --- Invariant 6: verify() catches a changed input file -------------------------

def test_verify_false_when_input_csv_changes(tmp_path: Path) -> None:
    from tessera.runner.cli import run_from_config
    from tessera.runner.config import RunConfig
    from tessera.runner.manifest import data_hash, verify, write_manifest
    from tessera.runner.recorder import ParquetRecorder

    data = tmp_path / "data"
    data.mkdir()
    csv = data / "AAPL.csv"
    csv.write_text(
        "Date,Open,High,Low,Close,Volume\n"
        + "".join(f"2020-01-{i + 1:02d},{10 + i % 3},12,9,{10 + i % 3},1000\n" for i in range(8))
    )
    cfg = RunConfig(
        strategy="reversal", symbols=["AAPL"], start_ts=0, end_ts=10**19,
        data_source=str(data), seed=0, initial_cash=100_000.0, cost_bps=5.0,
    )
    rd = tmp_path / "run"
    rec = ParquetRecorder(rd)
    run_from_config(cfg, rec)
    rec.close()
    write_manifest(rd, cfg, input_hash=data_hash([csv]), timings={"wall_seconds": 0.0})

    assert verify(rd, run_from_config) is True
    csv.write_text(csv.read_text().replace("10", "20"))  # change the data underneath
    assert verify(rd, run_from_config) is False


# --- Part 4: documented week-1 limitations --------------------------------------

class _OrderOnce:
    """Emits one huge market order on the first bar, then nothing. `side` picks buy/short."""

    def __init__(self, side: int) -> None:
        self._side = side

    def on_event(self, event: Bar, ctx: Context) -> list[Order]:
        if event.ts == 0:
            return [Order("AAPL", self._side, 1_000_000.0, "market")]  # ~10M notional @ ~10
        return []


def _run_attack(side: int) -> tuple[Book, list[tuple[str, dict]]]:
    rec = _Rec()
    book = Book(cash=100_000.0)  # default max_leverage 1.0
    # Order on bar 0 fills against bar 1's open (~10) -> ~10M gross on a 100k account.
    fm = NaiveFillModel(BpsCostModel(0.0))
    run([_bar(0, 10.0), _bar(1, 10.0)], _OrderOnce(side), fm, book, rec)
    return book, rec.records


def test_leverage_attack_is_rejected_long_and_short() -> None:
    # D43 regression for the exact vector the audit found (this replaces the old
    # test_no_margin_check_cash_can_go_negative, which asserted the *bug*). A strategy that
    # tries to buy 10M of notional on 100k — and its mirror that shorts 10M — must both be
    # rejected, emit a reject record, apply no fill, and leave equity untouched at 100k.
    for side in (+1, -1):
        book, records = _run_attack(side)
        kinds = [k for k, _ in records]
        assert kinds.count("reject") == 1, f"side={side}: {kinds}"
        assert "fill" not in kinds, f"side={side}: a fill was applied despite over-leverage"
        reject = next(p for k, p in records if k == "reject")
        assert reject["reason"] == "max_leverage"
        assert reject["side"] == side
        # No fill applied: cash and equity are exactly the starting 100k.
        assert book.cash == pytest.approx(100_000.0)
        assert accounting.equity(book, {"AAPL": 10.0}) == pytest.approx(100_000.0)
        assert book.positions == {}


def test_empty_event_stream_completes_without_error() -> None:
    # Documented limitation: a run over zero events succeeds silently (no records,
    # no error). Recorded so it is a known behaviour, not a surprise.
    rec = _Rec()

    class Noop:
        def on_event(self, event: Bar, ctx: Context) -> list[Order]:
            return []

    run([], Noop(), NaiveFillModel(BpsCostModel(0.0)), Book(100_000.0), rec)
    assert rec.records == []
