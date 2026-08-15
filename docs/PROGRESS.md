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
   - [Task 9 — metrics and the tearsheet](learn/task9-metrics-and-tearsheet.md)
   - [Task 10 (Parts 1–2) — real data and position sizing](learn/task10-real-data-and-sizing.md)
   - [Task 11 — the midnight-bar leak and session-close stamping](learn/task11-session-close-stamping.md)
   - [Task 12 — the margin / leverage check](learn/task12-margin-and-leverage.md)

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
| 9 | Metrics + tearsheet (`metrics/`) | ✅ | Returns/drawdown/Sharpe/turnover from a run dir; 4-panel tearsheet; `tessera report`. 51 tests pass. |
| 10 | Data, run it, write it up | 🔨 | **Parts 1–2 done**: six real adjusted tickers (2005–2026) validated + all gates pass; both strategies run across all six at 0/5 bps via the CLI; fixed-share→fixed-fractional sizing bug found and fixed. **Part 3 (README) deferred to after Tasks 11–12.** |
| 11 | Fix the D32 midnight-bar leak (`data/`, `runner/`) | ✅ | Bars stamped at the 16:00 ET session close (UTC), not midnight — closes the latent future-leak when a daily source is merged with intraday. DST-correct (21:00 UTC winter / 20:00 summer); behavior-preserving on the single-source grid. Manifest now versions the timestamp convention so `verify` raises a loud `ConventionMismatch` instead of silently reproducing a different run (D42); option B, storing dates in `RunConfig`, scheduled. 61 tests pass. |
| 12 | Margin / leverage check (`portfolio/`, `core/`) | ✅ | Closes D35 (the only real return-inflation vector): the engine rejects a fill that would push gross exposure above `max_leverage × equity` (default 1×), dropping the order and emitting seam-6's `reject` record. Covers shorts; de-risking always allowed. Pure predicate `accounting.admits_fill`. Zero rejections + identical metrics on the grid. 63 tests pass. |

**Right now:** Tasks 0–9 complete and committed (through the audit). Task 10 **Parts 1–2
done**: real adjusted daily bars for AAPL/MSFT/JPM/XOM/KO/NVDA (2005–2026) are installed in
`data/` and pass every validation + corporate-action gate; both example strategies run across
all six symbols at 0 and 5 bps through the real `tessera run`/`report` CLI. A position-sizing
bug surfaced by the real prices — fixed **100-share** sizing made results a function of price
level, not strategy — was fixed to **fixed-fractional notional** (`qty = target_frac ×
initial_cash / close`). **Part 3 (the README) is deferred until after Tasks 11–12** so it can
reflect what those add. 58 tests green; ruff + mypy-strict clean.

**Task 11 (done):** the D32 midnight-bar leak is closed — daily bars are now stamped at their
**16:00 ET session close** (converted to UTC, DST-correct) instead of UTC midnight, so a daily
bar can no longer sort ahead of that day's intraday ticks once sources are merged. Behavior-
preserving on the single-source grid (only the `ts` column shifts; metrics reproduce exactly).
The convention change silently broke `verify()` on pre-Task-11 runs (it re-ran a stored midnight
`end_ts` under the new rule and dropped the final bar → bare `False`); fixed by **versioning the
timestamp convention in the manifest (D42)** so `verify` raises a loud, explained
`ConventionMismatch` instead. 61 tests green; ruff + mypy-strict clean.

**Task 12 (done):** the D35 no-margin hole is closed — the engine rejects any fill that would push
gross exposure above `max_leverage × equity` (default 1×), dropping the order and emitting seam-6's
`reject` record (its first use). The rule covers **shorts** (a cash floor alone wouldn't), marks
look-ahead-safe, and always permits **de-risking** so a drift-over-limit account never locks up. The
audit's 10M-notional exploit (long and short) is now a passing regression test. Grid: zero rejections,
byte-identical metrics. The manifest also records per-kind `record_counts`, so a run states its reject
count (0 or N) affirmatively rather than by the absence of a file. 64 tests green; ruff + mypy-strict clean.

**Deferred / scheduled — Task 11 option B (the *cure* for the boundary reinterpretation).** D42's
convention check *detects* the break loudly but does not *cure* it: a config still stores raw ns
boundaries, so a future convention change would again reinterpret them. The cure is to **store
calendar dates (not raw ns) in `RunConfig`** so a boundary means "that date's session" under any
convention. This is a seam-7 schema change with a back-compat migration, so it is **scheduled for
when `RunConfig` is next modified, or when intraday bar-splitting lands, whichever comes first.**
Recorded as a decision (D42), not an omission.

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
| `engine.py` | `run(events, strategy, fill_model, book, recorder)`: the loop. Per event — advance clock, fill past orders at the open (rejecting any that would breach the book's leverage cap via `accounting.admits_fill`, emitting a `reject` record instead of applying), apply + record, mark, call strategy, submit new orders, record portfolio. Also owns the `Recorder` protocol. Emits `fill`/`order`/`portfolio`/`reject` records; returns nothing. | **done** |

### `tessera/execution/` — orders → fills (mypy-strict)
| File | Owns | Status |
|------|------|--------|
| `base.py` | The `Fill` record and the `FillModel` / `CostModel` protocols. `CostModel.cost` narrowed to `(order, fill_price, qty)`; `ctx: MarketCtx` deferred (flagged). | **done** |
| `naive.py` | `NaiveFillModel`: market orders fill at the next bar's open via a pending queue with `arrival_ts = submit_ts + latency_ns` (default 0); limit orders get one shot at the next open. | **done** |
| `costs.py` | `BpsCostModel(bps)`: `bps x 1e-4 x price x |qty|` charged on each fill. | **done** |

### `tessera/portfolio/` — positions & accounting
| File | Owns | Status |
|------|------|--------|
| `book.py` | `Book` (cash, positions, realized PnL, **`max_leverage`** cap) + `Position` (qty, avg cost). `apply_fill(symbol, qty, price, cost)` applies fills one at a time (average cost), splits a zero-crossing fill into close+open, expenses fees to realized. The book stores the leverage cap but does not enforce it — the engine does, via `accounting.admits_fill` (D43). | **done** |
| `accounting.py` | Pure functions over a `Book` + latest `prices`: `market_value`, `equity`, `unrealized_pnl`, and **`admits_fill`** (whether a fill keeps gross exposure within `max_leverage × equity`, with a de-risking carve-out; look-ahead-safe marks). Never mutates the book; marks at last observed price. | **done** |

### `tessera/strategy/` — user strategy surface
| File | Owns | Status |
|------|------|--------|
| `base.py` | `Order` (frozen intent), `Strategy` protocol (`on_event(event, ctx) -> list[Order]`), and `Context` — a fresh, frozen, per-event snapshot exposing only `ts`, `cash`, read-only `positions`, and `position(symbol)`. Look-ahead impossible by absence of any future channel. | **done** |
| `examples/ma_crossover.py` | `MaCrossover(fast, slow, target_frac, initial_cash)`: long when fast SMA > slow SMA, flat otherwise; keeps its own two ring buffers. Sizes by **fixed-fractional notional** (`qty = target_frac × initial_cash / close`), not a fixed share count. | **done** |
| `examples/reversal.py` | `Reversal(target_frac, initial_cash)`: long after a down day, short after an up day; keeps only the previous close. Targets `±target_frac × initial_cash` notional and orders the **delta** to target, so a flip crosses zero in one order. | **done** |
| `examples/buy_and_hold.py` | `BuyAndHold(target_frac, initial_cash)`: buy once on the first bar (sized at that close) and hold forever — the benchmark baseline. Used by `test_validation.py` as the engine's ground-truth subject. Not wired into the CLI yet (D44). | **done** |

### `tessera/data/` — sources → events
| File | Owns | Status |
|------|------|--------|
| `loader.py` | The `DataSource` protocol: a per-symbol source that yields a time-ordered `Iterator[Event]`. | **done** |
| `sources/csv_bars.py` | `CsvBarSource` reads a daily-bar CSV → `Bar` events; `to_epoch_ns` converts dates to int-ns (forced to `ns` resolution). The one human-time→int boundary. Stamps each bar at its **16:00 ET session close** (DST-correct via `America/New_York`→UTC), not UTC midnight, so a daily bar can't leak ahead of that day's intraday ticks (Task 11, D41). Fed **real split/dividend-adjusted Stooq bars** (2005–2026) in `data/`. | **done** |

### `tessera/runner/` — configs, records, manifests, CLI
| File | Owns | Status |
|------|------|--------|
| `config.py` | `RunConfig` (frozen, seam-7 fields) + `to_dict`/`from_dict` for the manifest. | **done** |
| `manifest.py` | `write_manifest`/`read_manifest` (config, git commit, data hash, versions, seed, **timestamp convention**, **per-kind `record_counts`**, timings) and `verify(run_dir, run_fn)` re-running the config and comparing parquet content. `verify` raises **`ConventionMismatch`** when a run's stored timestamp convention differs from the current code's, rather than silently reproducing a different run (D42). `record_counts` states the reject count affirmatively (D43). | **done** |
| `recorder.py` | `ParquetRecorder` (buffer by kind → fills/orders/portfolio/**reject**.parquet; `record_counts()` for affirmative provenance), `NullRecorder`, `MultiRecorder`. (Protocol lives in `core/engine.py`.) | **done** |
| `cli.py` | `tessera run` (RunConfig → run → parquet + manifest), `tessera verify`, and `tessera report` (metrics line + tearsheet PNG). `run_from_config` is the shared reproducible core. `_make_strategy` **injects `config.initial_cash`** into strategies that accept it, so fractional-notional sizing scales with the account. | **done** |

### `tessera/metrics/` — offline analysis
| File | Owns | Status |
|------|------|--------|
| `returns.py` | Reads `portfolio.parquet`/`fills.parquet`; `compute_metrics` (total/annualized return, vol, Sharpe, max drawdown, turnover, hit rate, win/loss) + `equity_curve`/`drawdown_series`/`rolling_sharpe`. Offline, never the engine. | **done** |
| `tearsheet.py` | `render(run_dir)`: a 4-panel PNG (equity, underwater drawdown, rolling 60-period Sharpe, return histogram). Headless Agg backend. | **done** |

### `tests/` — the three load-bearing invariants
| File | Asserts | Status |
|------|---------|--------|
| `test_no_lookahead.py` | Cheating strategies (peek future / forge cash / mutate positions) all raise; a legit rolling-mean strategy works; Context is an immutable snapshot. **6 tests.** | **done** |
| `test_determinism.py` | Two identical runs produce an identical record stream (byte-identical files come with Task 7). **2 tests.** | **done** |
| `test_engine.py` | End-to-end run records fills/orders/portfolio; a bar-0 order fills at bar-1's open; final equity reflects fill + mark. **3 tests.** | **done** |
| `test_runner.py` | Config round-trip; Null/Multi recorders; ParquetRecorder writes fills/orders/portfolio; manifest write+read; verify passes on identical rerun and fails on divergence; data hash is content-sensitive; verify reports a `ConventionMismatch` (not a bare False) when a run's timestamp convention differs, and a tripwire pins the convention string to `to_epoch_ns`'s actual mapping (D42); **the manifest records the reject count affirmatively — 0 on a clean run, 1 when the leverage cap trips (D43).** **10 tests.** | **done** |
| `test_strategies_and_cli.py` | MA-crossover long→flat, reversal down/up trading, CSV loader stamps bars at the 16:00 ET session close (winter + summer, DST-correct), a merged daily bar does not leak ahead of same-day intraday ticks, and `tessera run` produces a verifiable run directory. **5 tests.** | **done** |
| `test_metrics.py` | Known-value total return + max drawdown, drawdown non-positive, turnover/trade count, Sharpe annualisation, tearsheet writes a PNG, missing-fills handling. **6 tests.** | **done** |
| `test_audit.py` | Audit regressions: Sharpe hand-value (no √252 bug), fill-qty invariant, final-bar-order dropped, 300-sequence accounting sweep, verify-on-changed-input, **the leverage attack rejected long *and* short (D43, replaces the old no-margin limitation test)**, empty-run limitation. **7 tests.** | **done** |
| `test_accounting.py` | Cash + mark-to-market = equity through a partial fill, a long→short flip, and a close; fees are a realized drag; short-cover profit; average-cost blend; **margin admits within the cap and rejects the 10M long/short beyond it; the de-risking carve-out lets an over-limit (drift-induced) account reduce but not increase exposure (D43).** **6 tests.** | **done** |
| `test_events_clock.py` | Clock moves forward only (backward raises); identical-ts events order deterministically; events are frozen + slotted. **8 tests.** | **done** |
| `test_queue.py` | Three sources merge in order; identical ts break by source priority; out-of-order source raises; merge is lazy over infinite sources. **5 tests.** | **done** |
| `test_fills.py` | Next-open fill (not current close), latency delays fill, symbol matching, bps cost, non-bar events don't fill, one-shot limit crossing. **6 tests.** | **done** |
| `test_validation.py` | The engine vs **independently computable ground truth** (D44): buy-and-hold on all six real symbols reproduces pandas total/annualized return, vol, and max drawdown to machine precision, with the only residual being the next-open entry (asserted == the first overnight gap); plus analytic anchors — constant-return equity `= initial×(1+r)^k` and an integer-exact round-trip PnL. **3 tests.** | **done** |

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
- **Task 9 — metrics + tearsheet.** Just-implement (`metrics/`). Implemented
  `metrics/returns.py` (`compute_metrics` + series helpers, all read from a run
  directory's parquet) and `metrics/tearsheet.py` (`render` → 4-panel PNG, Agg
  backend). Added `tessera report` to the CLI (lazy matplotlib import). Decisions
  D27–D30. Verified live (PNG rendered + visually checked). Added
  `tests/test_metrics.py` — 6 tests; total 51 green; ruff and mypy-strict clean.
- **Audit round 1 (post-Task-9).** Ran the full `docs/AUDIT.md` adversarially with
  evidence. Result: **0 bugs** — the alleged √252 Sharpe bug is disproven by a
  hand-computed regression test (formula is correct). Recorded 6 findings as
  decisions D31–D36 (Sharpe annualisation basis; midnight-bar future-leak risk;
  byte→content determinism; verify env-scope; no margin check; silent no-op runs),
  corrected ARCHITECTURE invariant 2, and added `tests/test_audit.py` — 7 tests;
  total 58 green. Findings written up in `docs/AUDIT.md`.
- **Task 10 Part 1 — real data + validation.** Just-implement (`data/`). Installed real
  split/dividend-adjusted Stooq daily bars for AAPL/MSFT/JPM/XOM/KO/NVDA into `data/`,
  replacing the synthetic sine wave. Widened the window to full history (2005–2026) to
  include the 2008 crisis. Built a validation table + gates (vol band, mean|r|/std ≪ 1,
  ~252 bars/yr, and a corporate-action split gate); **all pass** — 0 split-ratio artifacts
  despite NVDA's 4:1/10:1 splits, proving adjustment is intact. Decisions D37 (adjusted
  prices), D38 (2005+ scope), D39 (split gate). No code changes; no tests added.
- **Task 10 Part 2 — run it + sizing fix.** Ran both strategies × six symbols × {0,5} bps
  through the real `tessera run`/`report` CLI (24 runs) and verified three return identities
  by hand. Diagnosed that fixed **100-share** sizing made results a function of price level,
  not strategy (portfolio vol 0.48–3.75% vs underlying 18–48%; ma_crossover turnover spread
  9.1× on 1.18× trade-count spread; 12% of AAPL PnL pre-2015). Fixed both strategies to
  **fixed-fractional notional** (`qty = target_frac × initial_cash / close`), with the runner
  injecting `initial_cash`; strengthened the reversal test to pin the long→short flip
  (zero-crossing delta). Decision D40 (recorded, then **corrected**: daily rebalancing is
  0.72% of traded notional, not a cost amplifier — turnover is exposure, not a cost proxy).
  Still 58 tests green; ruff + mypy-strict clean. **Part 3 (README) deferred to after Tasks
  11–12.** Learn guide: `learn/task10-real-data-and-sizing.md`.
- **Task 11 — session-close bar stamping (D32 leak fix).** Explain-first (`core/`+`data/`);
  chose **option A** (move the timestamp) over **B** (split each bar into open/close events)
  and implemented in `data/` only. `CsvBarSource` now stamps daily bars at their **16:00 ET
  session close** converted to UTC — 21:00 in winter (EST), 20:00 in summer (EDT) — instead of
  UTC midnight, so a daily bar can no longer sort ahead of that day's intraday ticks once an
  intraday source is merged in (closing the latent D32 future-leak on the close). DST is handled
  by constructing the wall-clock close in `America/New_York` and converting (a fixed offset would
  be wrong twice a year); `zoneinfo` is stdlib, no new dependency. Behavior-preserving on the
  single-source grid — only the `ts` column shifts. Verified on the actual Task-10 grid window
  (AAPL 2005-01-03…2024-12-31, 0 bps): re-running ma_crossover and reversal reproduces the baseline
  `runs/task10p2_fixed/` rows to machine precision (total return, Sharpe, max drawdown, trade count
  all identical, incl. reversal's 5016 fills / 455× turnover). Found one intended behavior: `--start/
  --end` now bound at the session close on both ends, so a config storing a raw old-midnight `end_ts`
  reinterprets and drops the boundary bar — old runs must be re-parameterized by date to reproduce.
  Strengthened the loader test (pins winter+summer close instants) and added a
  merge test proving a daily bar sorts after same-day intraday trades. Decision D41; D32 marked
  resolved. Known limitations deferred to week-2 intraday work (option B's job): half-day early
  closes not modelled (no calendar dep), and fills stamped at the next close instant not the true
  open. Learn guide: `learn/task11-session-close-stamping.md`.
- **Task 11 (cont.) — reproducibility guard for the convention change (D42).** The D41 stamping
  change silently broke `verify()` on pre-Task-11 runs: because `verify` re-runs a config's stored
  **raw ns** boundaries, the grid's midnight `end_ts` (2024-12-31 00:00 UTC) now falls before that
  day's session-close bar (21:00 UTC), so a re-run drops the final bar and returns a bare `False`
  (confirmed empirically: replay 5032 rows vs stored 5033). Fixed with **option A**: the manifest
  now records a `timestamp_convention` (single source of truth in `csv_bars.py`, `session_close_v1`),
  and `verify` raises a loud, explained **`ConventionMismatch`** on a mismatch — closing the class of
  gap **D34** anticipated (drift in *our own* code that data-hash/versions can't see). Two hardening
  tests: one asserts a `midnight_v0` (and field-absent) manifest triggers a *specific* mismatch, not
  a generic False; a tripwire pins the convention string to `to_epoch_ns`'s actual mapping so
  changing the mapping without bumping the string breaks a test. **A detects; option B (store dates
  in `RunConfig`) cures and is scheduled** for the next `RunConfig` change or intraday bar-splitting.
  61 tests green; ruff + mypy-strict clean.
- **Task 12 — margin / leverage check (D35 fix).** Explain-first (`portfolio/`+`core/`). The engine
  now consults a pure `accounting.admits_fill(book, symbol, signed_qty, price, prices, cost)` before
  applying each fill and **rejects** (drops + records seam-6's `reject`) any that would push gross
  exposure above `book.max_leverage × equity` (default 1×). Chosen over enforcing inside
  `Book.apply_fill` (would grow a reject channel + pull marks into the book) and over the fill model
  (would need a seam-4 change); `apply_fill` and the fill model are untouched. The rule covers shorts
  (gross exposure, not a cash floor — a short generates cash), marks the traded symbol at its fill
  price and others at last close (look-ahead-safe), and includes a **de-risking carve-out**: a fill
  that doesn't increase gross exposure is always admitted, so an account pushed over the cap by
  mark-to-market drift can reduce and never locks up. Limit lives on `Book` (default 1.0), not
  `RunConfig`, to avoid tripping the Task-11 option-B trigger. Decision D43. Tests: predicate + the
  drift-over-limit carve-out (both directions) in `test_accounting.py`; the audit's 10M long/short
  exploit as a rejection regression in `test_audit.py` (replacing `test_no_margin_check…`). Verified:
  grid AAPL ma_crossover + reversal @0bps re-run with **zero rejections and byte-identical metrics**.
  Follow-up: since a clean run writes no `reject.parquet` (ambiguous with a broken recorder), the
  manifest now records per-kind `record_counts` with `reject` always seeded, so **"0 rejections" is
  affirmed in provenance, not inferred** (`ParquetRecorder.record_counts()`; tested both ways).
  64 tests green; ruff + mypy-strict clean. Learn guide: `learn/task12-margin-and-leverage.md`.
- **Validation suite — the engine vs independent ground truth (D44).** Just-implement
  (`strategy/examples/` + `tests/`). Added a `BuyAndHold` benchmark strategy and
  `tests/test_validation.py`, which prove the engine reads the instrument correctly rather than
  matching unreproducible published returns. (1) Buy-and-hold on all six real symbols reproduces
  the total return, annualized return, annualized vol, and max drawdown computed directly from the
  same bars in numpy/pandas — to **0.0 (machine precision)** for all six — with the only permitted
  discrepancy from a naive first-close hold being the next-open entry, asserted to equal **exactly
  the first overnight gap** (AAPL −0.7557%, MSFT/KO 0.0000%). (2) Analytic anchors: constant-return
  buy-and-hold equity `= initial×(1+r)^k` (to 1e-12) and an integer-exact round trip (buy 10@100,
  sell 10@110 → realized PnL 100). Recorded the one interaction: a fully-invested hold sized at the
  prior close can read >1× gross at the next-open fill on a gap-up day (AAPL/XOM/NVDA), so the
  buy-and-hold validation relaxes the Task-12 cap (an entry-timing artifact, cap tested elsewhere).
  67 tests green; ruff + mypy-strict clean.
