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
| 6 | Engine loop (`core/engine.py`) | ⬜ | The centerpiece. Ordering matters. |
| 7 | Recorder, config, manifest (`runner/`) | ⬜ | Reproducible run directories. |
| 8 | Example strategies + CLI (`strategy/`, `runner/cli.py`) | ⬜ | `tessera run ...`. |
| 9 | Metrics + tearsheet (`metrics/`) | ⬜ | Computed from a run directory. |
| 10 | Data, run it, write it up | ⬜ | Real tickers, README, results. |

**Right now:** Tasks 0–5 complete (Tasks 0–4 committed; Task 5 not yet). Next is
Task 6 (the engine loop — the centerpiece), which ties queue + clock + strategy +
fill model + portfolio + recorder together. A `core/` component, so it will be
explained (the exact ordering of operations, and a plausible-but-wrong alternative)
and wait for your decision before any code is written.

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
| `engine.py` | The main loop tying together queue, clock, strategy, fills, portfolio, recorder. | stub |

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
| `examples/ma_crossover.py` | Moving-average crossover example (self-maintained rolling state). | stub |
| `examples/reversal.py` | Mean-reversion example (buy after down days, sell after up days). | stub |

### `tessera/data/` — sources → events
| File | Owns | Status |
|------|------|--------|
| `loader.py` | The `DataSource` protocol: a source that yields `Iterator[Event]`. | stub |
| `sources/csv_bars.py` | CSV daily-bar loader; converts human timestamps to integer-ns `Bar` events. | stub |

### `tessera/runner/` — configs, records, manifests, CLI
| File | Owns | Status |
|------|------|--------|
| `config.py` | `RunConfig`: the single immutable description of a run. | stub |
| `manifest.py` | Manifest write + verify: reproducibility metadata per run directory. | stub |
| `recorder.py` | `Recorder` protocol; `ParquetRecorder`, `NullRecorder`, `MultiRecorder`. | stub |
| `cli.py` | The typer CLI entry point: `tessera run` and `tessera report`. | stub |

### `tessera/metrics/` — offline analysis
| File | Owns | Status |
|------|------|--------|
| `returns.py` | Metrics from a run directory: equity curve, drawdown, Sharpe, turnover, hit rate. | stub |
| `tearsheet.py` | A single matplotlib figure summarizing a run. | stub |

### `tests/` — the three load-bearing invariants
| File | Asserts | Status |
|------|---------|--------|
| `test_no_lookahead.py` | Cheating strategies (peek future / forge cash / mutate positions) all raise; a legit rolling-mean strategy works; Context is an immutable snapshot. **6 tests.** | **done** |
| `test_determinism.py` | Same config + seed twice → byte-identical records. | stub |
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
