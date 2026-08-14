"""Owns the typer CLI entry point: `tessera run` and `tessera verify`.

The CLI is one producer of a RunConfig (later a sweep or an agent could be another). It
also provides `run_from_config`, the single function that turns a config into a live run
by resolving the strategy and data source — the same function `manifest.verify` replays.
"""

from __future__ import annotations

import inspect
import time
from pathlib import Path
from typing import Any

import typer

from tessera.core.engine import Recorder, run
from tessera.core.queue import merge
from tessera.data.sources.csv_bars import CsvBarSource, to_epoch_ns
from tessera.execution.costs import BpsCostModel
from tessera.execution.naive import NaiveFillModel
from tessera.portfolio.book import Book
from tessera.runner.config import RunConfig
from tessera.runner.manifest import ConventionMismatch, data_hash, verify, write_manifest
from tessera.runner.recorder import ParquetRecorder
from tessera.strategy.examples.ma_crossover import MaCrossover
from tessera.strategy.examples.reversal import Reversal

app = typer.Typer(help="Tessera: an event-driven backtesting engine.")

# Name -> strategy class. New strategies register here.
_STRATEGIES: dict[str, Any] = {
    "ma_crossover": MaCrossover,
    "reversal": Reversal,
}


def _coerce(value: str) -> Any:
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            continue
    return value


def _parse_params(raw: str) -> dict[str, Any]:
    """Parse 'fast=10,slow=50' into {'fast': 10, 'slow': 50}."""
    params: dict[str, Any] = {}
    for pair in filter(None, (p.strip() for p in raw.split(","))):
        key, _, value = pair.partition("=")
        params[key.strip()] = _coerce(value.strip())
    return params


def _make_strategy(name: str, params: dict[str, Any], initial_cash: float) -> Any:
    if name not in _STRATEGIES:
        raise typer.BadParameter(f"unknown strategy '{name}'; choose from {sorted(_STRATEGIES)}")
    cls = _STRATEGIES[name]
    kwargs = dict(params)
    # Inject starting capital so fractional-notional sizing scales with the account. It is a
    # static config value (not market data), so this is not look-ahead. Only passed if the
    # strategy accepts it, so a strategy that sizes some other way is unaffected.
    if "initial_cash" in inspect.signature(cls).parameters:
        kwargs.setdefault("initial_cash", initial_cash)
    return cls(**kwargs)


def run_from_config(config: RunConfig, recorder: Recorder) -> None:
    """Execute a run described entirely by `config`, emitting to `recorder`.

    This is the reproducible core the CLI and `manifest.verify` both call.
    """
    strategy = _make_strategy(config.strategy, config.params, config.initial_cash)
    sources = [
        CsvBarSource(
            Path(config.data_source) / f"{symbol}.csv", symbol, config.start_ts, config.end_ts
        ).events()
        for symbol in config.symbols
    ]
    fill_model = NaiveFillModel(BpsCostModel(config.cost_bps), latency_ns=config.latency_ns)
    run(merge(sources), strategy, fill_model, Book(config.initial_cash), recorder)


@app.command(name="run")
def run_cmd(  # noqa: PLR0913 - a CLI naturally has many flags
    strategy: str = typer.Option(..., "--strategy", help="ma_crossover or reversal"),
    symbol: str = typer.Option("AAPL", "--symbol"),
    start: str = typer.Option("2015-01-01", "--start"),
    end: str = typer.Option("2024-12-31", "--end"),
    params: str = typer.Option("", "--params", help="e.g. fast=10,slow=50"),
    data_dir: str = typer.Option("data", "--data-dir", help="dir holding <SYMBOL>.csv"),
    cost_bps: float = typer.Option(0.0, "--cost-bps"),
    latency_ns: int = typer.Option(0, "--latency-ns"),
    seed: int = typer.Option(0, "--seed"),
    cash: float = typer.Option(100_000.0, "--cash"),
    out: str = typer.Option("runs", "--out", help="parent dir for run directories"),
) -> None:
    """Run a backtest and write a reproducible run directory."""
    config = RunConfig(
        strategy=strategy,
        symbols=[symbol],
        start_ts=to_epoch_ns(start),
        end_ts=to_epoch_ns(end),
        data_source=data_dir,
        seed=seed,
        initial_cash=cash,
        params=_parse_params(params),
        cost_bps=cost_bps,
        latency_ns=latency_ns,
    )

    run_dir = Path(out) / f"{strategy}-{symbol}-{time.time_ns()}"
    recorder = ParquetRecorder(run_dir)

    started = time.perf_counter()
    run_from_config(config, recorder)
    recorder.close()
    elapsed = time.perf_counter() - started

    inputs = [Path(data_dir) / f"{s}.csv" for s in config.symbols]
    write_manifest(run_dir, config, input_hash=data_hash(inputs), timings={"wall_seconds": elapsed})
    typer.echo(str(run_dir))


@app.command(name="verify")
def verify_cmd(run_dir: str = typer.Argument(..., help="a run directory to reproduce")) -> None:
    """Re-run a run directory's manifest and confirm identical output."""
    try:
        ok = verify(run_dir, run_from_config)
    except ConventionMismatch as exc:
        # Not a reproduction failure — the run's convention predates the current code, so a
        # faithful re-run is impossible. Report it distinctly (exit 2) instead of "MISMATCH".
        typer.echo(f"CONVENTION MISMATCH: {exc}")
        raise typer.Exit(2) from exc
    typer.echo("OK" if ok else "MISMATCH")
    raise typer.Exit(0 if ok else 1)


@app.command(name="report")
def report_cmd(
    run_dir: str = typer.Argument(..., help="a completed run directory"),
    out: str = typer.Option("", "--out", help="PNG path (default: <run_dir>/tearsheet.png)"),
) -> None:
    """Compute metrics for a run and write a tearsheet PNG."""
    # Imported lazily so `tessera run` never pays matplotlib's import cost.
    from tessera.metrics.returns import compute_metrics
    from tessera.metrics.tearsheet import render

    m = compute_metrics(run_dir)
    typer.echo(
        f"total {m['total_return'] * 100:.2f}%  ann {m['annualized_return'] * 100:.2f}%  "
        f"vol {m['annualized_vol'] * 100:.2f}%  Sharpe {m['sharpe']:.2f}  "
        f"maxDD {m['max_drawdown'] * 100:.2f}%  turnover {m['turnover']:.2f}x  "
        f"hit {m['hit_rate'] * 100:.1f}%  trades {int(m['n_trades'])}"
    )
    path = render(run_dir, out or None)
    typer.echo(str(path))


if __name__ == "__main__":
    app()
