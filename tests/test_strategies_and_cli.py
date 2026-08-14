"""Tests for the example strategies, the CSV loader, and the CLI (Task 8).

Covers the acceptance: strategies keep their own rolling state and trade as specified,
the CSV loader converts dates to integer-ns bars, and `tessera run` produces a run
directory that `verify` reproduces.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from tessera.core.events import Bar, Event, Trade
from tessera.core.queue import merge
from tessera.data.sources.csv_bars import CsvBarSource, to_epoch_ns
from tessera.runner.cli import app, run_from_config
from tessera.runner.manifest import read_manifest, verify
from tessera.strategy.base import Context, Order
from tessera.strategy.examples.ma_crossover import MaCrossover
from tessera.strategy.examples.reversal import Reversal

DAY = 86_400_000_000_000


def _play(strategy: object, closes: list[float]) -> list[list[Order]]:
    """Run a strategy over synthetic bars, tracking position so Context is realistic."""
    out: list[list[Order]] = []
    pos = 0.0
    for i, close in enumerate(closes):
        bar: Event = Bar(i * DAY, "AAPL", close, close, close, close, 1.0)
        ctx = Context(ts=bar.ts, cash=100_000.0, positions={"AAPL": pos})
        orders = strategy.on_event(bar, ctx)  # type: ignore[attr-defined]
        for o in orders:
            pos += o.side * o.qty
        out.append(orders)
    return out


def test_ma_crossover_goes_long_then_flat() -> None:
    strat = MaCrossover(fast=2, slow=4, target_frac=0.10, initial_cash=100_000.0)
    # rising then falling closes: fast crosses above slow, then back below.
    orders = _play(strat, [10, 10, 10, 10, 12, 14, 16, 8, 6, 4])
    flat = [o for step in orders for o in step]
    sides = [o.side for o in flat]
    assert +1 in sides and -1 in sides  # bought, then exited to flat
    # Entry is sized to notional/price: fast(2)MA crosses above slow(4)MA at close 12,
    # so the buy is 10_000 / 12 shares, not a fixed 100.
    entry = next(o for o in flat if o.side == +1)
    assert entry.qty == pytest.approx(10_000.0 / 12.0)


def test_reversal_flips_long_to_short_with_delta_sizing() -> None:
    frac, cash = 0.10, 100_000.0
    notional = frac * cash  # 10_000
    orders = _play(Reversal(target_frac=frac, initial_cash=cash), [10.0, 9.0, 11.0])
    # day1 (9<10, down) -> open long of notional/9 shares.
    assert orders[1][0].side == +1
    long_qty = notional / 9.0
    assert orders[1][0].qty == pytest.approx(long_qty)
    # day2 (11>9, up) -> flip to short notional/11. The single order must CLOSE the long AND
    # OPEN the short, i.e. its size is the zero-crossing delta long_qty + short_target.
    assert orders[2][0].side == -1
    short_target = notional / 11.0
    assert orders[2][0].qty == pytest.approx(long_qty + short_target)


def test_csv_loader_stamps_bars_at_session_close(tmp_path: Path) -> None:
    csv = tmp_path / "AAPL.csv"
    # A winter date (EST) and a summer date (EDT): the fix must get the DST shift right.
    csv.write_text(
        "Date,Open,High,Low,Close,Volume\n"
        "2020-01-02,10,11,9,10.5,1000\n"
        "2020-07-01,10,11,9,10.5,1000\n"
    )
    bars = list(CsvBarSource(csv, "AAPL").events())
    assert len(bars) == 2
    assert all(isinstance(b.ts, int) for b in bars)
    # Both code paths agree: the vectorised loader and the scalar to_epoch_ns helper.
    assert bars[0].ts == to_epoch_ns("2020-01-02")
    assert bars[1].ts == to_epoch_ns("2020-07-01")
    # The stamp is the 16:00 ET session close, NOT UTC midnight (D41): 21:00 UTC in
    # winter, 20:00 UTC in summer — the one-hour DST difference the fix has to handle.
    def _utc(ns: int) -> pd.Timestamp:
        return pd.Timestamp(ns, unit="ns", tz="UTC")

    assert _utc(bars[0].ts) == pd.Timestamp("2020-01-02 21:00", tz="UTC")
    assert _utc(bars[1].ts) == pd.Timestamp("2020-07-01 20:00", tz="UTC")
    assert bars[0].close == 10.5


def test_daily_bar_does_not_leak_ahead_of_same_day_intraday(tmp_path: Path) -> None:
    """The D41 leak, closed: a daily bar carries the day's close, so once merged with
    that day's intraday ticks it must sort AFTER them, not before. Under the old midnight
    stamp the bar (00:00 UTC) preceded every tick — a future leak on the close."""
    csv = tmp_path / "AAPL.csv"
    csv.write_text("Date,Open,High,Low,Close,Volume\n2020-01-02,10,11,9,10.5,1000\n")
    daily = list(CsvBarSource(csv, "AAPL").events())

    def _et(t: str) -> int:  # a same-session intraday instant, ET -> UTC ns
        return int(pd.Timestamp(f"2020-01-02 {t}", tz="America/New_York").value)

    intraday = [
        Trade(_et("10:00"), "AAPL", 10.2, 100.0, +1),
        Trade(_et("15:00"), "AAPL", 10.4, 100.0, -1),
    ]
    merged = list(merge([daily, intraday]))
    # Both trades come first; the bar (the close) is last. No look-ahead.
    assert [type(e).__name__ for e in merged] == ["Trade", "Trade", "Bar"]
    assert merged[-1].ts > merged[0].ts


def _write_prices(path: Path, closes: list[float]) -> None:
    lines = ["Date,Open,High,Low,Close,Volume"]
    for i, c in enumerate(closes):
        day = f"2020-01-{i + 1:02d}"
        lines.append(f"{day},{c},{c + 1},{c - 1},{c},1000")
    path.write_text("\n".join(lines) + "\n")


def test_cli_run_produces_a_verifiable_run_directory(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _write_prices(data_dir / "AAPL.csv", [10, 9, 11, 8, 12, 7, 13, 6])

    result = CliRunner().invoke(
        app,
        [
            "run",
            "--strategy", "reversal",
            "--symbol", "AAPL",
            "--start", "2020-01-01",
            "--end", "2020-12-31",
            "--data-dir", str(data_dir),
            "--cost-bps", "5",
            "--out", str(tmp_path / "runs"),
        ],
    )
    assert result.exit_code == 0, result.output

    run_dir = Path(result.output.strip())
    assert (run_dir / "manifest.json").exists()
    for name in ("fills.parquet", "orders.parquet", "portfolio.parquet"):
        assert (run_dir / name).exists()

    manifest = read_manifest(run_dir)
    assert manifest["config"]["strategy"] == "reversal"
    # The run reproduces exactly.
    assert verify(run_dir, run_from_config) is True
