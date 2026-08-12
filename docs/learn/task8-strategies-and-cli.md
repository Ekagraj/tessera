# Understanding Task 8: strategies, the CSV loader, and the CLI

A from-scratch explanation, no code required. This is the task where the whole system
finally runs from **one command**. Read it once, then try the "Answer these yourself"
section.

---

## Part 0 — The problem we are actually solving

We have a working engine, but nothing *real* to feed it and no easy way to launch a run.
Task 8 supplies the three missing everyday pieces:

1. **Strategies** — actual trading ideas to test (moving-average crossover; reversal).
2. **A data loader** — read real price files (CSV) and turn them into events.
3. **A command-line** — one command that wires it all together and writes a run.

Nothing here is deep architecture; it's the "make it usable" layer. But one small,
famous kind of bug shows up (see Part 4), and it's worth understanding.

---

## Part 1 — The two strategies (and the one rule they obey)

Both strategies obey the Task-3 rule: **keep your own memory, never ask for history.**

- **Moving-average crossover** (`MaCrossover`) — keeps two running averages of recent
  closes: a *fast* one (few days) and a *slow* one (many days). When the fast average
  rises above the slow one, it buys; when the fast falls back below, it sells to flat.
  It stores its own two little fixed-length buffers of recent prices and updates them as
  each day arrives. It is **long or flat**.
- **Reversal** (`Reversal`) — bets that a move reverses: after a *down* day it goes long,
  after an *up* day it goes short. It remembers only *yesterday's close* to know whether
  today was up or down. It is **long or short**, and it leans on the engine's flip
  accounting (Task 4) to switch sides in one order.

Neither ever receives a table of past prices. Each builds whatever history it needs, one
legitimate day at a time — so look-ahead stays impossible.

> Recite: *both strategies accumulate their own rolling state; crossover is long/flat on
> two moving averages, reversal is long/short on yesterday-vs-today.*

---

## Part 2 — The CSV loader: turning a file into events

A **data source** reads a price file and yields `Bar` events in time order. `CsvBarSource`
opens a CSV like `Date,Open,High,Low,Close,Volume`, normalizes the column names, and emits
one `Bar` per row.

Crucially, it's the **conversion boundary**: the one and only place where a human date
string ("2020-01-02") becomes an integer number of nanoseconds. Everywhere downstream —
the queue, the clock, the engine — only ever sees integers. If we ever add tick data,
that's a *new* loader, and the rest of the system doesn't change.

---

## Part 3 — The CLI: one command, one run

`tessera run --strategy ma_crossover --symbol AAPL --start 2020-01-01 --end 2024-12-31
--params fast=10,slow=50` does the whole dance:

1. package all those flags into a **RunConfig**,
2. resolve the strategy name to a class and the symbol to its CSV file,
3. run the engine, writing records to a **ParquetRecorder** in a fresh `runs/<id>/` folder,
4. write the **manifest** (with a hash of the input CSV, the git commit, timings…).

There's a nice design point: the CLI and `verify` share **one** function,
`run_from_config`. The CLI calls it to run; `verify` calls the *same* function to
reproduce. Because there's a single code path, "what the CLI did" and "what verify
checks" can never drift apart. There's also a `tessera verify <dir>` command that re-runs
a saved run and confirms it reproduces.

> Recite: *the CLI turns flags into a RunConfig and runs it; run_from_config is the one
> shared path that both running and verifying use.*

---

## Part 4 — Your question: "weren't we using plain nanosecond integers? Why did datetime show up?"

Great question, and it's the subtle heart of this task. Yes — **inside** the system,
time is *always* a plain integer number of nanoseconds. That never changed.

But the CSV on disk doesn't contain integers. It contains **human dates** like
`2020-01-02`. Something has to translate that text into our integer. That translation is
the loader's whole job — the "conversion boundary" — and to parse a messy human date, the
easiest tool is a datetime library (pandas). So datetime appears **only** as a
short-lived stepping stone: *text date → datetime → integer nanoseconds*, all inside the
loader. The moment a `Bar` is created, it's back to a pure integer, forever.

Now the bug. When we asked pandas to parse the dates, its modern default produced a
datetime measured in **microseconds**, not nanoseconds. When we then pulled the integer
out of it, we got a *microsecond* count — exactly **1000× too small**. Every timestamp
came out a thousand times lower than it should be, which pushed them all outside the
requested date range, so the loader silently produced **zero bars** and the run did
nothing.

The fix was one line: explicitly tell pandas "use nanosecond resolution" before pulling
out the integer. Then the loader's integer matched the rest of the system exactly.

Why this is worth remembering: it's the *same* family of trap the whole "integer
nanoseconds" rule exists to avoid (Task 1). Human time formats have hidden resolutions
and timezones; the instant you touch one you can be off by a factor of 1000 (or a
timezone). That's precisely why we **confine** all date handling to this one boundary and
keep pure integers everywhere else — so a slip like this can only ever happen in one
place, and it's easy to find.

> Recite: *the engine only ever uses integer nanoseconds; datetime appears only inside
> the loader as a temporary step to parse human dates. The bug was pandas defaulting to
> microseconds, making every timestamp 1000x too small — fixed by forcing ns resolution.*

---

## Part 5 — What actually got built in Task 8

- **`tessera/strategy/examples/ma_crossover.py`** and **`reversal.py`** — the two example
  strategies, each keeping its own rolling state.
- **`tessera/data/loader.py`** — the `DataSource` protocol.
- **`tessera/data/sources/csv_bars.py`** — `CsvBarSource` and `to_epoch_ns` (the date →
  int-ns conversion, forced to nanosecond resolution).
- **`tessera/runner/cli.py`** — `tessera run` and `tessera verify`, plus the shared
  `run_from_config`.
- **`tests/test_strategies_and_cli.py`** — strategy behavior, the loader's date
  conversion, and a full `tessera run` that `verify` reproduces.

---

## Worked example with synthetic data

Write a small CSV and run it:

```
data/AAPL.csv:
  Date,Open,High,Low,Close,Volume
  2020-01-01,100,101,99,100.5,1000000
  2020-01-02,100.5,102,100,101.2,1000000
  ... (more daily rows) ...

$ tessera run --strategy ma_crossover --symbol AAPL \
      --start 2020-01-01 --end 2020-12-31 --params fast=5,slow=20 --cost-bps 5

runs/ma_crossover-AAPL-1786.../        <- printed run directory
  fills.parquet
  orders.parquet
  portfolio.parquet
  manifest.json    { config, git_commit, data_hash, versions, timings }

$ tessera verify runs/ma_crossover-AAPL-1786...
OK
```

What happened, in order: the CLI parsed the flags into a RunConfig; `to_epoch_ns` turned
`2020-01-01` into an integer; `CsvBarSource` streamed `Bar` events; the queue passed them
to the engine; the engine ran the crossover strategy, filling at next opens and marking
each day; the ParquetRecorder wrote three tables; the manifest stamped it. `verify` re-ran
the same config and confirmed identical output.

### Which file and function did each step

| Step above | File | Function / type |
|---|---|---|
| parse flags → config | `tessera/runner/cli.py` | `run_cmd` |
| date text → integer ns | `tessera/data/sources/csv_bars.py` | `to_epoch_ns` |
| CSV rows → Bar events | `tessera/data/sources/csv_bars.py` | `CsvBarSource.events` |
| the trading logic | `tessera/strategy/examples/ma_crossover.py` | `MaCrossover.on_event` |
| the shared run/verify core | `tessera/runner/cli.py` | `run_from_config` |
| write parquet + manifest | `tessera/runner/recorder.py`, `manifest.py` | `ParquetRecorder`, `write_manifest` |
| the end-to-end test | `tests/test_strategies_and_cli.py` | `test_cli_run_produces_a_verifiable_run_directory` |

---

## Answer these yourself

Cover the text and try these.

1. **How does the moving-average strategy compute its averages without ever seeing a
   table of past prices?** (Part 1. Two self-maintained ring buffers updated one day at a
   time.)

2. **Why does a datetime library appear at all if the whole engine uses integer
   nanoseconds?** (Part 4. Only inside the loader, as a temporary step to parse human date
   text into the integer — the conversion boundary. It never enters the engine.)

3. **What was the microsecond bug, and why does confining date handling to one place make
   it easy to catch?** (Part 4. Pandas defaulted to microseconds → timestamps 1000x too
   small → zero bars. One boundary = one place for such slips.)

4. **Why do the CLI and `verify` share `run_from_config`?** (Part 3. So running and
   reproducing follow one identical code path and can't drift apart.)

If those come out cleanly in your own words, you've got Task 8 cold.

---

## Mini-glossary

- **Strategy** — a trading idea implementing `on_event`; keeps its own state.
- **SMA (simple moving average)** — the average of the last N closes.
- **Ring buffer / deque** — a fixed-length memory that drops the oldest item as new ones arrive.
- **DataSource** — anything that yields time-ordered events (here, from a CSV).
- **Conversion boundary** — the single place human dates become integer nanoseconds.
- **Resolution** — the unit a time value is measured in (seconds, milliseconds, micro-, nano-).
- **CLI** — command-line interface (`tessera run`, `tessera verify`).
- **run_from_config** — the one function that turns a RunConfig into a live run.
