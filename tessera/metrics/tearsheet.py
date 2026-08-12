"""Owns tearsheet rendering: a single matplotlib figure summarising a run.

Reads a run directory's records (via the metrics functions) and draws four panels:
the equity curve, the underwater drawdown, the rolling 60-period Sharpe, and the return
distribution. Uses the headless Agg backend so it renders a PNG without a display.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402  (backend must be set before pyplot)

from tessera.metrics import returns as metrics  # noqa: E402


def render(run_dir: str | Path, out_path: str | Path | None = None) -> Path:
    """Render the tearsheet PNG for `run_dir`; returns the written path."""
    run_dir = Path(run_dir)
    equity = metrics.equity_curve(metrics.load_portfolio(run_dir))
    rets = metrics.period_returns(equity)
    dd = metrics.drawdown_series(equity)
    rsharpe = metrics.rolling_sharpe(rets, window=60)
    m = metrics.compute_metrics(run_dir)

    fig, axes = plt.subplots(2, 2, figsize=(14, 9))
    fig.suptitle(
        f"{run_dir.name}   |   total {m['total_return'] * 100:.1f}%   "
        f"Sharpe {m['sharpe']:.2f}   maxDD {m['max_drawdown'] * 100:.1f}%   "
        f"turnover {m['turnover']:.1f}x",
        fontsize=12,
    )

    ax = axes[0, 0]
    ax.plot(equity.index, equity.to_numpy(), color="tab:blue")
    ax.set_title("Equity curve")
    ax.set_ylabel("equity")

    ax = axes[0, 1]
    ax.fill_between(dd.index, dd.to_numpy() * 100.0, 0.0, color="tab:red", alpha=0.4)
    ax.set_title("Underwater (drawdown %)")
    ax.set_ylabel("%")

    ax = axes[1, 0]
    ax.plot(rsharpe.index, rsharpe.to_numpy(), color="tab:green")
    ax.axhline(0.0, color="black", linewidth=0.8)
    ax.set_title("Rolling 60-period Sharpe")

    ax = axes[1, 1]
    if len(rets):
        ax.hist(rets.to_numpy() * 100.0, bins=40, color="tab:purple", alpha=0.8)
    ax.set_title("Period return distribution (%)")
    ax.set_xlabel("%")

    for ax in (axes[0, 0], axes[0, 1], axes[1, 0]):
        for label in ax.get_xticklabels():
            label.set_rotation(30)
            label.set_horizontalalignment("right")

    fig.tight_layout(rect=(0, 0, 1, 0.97))
    out = Path(out_path) if out_path is not None else run_dir / "tearsheet.png"
    fig.savefig(out, dpi=100)
    plt.close(fig)
    return out
