# Build log & file guide

A living document. It answers two questions at any point in the project:

1. **What are we working on, and what's done?** — see [Progress tracker](#progress-tracker).
2. **What does each file do?** — see [File-by-file guide](#file-by-file-guide).
3. **Why is it built this way (from scratch, no code)?** — see the learning guides
   in [`docs/learn/`](learn/). One per task, written to teach the concepts:
   - [Task 1 — events and the clock](learn/task1-events-and-clock.md)
   - [Task 2 — the event queue](learn/task2-the-event-queue.md)
   - [Task 3 — the strategy protocol and Context](learn/task3-strategy-and-context.md)
   - [Task 4 — portfolio accounting](learn/task4-portfolio-accounting.md)
   - [Task 5 — naive fill model and costs](learn/task5-fills-and-costs.md)
   - [Task 6 — the engine loop](learn/task6-the-engine-loop.md)
   - [Task 7 — recorder, config, manifest](learn/task7-recorder-config-manifest.md)
   - [Task 8 — strategies, CSV loader, and CLI](learn/task8-strategies-and-cli.md)

I update this after every task. It is written to be read top-to-bottom by someone
(you, an interviewer, future-me) who has never seen the code.

> **Continuing the project in a fresh session?** See
> [`SESSION_HANDOFF.md`](SESSION_HANDOFF.md) — it records the exact per-task
> workflow (explain-first vs. just-implement, verify, decisions log, learning
> guide, quiz, commit rules) so the work continues in the same style.

---

## The one-paragraph summary

**Tessera** is an event-driven backtesting engine. Timestamped market events flow
through an ordered queue; a strategy reacts to **one event at a time** and emits
orders; an execution model decides fills; the engine writes every outcome to a
record stream on disk; metrics and tearsheets are computed **afterwards** from those
records. The design goal is that every future feature is an *addition*, never a
rewrite — enforced by the nine "seams" in `ARCHITECTURE.md`.

---

## Progress tracker

Legend: ✅ done · 🔨 in progress · ⬜ not started

| Task | Component | Status | Notes |
|-----|-----------|--------|-------|
| 0 | Repo scaffolding | ✅ | Tree, config, tests stubbed. `pytest`/`ruff`/`mypy` all green. |
| 1 | Event types + clock (`core/`) | ✅ | Frozen slotted events, `ordering_key` total order, monotonic clock. 8 tests pass. |
| 2 | Event queue (`core/queue.py`) | ✅ | Heap k-way merge; O(k) memory, lazy, crashes on out-of-order sources. 13 tests pass. |
| 3 | Strategy protocol + Context (`strategy/`) | ✅ | Fresh immutable Context, minimal surface, Order + Strategy protocol. 19 tests pass. |
| 4 | Portfolio accounting (`portfolio/`) | ✅ | Average-cost book, mark-to-market accounting, flip/partial-fill handling. 23 tests pass. |
| 5 | Naive fill model + costs (`execution/`) | ✅ | Next-open fills, pending/latency queue at 0, bps costs, one-shot limits. 29 tests pass. |
| 6 | Engine loop (`core/engine.py`) | ✅ | Fixed per-iteration order (fill-past → strategy → submit); Recorder protocol; determinism live. 34 tests pass. |
| 7 | Recorder, config, manifest (`runner/`) | ✅ | RunConfig, Parquet/Null/Multi recorders, manifest write + verify. 41 tests pass. |
| 8 | Example strategies + CLI (`strategy/`, `runner/cli.py`) | ✅ | MA-crossover + reversal, CSV loader, `tessera run`/`verify`. Runs end-to-end. 45 tests pass. |
| 9 | Metrics + tearsheet (`metrics/`) | ⬜ | Computed from a run directory. |
| 10 | Data, run it, write it up | ⬜ | Real tickers, README, results. |

**Right now:** Tasks 0–8 complete (Tasks 0–7 committed; Task 8 not yet). The system
runs end-to-end from one command (`tessera run ... && tessera verify <dir>`). Next is
Task 9 (metrics + tearsheet in `metrics/`), a "just-implement" component computing the
equity curve, drawdown, Sharpe, turnover, etc. from a run directory, plus a tearsheet
figure and a `tessera report` command.

---

## The nine seams, in one line each

These are the fixed interfaces. Breaking one is a redesign, not an edit.

1. **Everything is a timestamped event** — data flows as events through a queue, never a dataframe.
2. **Strategy never touches the data store** — it gets one event + a read-only context.
3. **Strategies emit intent (`Order`), never outcomes** — no fill prices or PnL in strategy code.
4. **Execution is a swappable interface** — `FillModel` / `CostModel` protocols.
5. **Latency exists from day one, even at zero** — orders pass through a pending queue.
6. **Engine emits a stream, doesn't return a result** — it pushes to a `Recorder`.
7. **A run is a `RunConfig` in, a `Manifest` out** — engine knows nothing about where configs come from.
8. **Metrics are computed offline** from the record stream.
9. **One clock, owned by the engine** — no wall-clock, no unseeded randomness.

---

## File-by-file guide

Status of each file's *contents*: **stub** = docstring only, no logic yet.
As tasks land, entries move from stub → a real description of behavior.

### `tessera/core/` — the hot loop (mypy-strict)
| File | Owns | Status |
|------|------|--------|
| `events.py` | Frozen slotted `Event`/`Bar`/`Trade`/`Quote` (int-ns timestamps) **plus** `ordering_key(ts, source_priority, seq)` — the total order that makes co-timestamped merges deterministic. `BookUpdate` deferred. | **done** |
| `clock.py` | `Clock`: monotonic non-decreasing simulated time. Equal ts allowed; backward ts or reading before start raises `ClockError`; non-int ts raises `TypeError`. Never reads the wall clock. | **done** |
| `queue.py` | `merge(sources)`: a lazy heap-based k-way merge of per-source-sorted streams into one totally-ordered stream. O(k) memory; `QueueError` if a source is internally out of order. | **done** |
| `engine.py` | `run(events, strategy, fill_model, book, recorder)`: the loop. Per event — advance clock, fill past orders at the open, apply + record, mark, call strategy, submit new orders, record portfolio. Also owns the `Recorder` protocol. Emits `fill`/`order`/`portfolio` records; returns nothing. | **done** |

### `tessera/execution/` — orders → fills (mypy-strict)
| File | Owns | Status |
|------|------|--------|
| `base.py` | The `Fill` record and the `FillModel` / `CostModel` protocols. `CostModel.cost` narrowed to `(order, fill_price, qty)`; `ctx: MarketCtx` deferred (flagged). | **done** |
| `naive.py` | `NaiveFillModel`: market orders fill at the next bar's open via a pending queue with `arrival_ts = submit_ts + latency_ns` (default 0); limit orders get one shot at the next open. | **done** |
| `costs.py` | `BpsCostModel(bps)`: `bps x 1e-4 x price x |qty|` charged on each fill. | **done** |

### `tessera/portfolio/` — positions & accounting
| File | Owns | Status |
|------|------|--------|
| `book.py` | `Book` (cash, positions, realized PnL) + `Position` (qty, avg cost). `apply_fill(symbol, qty, price, cost)` applies fills one at a time (average cost), splits a zero-crossing fill into close+open, expenses fees to realized. | **done** |
| `accounting.py` | Pure functions over a `Book` + latest `prices`: `market_value`, `equity`, `unrealized_pnl`. Never mutates the book; marks at last observed price. | **done** |

### `tessera/strategy/` — user strategy surface
| File | Owns | Status |
|------|------|--------|
| `base.py` | `Order` (frozen intent), `Strategy` protocol (`on_event(event, ctx) -> list[Order]`), and `Context` — a fresh, frozen, per-event snapshot exposing only `ts`, `cash`, read-only `positions`, and `position(symbol)`. Look-ahead impossible by absence of any future channel. | **done** |
| `examples/ma_crossover.py` | `MaCrossover(fast, slow)`: long when fast SMA > slow SMA, flat otherwise. Keeps its own two ring buffers. | **done** |
| `examples/reversal.py` | `Reversal(qty)`: long after a down day, short after an up day. Keeps only the previous close. | **done** |
| `examples/ma_crossover.py` | Moving-average crossover example (self-maintained rolling state). | stub |
| `examples/reversal.py` | Mean-reversion example (buy after down days, sell after up days). | stub |

### `tessera/data/` — sources → events
| File | Owns | Status |
|------|------|--------|
| `loader.py` | The `DataSource` protocol: a per-symbol source that yields a time-ordered `Iterator[Event]`. | **done** |
| `sources/csv_bars.py` | `CsvBarSource` reads a daily-bar CSV → `Bar` events; `to_epoch_ns` converts dates to int-ns (forced to `ns` resolution). The one human-time→int boundary. | **done** |

### `tessera/runner/` — configs, records, manifests, CLI
| File | Owns | Status |
|------|------|--------|
| `config.py` | `RunConfig` (frozen, seam-7 fields) + `to_dict`/`from_dict` for the manifest. | **done** |
| `manifest.py` | `write_manifest`/`read_manifest` (config, git commit, data hash, versions, seed, timings) and `verify(run_dir, run_fn)` re-running the config and comparing parquet content. | **done** |
| `recorder.py` | `ParquetRecorder` (buffer by kind → fills/orders/portfolio.parquet), `NullRecorder`, `MultiRecorder`. (Protocol lives in `core/engine.py`.) | **done** |
| `cli.py` | `tessera run` (build a RunConfig → run → write parquet + manifest) and `tessera verify`. `run_from_config` is the single reproducible core the CLI and `verify` share. | **done** |

### `tessera/metrics/` — offline analysis
| File | Owns | Status |
|------|------|--------|
| `returns.py` | Metrics from a run directory: equity curve, drawdown, Sharpe, turnover, hit rate. | stub |
| `tearsheet.py` | A single matplotlib figure summarizing a run. | stub |

### `tests/` — the three load-bearing invariants
| File | Asserts | Status |
|------|---------|--------|
| `test_no_lookahead.py` | Cheating strategies (peek future / forge cash / mutate positions) all raise; a legit rolling-mean strategy works; Context is an immutable snapshot. **6 tests.** | **done** |
| `test_determinism.py` | Two identical runs produce an identical record stream (byte-identical files come with Task 7). **2 tests.** | **done** |
| `test_engine.py` | End-to-end run records fills/orders/portfolio; a bar-0 order fills at bar-1's open; final equity reflects fill + mark. **3 tests.** | **done** |
| `test_runner.py` | Config round-trip; Null/Multi recorders; ParquetRecorder writes fills/orders/portfolio; manifest write+read; verify passes on identical rerun and fails on divergence; data hash is content-sensitive. **7 tests.** | **done** |
| `test_strategies_and_cli.py` | MA-crossover long→flat, reversal down/up trading, CSV loader date→int-ns, and `tessera run` produces a verifiable run directory. **4 tests.** | **done** |
| `test_accounting.py` | Cash + mark-to-market = equity through a partial fill, a long→short flip, and a close; fees are a realized drag; short-cover profit; average-cost blend. **4 tests.** | **done** |
| `test_events_clock.py` | Clock moves forward only (backward raises); identical-ts events order deterministically; events are frozen + slotted. **8 tests.** | **done** |
| `test_queue.py` | Three sources merge in order; identical ts break by source priority; out-of-order source raises; merge is lazy over infinite sources. **5 tests.** | **done** |
| `test_fills.py` | Next-open fill (not current close), latency delays fill, symbol matching, bps cost, non-bar events don't fill, one-shot limit crossing. **6 tests.** | **done** |

### Project root & docs
| File | Purpose |
|------|---------|
| `CLAUDE.md` | House rules for how code gets written here. |
| `pyproject.toml` | Deps, ruff, pytest, mypy config (strict on `core` + `execution`). |
| `.gitignore` | Ignores `runs/`, `data/`, and build/editor junk. |
| `docs/ARCHITECTURE.md` | The nine seams. The source of truth for design. |
| `docs/WEEK1.md` | The task-by-task plan for week 1. |
| `docs/decisions.md` | Decision log — what/why for each non-trivial choice. |
| `docs/PROGRESS.md` | **This file.** Build log + file guide. |

---

## Changelog

- **Task 0 — repo scaffolding.** Created the full `tessera/` tree, config files,
  `.gitignore`, decisions log, and stubbed tests. Verified `pytest` (0 tests, no
  error), `ruff`, and `mypy --strict` on `core`/`execution` all pass. Promoted
  `CLAUDE.md` to root and the architecture/plan docs into `docs/`.
- **Task 1 — event types + clock.** Decisions (see `decisions.md`): total-order
  tie-break `(ts, source_priority, seq)`, frozen slotted dataclass events,
  time-conversion confined to the data layer. Implemented `core/events.py`
  (`Event`/`Bar`/`Trade`/`Quote` + `ordering_key`) and `core/clock.py` (monotonic
  `Clock`, `ClockError`). Added `tests/test_events_clock.py` — 8 tests green;
  ruff and mypy-strict clean.
- **Task 2 — event queue.** Decisions (see `decisions.md`): heap-based k-way merge
  over per-source-sorted streams, crash loudly on an internally out-of-order source.
  Implemented `core/queue.py` (`merge`, `QueueError`) — lazy, O(k) memory, reusing
  `ordering_key` for deterministic ties. Added `tests/test_queue.py` — 5 tests;
  total 13 green; ruff and mypy-strict clean.
- **Task 3 — strategy protocol + Context.** Decisions (see `decisions.md`): prevent
  look-ahead by absence (no future channel on Context), fresh immutable Context per
  event, minimal surface. Implemented `strategy/base.py` (`Order`, `Strategy`
  protocol, `Context`) and the load-bearing `tests/test_no_lookahead.py` — 6 tests;
  total 19 green; ruff and mypy-strict clean.
- **Task 4 — portfolio accounting.** Decisions (see `decisions.md`): average-cost
  basis, fills as primitives, zero-crossing split, fees expensed to realized.
  Implemented `portfolio/book.py` (`Book`, `Position`, `apply_fill`) and
  `portfolio/accounting.py` (`market_value`, `equity`, `unrealized_pnl`). Added the
  load-bearing `tests/test_accounting.py` — 4 tests; total 23 green; ruff and
  mypy-strict clean.
- **Task 5 — naive fill model + costs.** Decisions (see `decisions.md`): next-open
  fills, pending/latency queue at 0, `CostModel` signature narrowed (defer `ctx`),
  one-shot limits. Implemented `execution/base.py` (`Fill`, `FillModel`/`CostModel`
  protocols), `execution/naive.py` (`NaiveFillModel`), `execution/costs.py`
  (`BpsCostModel`). Added `tests/test_fills.py` — 6 tests; total 29 green; ruff and
  mypy-strict clean.
- **Task 6 — engine loop.** Decisions (see `decisions.md`): per-iteration order
  clock→fill-past→apply→strategy→submit→record (same-bar look-ahead impossible),
  Recorder protocol in core (dependency inversion), latest-price marking. Implemented
  `core/engine.py` (`run`, `Recorder` protocol). Added `tests/test_engine.py` (3) and
  the load-bearing `tests/test_determinism.py` (2); total 34 green; ruff and
  mypy-strict clean.
- **Task 7 — recorder, config, manifest.** Just-implement (`runner/`). Implemented
  `runner/config.py` (`RunConfig` + dict round-trip), `runner/recorder.py`
  (`ParquetRecorder`, `NullRecorder`, `MultiRecorder`), `runner/manifest.py`
  (write/read + `verify` via injected run_fn, git/hash/versions/timings). Decisions
  D21–D23. Added `tests/test_runner.py` — 7 tests; total 41 green; ruff and
  mypy-strict clean.
- **Task 8 — strategies + CSV loader + CLI.** Just-implement. Implemented
  `strategy/examples/ma_crossover.py` + `reversal.py` (self-maintained state),
  `data/loader.py` (`DataSource`), `data/sources/csv_bars.py` (`CsvBarSource`,
  `to_epoch_ns`), and `runner/cli.py` (`tessera run`/`verify`, `run_from_config`).
  Decisions D24–D26 (incl. the pandas microsecond-resolution bug). Verified live
  end-to-end. Added `tests/test_strategies_and_cli.py` — 4 tests; total 45 green;
  ruff and mypy-strict clean.
