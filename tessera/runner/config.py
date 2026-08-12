"""Owns RunConfig: the single immutable description of a run the engine consumes.

Everything the engine needs to reproduce a run lives here (ARCHITECTURE seam 7). The
engine takes exactly one RunConfig and knows nothing about where it came from — a CLI
today, a sweep orchestrator or an agent later. `to_dict`/`from_dict` round-trip it
through plain JSON for the manifest.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class RunConfig:
    """An immutable, fully-reproducible description of one backtest run."""

    strategy: str  # import path, e.g. "tessera.strategy.examples.ma_crossover:MaCrossover"
    symbols: list[str]
    start_ts: int
    end_ts: int
    data_source: str
    seed: int
    initial_cash: float
    params: dict[str, Any] = field(default_factory=dict)
    fill_model: str = "naive"
    cost_bps: float = 0.0
    latency_ns: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunConfig:
        return cls(**data)
