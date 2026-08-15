"""Owns the Recorder implementations: ParquetRecorder, NullRecorder, MultiRecorder.

The `Recorder` protocol itself lives in `core/engine.py` (so the engine never imports
from runner). These implement it (ARCHITECTURE seam 6):

- `ParquetRecorder` buffers records by kind and writes one parquet file per kind on
  close (`fill` -> fills.parquet, `order` -> orders.parquet, `portfolio` ->
  portfolio.parquet). Buffering suits parquet's columnar, write-once nature and is
  ample for daily-bar runs.
- `NullRecorder` drops everything — for fast benchmark runs.
- `MultiRecorder` fans a record stream out to several recorders at once.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from tessera.core.engine import Recorder

# Which file each record kind is written to.
_FILENAMES: dict[str, str] = {
    "fill": "fills.parquet",
    "order": "orders.parquet",
    "portfolio": "portfolio.parquet",
}


class NullRecorder:
    """Discards every record. Useful when only speed or side effects matter."""

    def record(self, kind: str, payload: dict[str, Any]) -> None:
        pass

    def close(self) -> None:
        pass


class MultiRecorder:
    """Fans each record out to several recorders (e.g. parquet + a live stream)."""

    def __init__(self, *recorders: Recorder) -> None:
        self._recorders = list(recorders)

    def record(self, kind: str, payload: dict[str, Any]) -> None:
        for recorder in self._recorders:
            recorder.record(kind, payload)

    def close(self) -> None:
        for recorder in self._recorders:
            recorder.close()


class ParquetRecorder:
    """Buffers records by kind and writes one parquet file per kind on close."""

    def __init__(self, run_dir: str | Path) -> None:
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._buffers: dict[str, list[dict[str, Any]]] = {}

    def record(self, kind: str, payload: dict[str, Any]) -> None:
        self._buffers.setdefault(kind, []).append(dict(payload))

    def record_counts(self) -> dict[str, int]:
        """How many records of each kind were seen. Lets the manifest state a run's
        reject count affirmatively, so "0 rejections" is recorded, not inferred from a
        missing reject.parquet (which is ambiguous with a broken recorder)."""
        return {kind: len(rows) for kind, rows in self._buffers.items()}

    def close(self) -> None:
        for kind, rows in self._buffers.items():
            filename = _FILENAMES.get(kind, f"{kind}.parquet")
            pd.DataFrame(rows).to_parquet(self.run_dir / filename, index=False)
