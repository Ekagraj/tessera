"""Validate the engine against independently computable ground truth.

The project's actual claim is that the engine *reads the instrument correctly* — that a
backtest's equity curve is the arithmetic consequence of the bars, nothing more. These tests
check that against truth we can compute without the engine, not against published trader
returns (which are not reproducible from daily bars):

1. **Buy-and-hold vs pandas on real data.** A BuyAndHold run on each of the six symbols must
   reproduce the total return, annualized return, annualized vol, and max drawdown computed
   directly from the same bars in plain pandas. They match up to exactly one thing — the entry
   fills at the next bar's *open* rather than the first *close* — and that residual is asserted
   to be precisely the first overnight gap.
2. **Analytic cases with hand-derivable answers.** A constant-daily-return series where
   buy-and-hold equity must equal (1+r)^n exactly, and a fixed round trip whose PnL is
   arithmetic. Exact values, not approximate.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from tessera.core.engine import run
from tessera.core.events import Bar, Event
from tessera.data.sources.csv_bars import CsvBarSource, to_epoch_ns
from tessera.execution.costs import BpsCostModel
from tessera.execution.naive import NaiveFillModel
from tessera.metrics.returns import TRADING_DAYS, compute_metrics
from tessera.portfolio.book import Book
from tessera.runner.recorder import ParquetRecorder
from tessera.strategy.base import Order
from tessera.strategy.examples.buy_and_hold import BuyAndHold

DAY = 86_400_000_000_000
INITIAL = 100_000.0


def _synth_bar(k: int, o: float, c: float) -> Bar:
    """A synthetic bar at day k with open o, close c, and high/low bracketing them."""
    return Bar(k * DAY, "SYN", o, max(o, c), min(o, c), c, 1.0)
SYMBOLS = ["AAPL", "MSFT", "JPM", "XOM", "KO", "NVDA"]
DATA_DIR = Path("data")
# The buy-and-hold validation is about accounting, not the Task-12 leverage cap: a fully
# invested position sized at the prior close can read fractionally above 1x at the next-open
# fill on a gap-up day (an entry-timing artifact). Relax the cap so the entry always fills; the
# cap has its own tests in test_audit.py / test_accounting.py.
NO_CAP = 1e12


class _ListRecorder:
    """Collects records in memory (portfolio equity path is what the analytic tests need)."""

    def __init__(self) -> None:
        self.records: list[tuple[str, dict[str, Any]]] = []

    def record(self, kind: str, payload: dict[str, Any]) -> None:
        self.records.append((kind, dict(payload)))

    def close(self) -> None:
        pass

    def equity(self) -> list[float]:
        return [p["equity"] for k, p in self.records if k == "portfolio"]


# --------------------------------------------------------------------------------------------
# 1. Buy-and-hold on real data vs an independent pandas computation
# --------------------------------------------------------------------------------------------

def _load_bars(symbol: str) -> list[Bar]:
    src = CsvBarSource(
        DATA_DIR / f"{symbol}.csv", symbol, to_epoch_ns("2005-01-03"), to_epoch_ns("2024-12-31")
    )
    return list(src.events())


def _buy_and_hold_ground_truth(bars: list[Bar]) -> dict[str, float]:
    """Compute buy-and-hold metrics from the bars WITHOUT the engine, replicating only the
    documented mechanics: size at the first close, fill at the next open, mark at each close."""
    closes = np.array([b.close for b in bars], dtype=float)
    opens = np.array([b.open for b in bars], dtype=float)
    qty = INITIAL / closes[0]              # sized at the first close (what the strategy knows)
    cash_after = INITIAL - qty * opens[1]  # the buy fills at the SECOND bar's open, cost 0

    eq = np.empty(len(bars), dtype=float)
    eq[0] = INITIAL                         # bar 0: order placed, not yet filled -> flat
    eq[1:] = cash_after + qty * closes[1:]  # bar 1..: cash + position marked at the close

    rets = np.diff(eq) / eq[:-1]
    n = len(rets)
    dd = eq / np.maximum.accumulate(eq) - 1.0
    return {
        "total_return": eq[-1] / eq[0] - 1.0,
        "annualized_return": (eq[-1] / eq[0]) ** (TRADING_DAYS / n) - 1.0,
        "annualized_vol": float(rets.std(ddof=1) * math.sqrt(TRADING_DAYS)),
        "max_drawdown": float(dd.min()),
        # the residual the engine is "allowed" to differ from a naive close-to-close hold by:
        "naive_total_return": closes[-1] / closes[0] - 1.0,
        "entry_gap": (closes[0] - opens[1]) / closes[0],  # first overnight move, close_0 -> open_1
    }


@pytest.mark.skipif(not DATA_DIR.exists(), reason="real data/ not installed (gitignored)")
def test_buy_and_hold_matches_pandas_on_real_data(tmp_path: Path) -> None:
    for symbol in SYMBOLS:
        csv = DATA_DIR / f"{symbol}.csv"
        if not csv.exists():
            pytest.skip(f"{csv} missing")
        bars = _load_bars(symbol)
        assert len(bars) > 250, f"{symbol}: too few bars ({len(bars)})"

        run_dir = tmp_path / symbol
        rec = ParquetRecorder(run_dir)
        book = Book(cash=INITIAL, max_leverage=NO_CAP)
        run(bars, BuyAndHold(1.0, INITIAL), NaiveFillModel(BpsCostModel(0.0)), book, rec)
        rec.close()
        # The entry actually happened exactly once and was not rejected.
        counts = rec.record_counts()
        assert counts.get("fill") == 1, f"{symbol}: expected one entry fill, got {counts}"
        assert counts.get("reject", 0) == 0, f"{symbol}: entry was rejected"

        got = compute_metrics(run_dir)
        truth = _buy_and_hold_ground_truth(bars)

        # The engine reproduces the independent pandas computation to machine precision. A
        # larger gap on any of these is a finding, not a tolerance to widen.
        for key in ("total_return", "annualized_return", "annualized_vol", "max_drawdown"):
            assert got[key] == pytest.approx(truth[key], rel=1e-9, abs=1e-12), (
                f"{symbol}.{key}: engine={got[key]!r} truth={truth[key]!r}"
            )

        # And the ONLY discrepancy from a naive first-close buy-and-hold is the entry fill
        # landing at the next open — precisely the first overnight gap, nothing else.
        residual = got["total_return"] - truth["naive_total_return"]
        assert residual == pytest.approx(truth["entry_gap"], rel=1e-9, abs=1e-12), (
            f"{symbol}: residual {residual!r} != first overnight gap {truth['entry_gap']!r}"
        )


# --------------------------------------------------------------------------------------------
# 2. Analytic cases with hand-derivable answers (exact, not approximate)
# --------------------------------------------------------------------------------------------

def test_constant_return_buy_and_hold_equals_compound_exactly() -> None:
    # A series that closes exactly r higher every day, with each open equal to the prior close
    # (no gap), so a fully invested buy-and-hold has zero residual cash and its equity MUST be
    # initial * (1+r)^k at every bar k.
    r, n, p0 = 0.01, 10, 100.0
    closes = [p0 * (1.0 + r) ** k for k in range(n + 1)]
    opens = [p0] + [closes[k - 1] for k in range(1, n + 1)]
    bars: list[Event] = [_synth_bar(k, opens[k], closes[k]) for k in range(n + 1)]

    rec = _ListRecorder()
    run(bars, BuyAndHold(1.0, INITIAL), NaiveFillModel(BpsCostModel(0.0)), Book(INITIAL), rec)
    eq = rec.equity()

    assert len(eq) == n + 1
    for k in range(n + 1):
        assert eq[k] == pytest.approx(INITIAL * (1.0 + r) ** k, rel=1e-12)
    assert eq[-1] == pytest.approx(INITIAL * (1.0 + r) ** n, rel=1e-12)


class _RoundTrip:
    """Buy `qty` on the first bar, sell it two bars later. Everything else arithmetic."""

    def __init__(self, qty: float) -> None:
        self._qty = qty
        self._n = -1

    def on_event(self, event: Event, ctx: Any) -> list[Order]:
        self._n += 1
        if self._n == 0:
            return [Order(event.symbol, +1, self._qty, "market")]
        if self._n == 2:
            return [Order(event.symbol, -1, self._qty, "market")]
        return []


def test_fixed_round_trip_pnl_is_exact_arithmetic() -> None:
    # Buy 10 @ 100 (fills at bar 1's open), sell 10 @ 110 (fills at bar 3's open). PnL is
    # 10 * (110 - 100) = 100 exactly; cash and equity are integer-exact.
    qty, p_in, p_out = 10.0, 100.0, 110.0
    opens = [100.0, p_in, 105.0, p_out, 110.0]
    closes = [100.0, 105.0, 108.0, 110.0, 110.0]
    bars: list[Event] = [_synth_bar(k, opens[k], closes[k]) for k in range(5)]

    rec = _ListRecorder()
    book = Book(cash=INITIAL)
    run(bars, _RoundTrip(qty), NaiveFillModel(BpsCostModel(0.0)), book, rec)

    expected_pnl = qty * (p_out - p_in)  # 100.0, exact
    assert book.realized_pnl == expected_pnl
    assert book.cash == INITIAL - qty * p_in + qty * p_out  # 100_100.0
    assert book.cash == INITIAL + expected_pnl
    assert book.positions == {}  # flat after the round trip
    assert rec.equity()[-1] == INITIAL + expected_pnl  # final equity == starting + PnL

    fills = [p for k, p in rec.records if k == "fill"]
    assert len(fills) == 2
    assert (fills[0]["side"], fills[0]["qty"], fills[0]["price"]) == (+1, qty, p_in)
    assert (fills[1]["side"], fills[1]["qty"], fills[1]["price"]) == (-1, qty, p_out)
