"""Owns manifest write and verify: the reproducibility metadata for a run directory.

A manifest.json captures everything needed to reproduce a run (ARCHITECTURE seam 7):
the full config, the git commit, a content hash of the input data, the seed, the
python/library versions, and wall-clock timings. `verify` re-runs the manifest's config
into a throwaway directory and asserts the output matches the stored run byte-for-byte
(compared as parquet content).

Wall-clock use lives here, in the runner, deliberately — never inside the engine.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import numpy
import pandas as pd
import pyarrow

from tessera.core.engine import Recorder
from tessera.runner.config import RunConfig

MANIFEST_NAME = "manifest.json"

# A function that executes a run described by a config, emitting to a recorder.
RunFn = Callable[[RunConfig, Recorder], None]


def data_hash(paths: Iterable[str | Path]) -> str:
    """A stable content hash of the input data files (order-independent by name)."""
    digest = hashlib.sha256()
    for path in sorted(Path(p) for p in paths):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def library_versions() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "numpy": numpy.__version__,
        "pandas": pd.__version__,
        "pyarrow": pyarrow.__version__,
    }


def git_commit() -> str | None:
    """The current git HEAD, or None if unavailable (not a repo / git missing)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def write_manifest(
    run_dir: str | Path,
    config: RunConfig,
    *,
    input_hash: str,
    timings: dict[str, float],
) -> None:
    """Write manifest.json into `run_dir`."""
    manifest = {
        "config": config.to_dict(),
        "git_commit": git_commit(),
        "data_hash": input_hash,
        "seed": config.seed,
        "versions": library_versions(),
        "timings": timings,
    }
    text = json.dumps(manifest, indent=2, sort_keys=True)
    Path(run_dir, MANIFEST_NAME).write_text(text)


def read_manifest(run_dir: str | Path) -> dict[str, Any]:
    return json.loads(Path(run_dir, MANIFEST_NAME).read_text())


def config_from_manifest(run_dir: str | Path) -> RunConfig:
    return RunConfig.from_dict(read_manifest(run_dir)["config"])


def verify(run_dir: str | Path, run_fn: RunFn) -> bool:
    """Re-run the manifest's config and assert identical parquet output.

    `run_fn(config, recorder)` executes a run from a config (the CLI provides the real
    one that resolves the strategy and data source). Returns True iff every parquet file
    in `run_dir` is reproduced with identical content.
    """
    import tempfile

    from tessera.runner.recorder import ParquetRecorder

    config = config_from_manifest(run_dir)
    run_dir = Path(run_dir)

    with tempfile.TemporaryDirectory() as tmp:
        recorder = ParquetRecorder(tmp)
        run_fn(config, recorder)
        recorder.close()

        originals = sorted(run_dir.glob("*.parquet"))
        if not originals:
            return False
        for original in originals:
            replay = Path(tmp) / original.name
            if not replay.exists():
                return False
            a = pd.read_parquet(original).reset_index(drop=True)
            b = pd.read_parquet(replay).reset_index(drop=True)
            if not a.equals(b):
                return False
    return True
