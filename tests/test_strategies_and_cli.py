"""Tests for the example strategies, the CSV loader, and the CLI (Task 8).

Covers the acceptance: strategies keep their own rolling state and trade as specified,
the CSV loader converts dates to integer-ns bars, and `tessera run` produces a run
directory that `verify` reproduces.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from tessera.core.events import Bar, Event
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
    strat = MaCrossover(fast=2, slow=4)
    # rising then falling closes: fast crosses above slow, then back below.
    orders = _play(strat, [10, 10, 10, 10, 12, 14, 16, 8, 6, 4])
    sides = [o.side for step in orders for o in step]
    assert +1 in sides and -1 in sides  # bought, then exited to flat


def test_reversal_buys_after_down_sells_after_up() -> None:
    orders = _play(Reversal(qty=100.0), [10.0, 9.0, 11.0])
    # day1 (9<10, down) -> long; day2 (11>9, up) -> flip to short
    assert orders[1][0].side == +1
    assert orders[2][0].side == -1


def test_csv_loader_converts_dates_to_int_ns(tmp_path: Path) -> None:
    csv = tmp_path / "AAPL.csv"
    csv.write_text("Date,Open,High,Low,Close,Volume\n2020-01-02,10,11,9,10.5,1000\n")
    bars = list(CsvBarSource(csv, "AAPL").events())
    assert len(bars) == 1
    assert bars[0].ts == to_epoch_ns("2020-01-02")
    assert isinstance(bars[0].ts, int)
    assert bars[0].close == 10.5


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
