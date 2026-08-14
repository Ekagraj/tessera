"""Owns the CSV daily-bar loader: converts human dates to integer-ns Bar events.

This is a conversion boundary: dates are parsed to integer nanoseconds since the UTC
epoch here and nowhere else (hard rule 2). Column names are normalised to lowercase, so
Stooq/Yahoo-style `Date,Open,High,Low,Close,Volume` files load without fuss. Rows are
assumed already in date order (the queue will raise if a file is not).

A daily bar is stamped at its **session close** — 16:00 America/New_York — not at UTC
midnight. The bar carries that day's high/low/close, which are only known once the
session ends, so stamping it at midnight would place it ~21h before its data existed:
invisible with one daily source, but a real look-ahead once an intraday source is merged
in (the midnight bar would sort ahead of that day's ticks). See decision D41.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pandas as pd

from tessera.core.events import Bar

# US equity regular-session close, local wall-clock time. The UTC instant this maps to
# is 21:00 in winter (EST) and 20:00 in summer (EDT); constructing the close in the
# market tz and converting to UTC handles that DST shift correctly. Half-day early
# closes (13:00 ET, ~13 days/yr) are not modelled — they are stamped 3h late, which is
# harmless for a single daily source (every bar still shifts monotonically). See D41.
_MARKET_TZ = "America/New_York"
_SESSION_CLOSE = "16:00"

# Version tag for the date->nanoseconds mapping THIS MODULE implements. It is the single
# source of truth (imported, never re-typed) and is stamped into every run manifest so
# `verify` can report that a run made under a *different* convention — e.g. the old midnight
# stamp, "midnight_v0" — cannot be faithfully reproduced, instead of silently re-running and
# returning a bare mismatch (decision D42). BUMP THIS whenever you change how `to_epoch_ns`
# or `events()` map a date to ns; `test_timestamp_convention_pins_loader_behavior` ties this
# string to the actual mapping, so changing the mapping without bumping breaks a test.
TIMESTAMP_CONVENTION = "session_close_v1"


def to_epoch_ns(date: str) -> int:
    """Convert a human date to integer ns at the 16:00 ET session close (in UTC)."""
    local = pd.Timestamp(f"{date} {_SESSION_CLOSE}", tz=_MARKET_TZ)
    return int(local.tz_convert("UTC").value)


class CsvBarSource:
    """Yields `Bar` events for one symbol from a CSV of daily bars."""

    def __init__(
        self,
        path: str | Path,
        symbol: str,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> None:
        self.path = Path(path)
        self.symbol = symbol
        self.start_ts = start_ts
        self.end_ts = end_ts

    def events(self) -> Iterator[Bar]:
        df = pd.read_csv(self.path)
        df.columns = [str(c).strip().lower() for c in df.columns]

        # Stamp each bar at its 16:00 ET session close (see to_epoch_ns / D41). Add the
        # wall-clock close to the naive date FIRST, then localize to the market tz, so the
        # UTC offset is DST-correct; 16:00 is never in a spring-forward gap. Forcing ns
        # resolution guards the microsecond-parse bug that would make timestamps 1000x small.
        local_close = pd.to_datetime(df["date"]) + pd.Timedelta(hours=16)
        ts = (
            local_close.dt.tz_localize(_MARKET_TZ)
            .dt.tz_convert("UTC")
            .dt.as_unit("ns")
            .astype("int64")
            .to_numpy()
        )
        opens = df["open"].to_numpy(dtype=float)
        highs = df["high"].to_numpy(dtype=float)
        lows = df["low"].to_numpy(dtype=float)
        closes = df["close"].to_numpy(dtype=float)
        volumes = (
            df["volume"].to_numpy(dtype=float)
            if "volume" in df.columns
            else [0.0] * len(df)
        )

        for i in range(len(df)):
            t = int(ts[i])
            if self.start_ts is not None and t < self.start_ts:
                continue
            if self.end_ts is not None and t > self.end_ts:
                continue
            yield Bar(
                t,
                self.symbol,
                float(opens[i]),
                float(highs[i]),
                float(lows[i]),
                float(closes[i]),
                float(volumes[i]),
            )
