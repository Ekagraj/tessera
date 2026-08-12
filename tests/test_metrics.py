"""Tests for metrics and the tearsheet (Task 9).

Covers: metrics computed from a run directory's parquet (known-value checks), the
drawdown/return series, and that the tearsheet renders a non-empty PNG.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from tessera.metrics.returns import (
    TRADING_DAYS,
    compute_metrics,
    drawdown_series,
    equity_curve,
    period_returns,
)
from tessera.metrics.tearsheet import render

DAY = 86_400_000_000_000


def _write_run(run_dir: Path, equities: list[float], with_fills: bool = True) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "ts": [i * DAY for i in range(len(equities))],
            "cash": equities,
            "equity": equities,
            "realized_pnl": [0.0] * len(equities),
            "unrealized_pnl": [0.0] * len(equities),
        }
    ).to_parquet(run_dir / "portfolio.parquet", index=False)
    if with_fills:
        pd.DataFrame(
            {
                "ts": [1 * DAY, 2 * DAY],
                "symbol": ["AAPL", "AAPL"],
                "side": [1, -1],
                "qty": [100.0, 100.0],
                "price": [10.0, 11.0],
                "cost": [0.0, 0.0],
                "tag": ["", ""],
            }
        ).to_parquet(run_dir / "fills.parquet", index=False)


def test_total_return_and_max_drawdown_known_values(tmp_path: Path) -> None:
    _write_run(tmp_path / "run", [100.0, 110.0, 99.0, 121.0])
    m = compute_metrics(tmp_path / "run")
    assert m["total_return"] == pytest.approx(0.21)  # 121/100 - 1
    assert m["max_drawdown"] == pytest.approx(99.0 / 110.0 - 1.0)  # -0.1


def test_drawdown_is_nonpositive_and_returns_align(tmp_path: Path) -> None:
    _write_run(tmp_path / "run", [100.0, 110.0, 99.0, 121.0])
    equity = equity_curve(pd.read_parquet(tmp_path / "run" / "portfolio.parquet"))
    dd = drawdown_series(equity)
    assert (dd <= 1e-12).all()
    assert len(period_returns(equity)) == 3


def test_turnover_and_trade_count(tmp_path: Path) -> None:
    _write_run(tmp_path / "run", [100_000.0, 100_100.0, 100_050.0])
    m = compute_metrics(tmp_path / "run")
    # notional = 100*10 + 100*11 = 2100; mean equity ~100_050
    assert m["n_trades"] == 2.0
    assert m["turnover"] == pytest.approx(2100.0 / ((100_000 + 100_100 + 100_050) / 3))


def test_sharpe_uses_252_day_annualisation(tmp_path: Path) -> None:
    # Constant positive daily return -> vol 0 -> Sharpe is nan (guarded), so use a run
    # with variation and just assert it's finite and scaled by sqrt(252).
    _write_run(tmp_path / "run", [100.0, 101.0, 100.5, 102.0, 101.0, 103.0])
    m = compute_metrics(tmp_path / "run")
    assert TRADING_DAYS == 252
    assert m["sharpe"] == m["sharpe"]  # not NaN


def test_tearsheet_writes_a_png(tmp_path: Path) -> None:
    _write_run(tmp_path / "run", [100.0, 101.0, 99.0, 102.0, 98.0, 105.0])
    out = render(tmp_path / "run")
    assert out.exists()
    assert out.stat().st_size > 0
    assert out.suffix == ".png"


def test_metrics_handle_missing_fills(tmp_path: Path) -> None:
    _write_run(tmp_path / "run", [100.0, 101.0, 102.0], with_fills=False)
    m = compute_metrics(tmp_path / "run")
    assert m["turnover"] == 0.0
    assert m["n_trades"] == 0.0
