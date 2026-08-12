"""Owns performance metrics computed from a run directory (never from the engine).

Everything here reads the parquet the engine wrote — `portfolio.parquet` for the equity
curve and `fills.parquet` for trade-based stats — and derives metrics after the fact
(ARCHITECTURE seam 8). That means new metrics are new functions over the same records,
and old runs can be re-measured without re-running them.

Daily bars are annualised with 252 trading days; the Sharpe assumes a zero risk-free rate
and independent, identically-distributed period returns.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def load_portfolio(run_dir: str | Path) -> pd.DataFrame:
    return pd.read_parquet(Path(run_dir) / "portfolio.parquet")


def load_fills(run_dir: str | Path) -> pd.DataFrame:
    path = Path(run_dir) / "fills.parquet"
    if path.exists():
        return pd.read_parquet(path)
    return pd.DataFrame(columns=["ts", "symbol", "side", "qty", "price", "cost", "tag"])


def equity_curve(portfolio: pd.DataFrame) -> pd.Series:
    """Equity indexed by (UTC) time — the after-the-fact time axis for plotting."""
    index = pd.to_datetime(portfolio["ts"], utc=True)
    return pd.Series(portfolio["equity"].to_numpy(dtype=float), index=index, name="equity")


def period_returns(equity: pd.Series) -> pd.Series:
    return equity.pct_change(fill_method=None).dropna()


def drawdown_series(equity: pd.Series) -> pd.Series:
    """Fractional drawdown from the running peak (0 at highs, negative below)."""
    return equity / equity.cummax() - 1.0


def rolling_sharpe(
    returns: pd.Series, window: int = 60, periods_per_year: int = TRADING_DAYS
) -> pd.Series:
    mean = returns.rolling(window).mean()
    std = returns.rolling(window).std()
    return (mean / std) * np.sqrt(periods_per_year)


def compute_metrics(run_dir: str | Path, periods_per_year: int = TRADING_DAYS) -> dict[str, float]:
    """Compute the headline metrics for a completed run directory."""
    equity = equity_curve(load_portfolio(run_dir))
    fills = load_fills(run_dir)
    rets = period_returns(equity)
    dd = drawdown_series(equity)

    n = len(rets)
    e0 = float(equity.iloc[0]) if len(equity) else float("nan")
    e1 = float(equity.iloc[-1]) if len(equity) else float("nan")

    total_return = e1 / e0 - 1.0 if e0 else float("nan")
    if n > 0 and e0 > 0 and e1 > 0:
        ann_return = (e1 / e0) ** (periods_per_year / n) - 1.0
    else:
        ann_return = float("nan")
    vol = float(rets.std(ddof=1)) if n > 1 else float("nan")
    ann_vol = vol * np.sqrt(periods_per_year) if not np.isnan(vol) else float("nan")
    mean_r = float(rets.mean()) if n > 0 else float("nan")
    sharpe = (mean_r / vol) * np.sqrt(periods_per_year) if (n > 1 and vol > 0) else float("nan")

    # Turnover: traded notional relative to average equity.
    if len(fills):
        notional = float((fills["price"] * fills["qty"]).sum())
        mean_eq = float(equity.mean())
        turnover = notional / mean_eq if mean_eq else float("nan")
    else:
        turnover = 0.0

    # Hit rate and win/loss are per-period (daily): per-trade round-trip attribution under
    # average-cost accounting is a later feature.
    wins = rets[rets > 0]
    losses = rets[rets < 0]
    decided = len(wins) + len(losses)
    hit_rate = len(wins) / decided if decided else float("nan")
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    win_loss = avg_win / abs(avg_loss) if avg_loss != 0 else float("nan")

    return {
        "total_return": total_return,
        "annualized_return": ann_return,
        "annualized_vol": ann_vol,
        "sharpe": sharpe,
        "max_drawdown": float(dd.min()) if len(dd) else float("nan"),
        "turnover": turnover,
        "hit_rate": hit_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "win_loss_ratio": win_loss,
        "n_periods": float(n),
        "n_trades": float(len(fills)),
    }
