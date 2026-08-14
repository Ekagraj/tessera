"""Tests for the runner: RunConfig, recorders, and manifest write/verify.

Covers the Task-7 acceptance: a run produces runs/<id>/ with manifest.json plus
fills/orders/portfolio parquet, and verify() re-runs the config to an identical result.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from tessera.core.engine import Recorder, run
from tessera.core.events import Bar, Event
from tessera.data.sources.csv_bars import TIMESTAMP_CONVENTION, to_epoch_ns
from tessera.execution.costs import BpsCostModel
from tessera.execution.naive import NaiveFillModel
from tessera.portfolio.book import Book
from tessera.runner.config import RunConfig
from tessera.runner.manifest import (
    ConventionMismatch,
    config_from_manifest,
    data_hash,
    read_manifest,
    verify,
    write_manifest,
)
from tessera.runner.recorder import MultiRecorder, NullRecorder, ParquetRecorder

DAY = 86_400_000_000_000


class FlipFlop:
    def on_event(self, event: Event, ctx: Any) -> list[Any]:
        from tessera.strategy.base import Order

        held = ctx.position(event.symbol)
        side = +1 if held == 0.0 else -1
        return [Order(event.symbol, side, 10.0, "market")]


def _bars() -> list[Bar]:
    opens = [10.0, 11.0, 10.5, 12.0, 11.5]
    return [
        Bar(i * DAY, "AAPL", o, o + 1.0, o - 1.0, o + 0.5, 1_000.0)
        for i, o in enumerate(opens)
    ]


def _config() -> RunConfig:
    return RunConfig(
        strategy="flipflop",
        symbols=["AAPL"],
        start_ts=0,
        end_ts=5 * DAY,
        data_source="synthetic",
        seed=7,
        initial_cash=100_000.0,
        cost_bps=5.0,
    )


def _do_run(config: RunConfig, recorder: Recorder) -> None:
    """Execute a deterministic run described by `config`, emitting to `recorder`."""
    fm = NaiveFillModel(cost_model=BpsCostModel(config.cost_bps), latency_ns=config.latency_ns)
    run(_bars(), FlipFlop(), fm, Book(cash=config.initial_cash), recorder)


def test_config_round_trips_through_dict() -> None:
    cfg = _config()
    assert RunConfig.from_dict(cfg.to_dict()) == cfg


def test_null_and_multi_recorder() -> None:
    seen: list[tuple[str, dict[str, Any]]] = []

    class Spy:
        def record(self, kind: str, payload: dict[str, Any]) -> None:
            seen.append((kind, payload))

        def close(self) -> None:
            pass

    multi = MultiRecorder(NullRecorder(), Spy())
    multi.record("fill", {"x": 1})
    multi.close()
    assert seen == [("fill", {"x": 1})]


def test_parquet_recorder_writes_expected_files(tmp_path: Path) -> None:
    rec = ParquetRecorder(tmp_path / "run1")
    _do_run(_config(), rec)
    rec.close()
    for name in ("fills.parquet", "orders.parquet", "portfolio.parquet"):
        assert (tmp_path / "run1" / name).exists()
    # portfolio has one row per bar
    assert len(pd.read_parquet(tmp_path / "run1" / "portfolio.parquet")) == len(_bars())


def test_manifest_write_and_read(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    rec = ParquetRecorder(run_dir)
    _do_run(_config(), rec)
    rec.close()

    write_manifest(run_dir, _config(), input_hash="deadbeef", timings={"wall_seconds": 0.01})
    manifest = read_manifest(run_dir)

    assert manifest["seed"] == 7
    assert manifest["data_hash"] == "deadbeef"
    assert manifest["config"]["cost_bps"] == 5.0
    assert "python" in manifest["versions"]
    assert config_from_manifest(run_dir) == _config()


def test_verify_passes_on_identical_rerun(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    rec = ParquetRecorder(run_dir)
    _do_run(_config(), rec)
    rec.close()
    write_manifest(run_dir, _config(), input_hash="x", timings={"wall_seconds": 0.0})

    assert verify(run_dir, _do_run) is True


def test_verify_fails_when_rerun_diverges(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    rec = ParquetRecorder(run_dir)
    _do_run(_config(), rec)
    rec.close()
    write_manifest(run_dir, _config(), input_hash="x", timings={"wall_seconds": 0.0})

    def _diverging(config: RunConfig, recorder: Recorder) -> None:
        # Different cost -> different fill costs -> different records.
        fm = NaiveFillModel(cost_model=BpsCostModel(999.0))
        run(_bars(), FlipFlop(), fm, Book(cash=config.initial_cash), recorder)

    assert verify(run_dir, _diverging) is False


def test_verify_reports_convention_mismatch_not_generic_false(tmp_path: Path) -> None:
    """A run made under an older timestamp convention must be reported as a *convention*
    mismatch — loud and specific — not silently re-run and returned as a bare False.

    This is the guard that makes the version field load-bearing rather than decoration:
    without the check in verify(), the tampered manifest below would re-run and (because the
    synthetic bars are stamped by hand, not via to_epoch_ns) return True, hiding the break.
    """
    run_dir = tmp_path / "run"
    rec = ParquetRecorder(run_dir)
    _do_run(_config(), rec)
    rec.close()
    write_manifest(run_dir, _config(), input_hash="x", timings={"wall_seconds": 0.0})

    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["timestamp_convention"] == TIMESTAMP_CONVENTION  # current runs are tagged

    # Pretend this run was produced under the old midnight convention.
    manifest["timestamp_convention"] = "midnight_v0"
    (run_dir / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ConventionMismatch) as excinfo:
        verify(run_dir, _do_run)
    msg = str(excinfo.value)
    assert "midnight_v0" in msg and TIMESTAMP_CONVENTION in msg  # names both, so a human sees why

    # A pre-Task-11 manifest has no field at all (like runs/task10p2_fixed/): also reported.
    del manifest["timestamp_convention"]
    (run_dir / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ConventionMismatch):
        verify(run_dir, _do_run)


def test_timestamp_convention_pins_loader_behavior() -> None:
    """Tripwire against the bump-discipline weakness: the convention string is meaningful
    only if it tracks the actual date->ns mapping. If someone changes `to_epoch_ns` without
    bumping `TIMESTAMP_CONVENTION`, the second assertion here fails — forcing a conscious
    edit of this test, whose first assertion and name demand the version be bumped too.
    """
    assert TIMESTAMP_CONVENTION == "session_close_v1"
    # session_close_v1 means: a date maps to its 16:00 ET close, i.e. 21:00 UTC in winter.
    expected = int(pd.Timestamp("2020-01-02 21:00", tz="UTC").value)
    assert to_epoch_ns("2020-01-02") == expected


def test_data_hash_is_stable_and_content_sensitive(tmp_path: Path) -> None:
    a = tmp_path / "a.csv"
    a.write_text("date,close\n2020-01-01,10\n")
    h1 = data_hash([a])
    h2 = data_hash([a])
    assert h1 == h2
    a.write_text("date,close\n2020-01-01,11\n")
    assert data_hash([a]) != h1
