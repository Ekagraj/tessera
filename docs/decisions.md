# Decision log

Short entries recording non-trivial design choices: what was decided, the
alternatives, why this one, and what would make us revisit.

---

## Task 1: event types and the clock

**Raw material — rewrite in your own words.**

### D1. Co-timestamped events use a total order `(ts, source_priority, seq)`
Decided: when two events share a timestamp, break the tie by the source's fixed
priority (its index in the run's source list), then by per-source read order
(`seq`). Alternatives: break ties by event-type priority (undefined when two
same-type events collide), or lean on the heap's insertion order (not stable for
equal keys — a silent determinism bug). Chose the total order because it makes a
merge reproducible regardless of heap internals, which is the whole point of
`test_determinism`. Revisit if we ever need a *domain-meaningful* co-timestamp
order (e.g. quotes must precede trades at the same ts) — then priority becomes a
per-type rule, not just per-source.

### D2. Events are frozen, slotted dataclass subtypes
Decided: `Event` base with `Bar`/`Trade`/`Quote` subclasses, `frozen=True,
slots=True`, primitive fields only. Alternatives: a tagged-union struct (fat,
weaker type safety) or struct-of-arrays / numpy columns (fastest, but a big
complexity jump and the strategy API wants one event at a time anyway). Chose the
dataclass hierarchy because it is readable, gives clean `isinstance` dispatch,
and — critically — keeps the loop boundary to plain structs of primitives, which
is exactly what a later Rust port needs. Revisit when per-event Python allocation
shows up in a profiler at tick scale; SoA can then slot in behind the queue
without touching strategy code.

### D3. Human-time → integer-ns conversion lives in the data layer only
Decided: timestamps are integer nanoseconds everywhere inside the engine; parsing
of dates/datetimes happens exclusively in `DataSource` loaders, which emit events
that already carry `int` ts. Alternatives: a conversion helper inside `core`
(drags datetime/timezone concerns into the hot path) or lazy conversion in the
engine (datetimes in the loop — forbidden by seam 9). Chose the loader boundary
because it is the literal reading of the hard rule and keeps `core` free of
timezone traps. Revisit only if a source delivers pre-converted integer ts, in
which case the loader simply passes them through.

### On the clock itself
The clock is monotonic *non-decreasing*: equal timestamps are allowed (many events
share a ts) but a lower ts raises `ClockError` rather than silently reordering.
Reading `ts` before the first event also raises, so nothing can act as if time
were zero. A non-int ts (including `bool`, which is an `int` subclass) raises
`TypeError` — we validate the invariant at the clock, not just trust mypy.

---

## Task 2: the event queue

**Raw material — rewrite in your own words.**

### D4. The queue is a heap-based k-way merge over per-source-sorted streams
Decided: keep one pending event per source in a heap keyed by `ordering_key`; pop
the smallest, yield it, refill from that source. Alternatives: read every event
into one list and sort (O(N) memory — gigabytes at 50M events — plus a big upfront
sort stall, and it cannot stream a live feed), or pre-sort all sources into one
file the engine reads (fast reads but a stale-able build artifact that also cannot
handle live feeds). Chose the heap merge because memory is O(k) in the number of
sources regardless of total event count, the first event comes out immediately,
and a live feed is just another sorted iterator — so streaming later is "add a
source," not a rewrite. Revisit with the pre-sorted-file approach only as a caching
optimisation for repeated runs over static history.

### D5. An internally out-of-order source crashes loudly
Decided: if a source yields an event whose `ts` is below its own previous `ts`, the
queue raises `QueueError` naming the source and both timestamps. The alternative —
buffering and re-sorting each source internally — hides a real data bug and
reintroduces the O(N) memory/latency cost the heap merge exists to avoid. Chose the
crash because out-of-order source data means the input is wrong, and catching it at
the boundary is far cheaper than debugging a silently mis-ordered backtest. Revisit
only if we ever ingest a source that is legitimately unsorted, which would get its
own explicit sorting loader rather than weakening the queue.

### Why the heap never compares events
Heap entries are `(ordering_key, event)`. Because `ordering_key` is a *total* order
and globally unique (source priority differs across sources, seq differs within a
source), two entries never tie on the key, so Python never falls through to
comparing the `Event` objects — which are intentionally not orderable.

---

## Task 3: the strategy protocol and Context

**Raw material — rewrite in your own words.**

### D6. Look-ahead is prevented by *absence*, not by checks
Decided: a strategy only ever receives `on_event(event, ctx)`, and `ctx` holds only
present-time state (`ts`, `cash`, `positions`) with no reference to the data source,
the queue, or future events. We do not police look-ahead with runtime checks; we make
it impossible by never handing the strategy a channel to the future. History is the
strategy's own responsibility — it maintains a rolling buffer as instance state. The
rejected alternative was a convenient "give me the last N bars" API on Context, which
reintroduces the engine deciding lookback and is a slippery slope back toward handing
over a window that contains the future. Revisit only if a legitimate need arises that
cannot be met by the strategy keeping its own state — which would be a red flag.

### D7. Context is a fresh immutable snapshot per event
Decided: build a new frozen, slotted `Context` each event; snapshot `positions` (copy
then wrap in a read-only `MappingProxyType`). Alternative: reuse one mutable Context
with read-only views to avoid per-event allocation (fastest), at the cost of a live
object that surprises a strategy caching it across events, plus a view class. Chose the
fresh snapshot because correctness and a clean "impossible by construction" story beat
a micro-optimisation at daily-bar volume (~2,500 events/year), and the snapshot cannot
be tampered with or observed changing after the fact. Revisit at tick scale if
profiling shows Context allocation matters — the swap is localised to how the engine
builds Context and touches no strategy code.

### D8. The Context surface is deliberately minimal
Decided: expose only `ts`, `cash`, and read-only `positions` (plus a `position(symbol)`
helper). Deliberately not exposed: the queue/data source, any history/window API,
future prices, the recorder, or any way to mutate cash/positions. Rejected a richer
surface (derived `equity`, per-lot average cost) because equity needs mark prices
(accounting's job, Task 4) and average cost is the strategy's own bookkeeping — both
widen the seam for convenience. Revisit per-field only when a concrete strategy needs
it, never speculatively.

### On Order staying a pure record
`Order` is a frozen dataclass with exactly the seam-3 fields (`symbol, side, qty, type,
limit_price=None, tag=""`) and no validation logic — strategies emit intent; the engine
and fill model own correctness. `type` has no default, matching the architecture, so an
order's kind is always explicit.

---

## Task 4: portfolio accounting

**Raw material — rewrite in your own words.**

### D9. Realized PnL uses average-cost, not FIFO lots
Decided: each position carries one blended `avg_price`; reducing/closing realizes
`(exit - avg) x closed_qty`. Alternative: FIFO lot queues that realize lot-by-lot
(needed for tax-lot accounting and matching broker statements). Chose average cost
because over a position's full life total PnL is identical either way — the method only
changes the *timing* of realized-vs-unrealized recognition and per-trade attribution —
and a research backtester cares about total PnL and the equity curve, not tax lots. It
is also O(1) state and deterministic. Revisit if we ever need tax-lot fidelity; FIFO can
slot in behind the same `Book` interface.

### D10. The book takes fills as primitives, not a Fill type
Decided: `Book.apply_fill(symbol, qty_signed, price, cost)` rather than importing an
execution `Fill` type. Alternative: define a shared `Fill` dataclass now. Chose
primitives to keep the portfolio decoupled from execution (Task 5), matching the
narrow-primitive-boundary principle from the Rust seam; the engine will translate a
`Fill` into this call later. Revisit only if the translation becomes non-trivial.

### D11. Fills apply one at a time; a zero-crossing fill is split
Partial fills need no special handling because accounting is per-fill and additive — the
equity identity holds after each one. A fill that opposes the current position realizes
PnL only on the *closed* portion; if it crosses through zero (e.g. long 100, sell 150),
the remainder opens a new position at the fill price, and realized PnL is computed only
on the 100 that closed. Same rule under average-cost or FIFO.

### D12. Fees are expensed to realized PnL immediately
Decided: commission drains cash *and* is subtracted from realized PnL, rather than being
capitalized into the position's cost basis. This keeps the internal cross-check exact:
`realized + unrealized == equity - initial_cash`. `avg_price` therefore reflects pure
trade price; fees show up as a realized drag the moment they are paid.

### On the marking price
Unrealized PnL and equity are computed in `accounting.py` (pure functions over the book)
using a `prices` map of the latest observed market price per symbol — the last bar's
close. The engine keeps that map current as events arrive, so a mark never uses a price
beyond the current clock. Equity = `cash + Σ(qty x mark_price)`.

---

## Task 5: naive fill model and costs

**Raw material — rewrite in your own words.**

### D13. Market orders fill at the next bar's open, never the current close
Decided: a decision made at bar N's close fills at bar N+1's open. Filling at bar N's
close is look-ahead — the strategy would trade at a price it only learned once the bar
was over, i.e. once that price was no longer actionable. The next price a bar-N decision
can really transact at is the next open. A strategy that is profitable *only* when filled
at the close is profitable because of that bias, not because of a real edge — a red flag,
not a result. Revisit resolution (intrabar fills) only with finer data than daily bars.

### D14. The five lies of NaiveFillModel (list them so they're honest)
(1) Infinite liquidity — the whole order fills regardless of size. (2) No slippage — you
always get exactly the open. (3) No spread — you never cross bid/ask. (4) Certain
execution — no partial fills, rejects, gaps, or halts. (5) Trivial flat costs — a fixed
bps drag independent of size/urgency/conditions, whereas real cost is nonlinear in size
and varies with liquidity and volatility. These are acceptable *because* they are
explicit and each is a seam we can replace later without touching strategies.

### D15. Build the pending/latency queue now, at latency_ns = 0
Decided: every order gets `arrival_ts = submit_ts + latency_ns` and waits in a pending
queue until the clock reaches it; week 1 sets `latency_ns = 0` so behaviour is unchanged
(fills next open). We build the plumbing now because threading a pending queue through
the engine loop, fill model, and portfolio *later* is invasive surgery across three
components; building it now makes adding real latency a one-line config change (seam 5).

### D16. CostModel signature narrowed; `ctx: MarketCtx` deferred (flagged seam narrowing)
Seam 4 declares `cost(order, fill_price, qty, ctx: MarketCtx)`, but `MarketCtx` does not
exist. Rather than invent its fields speculatively, we implement `cost(order, fill_price,
qty)` and defer `ctx` until a cost model actually needs market state (spread/impact).
This is an explicit, temporary narrowing of the seam — not silent drift — and widening it
later touches only our own cost models. `BpsCostModel` charges `bps x 1e-4 x price x |qty|`.

### D17. Limit orders get one shot at the next open
Decided: a limit order fills at the next open if the open crosses its limit price,
otherwise it is cancelled (not left lingering). Week-1 strategies use only market orders,
so this keeps the interface complete without building resting-order/expiry machinery.
Revisit when a strategy needs true resting limits with a book (order-book replay, later).

---

## Task 6: the engine loop

**Raw material — rewrite in your own words.**

### D18. Per-iteration ordering: clock → fill past → apply → strategy → submit → record
Decided ordering for event E at time t: advance clock; fill PAST orders against E (at
E's open); apply those fills and record them; update the latest price; call the strategy
on a post-fill Context; submit the new orders; record the portfolio snapshot. The crux is
the adjacency of "fill" (step 2) and "submit" (step 6): past orders resolve before the
strategy decides, and new orders are placed after the fill pass, so they can only fill on
a *later* event. This makes same-bar look-ahead structurally impossible. The plausible-
but-wrong alternative — decide, submit, then fill against the same event — fills an order
at the very bar it was decided from, which is same-bar look-ahead bias that inflates
returns. A milder wrong ordering (apply fills after the strategy call) isn't look-ahead
but lets the strategy act on stale positions and double-order.

### D19. The Recorder protocol lives in core, not runner (flagged deviation)
Decided: the `Recorder` protocol lives in `core/engine.py` and the engine emits to it;
`runner/recorder.py` (Task 7) provides implementations. The architecture's literal layout
puts the protocol in `runner`, but the engine is in `core`, and `core` importing `runner`
inverts the layering. Dependency inversion — the high-level engine owns the abstraction,
the low-level runner implements it — is the clean fix. Flagged rather than done silently.

### D20. Marking uses a latest-price map the engine maintains
The engine keeps a `prices` map, updating `prices[symbol] = bar.close` as bars arrive
(step 4), and passes it to `accounting.equity`/`unrealized_pnl` for the portfolio
snapshot. Because the map only ever holds already-seen prices, marks never use a future
price. Records emitted: `fill`, `order`, `portfolio` (signals deferred — no channel yet).

### On the Rust seam and strategy-compute latency
Only plain dataclasses of primitives cross the loop (`Event`, `Order`, `Fill`) and the
only callback into user code is `Strategy.on_event`; to port the loop to Rust, the loop +
fill model + book move to Rust while `on_event` stays the one Python boundary. A strategy's
own compute-time latency would be modelled by offsetting the submit time in step 6
(`submit(order, t + compute_latency)`); deferred for now, hook noted.

---

## Task 7: recorder, config, manifest

**Raw material — rewrite in your own words.**

### D21. ParquetRecorder buffers by kind, writes once on close
Decided: the recorder accumulates records in memory per kind and writes one parquet file
per kind at `close` (`fill`->fills.parquet, `order`->orders.parquet,
`portfolio`->portfolio.parquet). Parquet is columnar and write-once by nature, and
daily-bar runs are tiny, so buffering is simplest and fine. Revisit with a streaming
row-group writer if runs ever get large enough that holding all records in memory hurts
(tick data). `NullRecorder` (drop everything, for benchmarks) and `MultiRecorder` (fan out
to several) round out seam 6.

### D22. `verify` re-runs via an injected run function, comparing parquet content
Decided: `verify(run_dir, run_fn)` reads the manifest's config, re-runs it into a
throwaway directory via `run_fn(config, recorder)`, and asserts every parquet file matches
the stored run by *content* (`DataFrame.equals`), not raw bytes. The run function is
injected because reconstructing a run from a config needs the strategy resolver and data
loader, which are the CLI's job (Task 8); this keeps `manifest.py` decoupled and testable
now. Content comparison (not byte comparison) is robust to incidental parquet metadata
differences while still proving the records are identical.

### D23. Wall-clock and git live in the runner, never the engine
The manifest captures git commit, a sha256 content hash of the input data, python/library
versions, the seed, and wall-clock timings. All wall-clock use (timings) and subprocess
git calls live in `runner/manifest.py` — outside the engine, so the hard "no wall clock in
the engine" rule is preserved. `git_commit` returns None gracefully if git is unavailable.

---

## Task 8: example strategies, CSV loader, and CLI

**Raw material — rewrite in your own words.**

### D24. Strategies hold their own rolling state (no dataframes)
`MaCrossover` keeps two `deque` ring buffers of recent closes; `Reversal` keeps only the
previous close. Both compute their signal from their *own* accumulated memory and read
current position via `ctx.position(...)` — never a window handed in. This is seam 2 made
concrete: history by self-accumulation, look-ahead impossible. Both act only on `Bar`
events and emit market orders; `MaCrossover` is long/flat, `Reversal` is long/short and
lets the engine's flip accounting handle direction changes.

### D25. The CLI's run_from_config is the single reproducible core
`run_from_config(config, recorder)` resolves the strategy (from a name→class registry) and
the data sources (one `CsvBarSource` per symbol, merged by the queue), then runs the
engine. Both the `run` command and `manifest.verify` call it, so "what the CLI does" and
"what verify reproduces" are guaranteed identical — there is one code path, not two.

### D26. Bug caught: pandas parses dates to MICROSECONDS by default
`pd.to_datetime(...)` in pandas 3.0 returns `datetime64[us]`, so `.astype("int64")` yielded
microseconds — every timestamp 1000x too small, which silently filtered all bars out of the
date range. Fixed by forcing `.dt.as_unit("ns")` before converting to int. This is exactly
the resolution trap the architecture warns about, and it lives entirely at the loader
boundary where all human-time→int-ns conversion is confined.

---

## Task 9: metrics and tearsheet

**Raw material — rewrite in your own words.**

### D27. Metrics are computed offline from the record parquet, not the engine
`metrics/returns.py` reads `portfolio.parquet` (equity curve) and `fills.parquet` (trades)
and derives everything after the fact (seam 8). Benefits: new metrics are just new
functions over the same records; old runs can be re-measured without re-running; and the
engine stays lean and single-purpose. The engine never computes a Sharpe.

### D28. Annualising Sharpe from daily returns assumes 252 iid days, rf = 0
Sharpe = mean(daily return) / std(daily return) × √252. The √252 scales a per-day ratio to
per-year and assumes returns are independent and identically distributed across 252 trading
days a year, and that the risk-free rate is zero. If returns are autocorrelated (they often
are), √252 overstates the annualisation — a known limitation, fine for a week-1 tearsheet.
Volatility is annualised the same way (×√252); annualised return is geometric:
`(equity_end/equity_start)^(252/n) − 1`.

### D29. Hit rate and win/loss are per-period (daily), not per-trade
`hit_rate`, `avg_win`, `avg_loss` are computed over the daily return series (fraction of
up days, mean up move, mean down move), not over round-trip trades. Per-trade round-trip
attribution under average-cost accounting is a later feature; the per-period version is a
defensible, cheap proxy now and is clearly labelled as such.

### D30. Tearsheet uses the headless Agg backend and lazy import
`tearsheet.py` sets matplotlib's `Agg` backend before importing pyplot so it renders a PNG
with no display. The CLI imports the metrics/tearsheet modules *lazily* inside the `report`
command, so `tessera run` never pays matplotlib's (heavy) import cost. Four panels: equity
curve, underwater drawdown, rolling 60-period Sharpe, return distribution.

---

## Audit round 1 (post-Task-9) — see docs/AUDIT.md for full evidence

### D31. Reported annualized return is geometric; Sharpe uses arithmetic annualisation
`annualized_return` is geometric `(e_end/e_start)^(252/n) − 1`; `sharpe` is
`(mean/std)·√252`, which equals `arithmetic_ann_return/ann_vol` (`mean·252/ann_vol`). So
`annualized_return / annualized_vol ≠ sharpe`. Both conventions are standard; revisit if we
want one consistent annualisation basis across the tearsheet. The Sharpe itself is correct —
proven by a hand-computed regression test and by reproducing the −24.04 run
(`mean −0.001476, std 0.000974, ann_vol 1.55%` → `−24.0411`).

**Correction note:** a `−24.04` Sharpe is NOT explained by the geometric-vs-arithmetic gap
(with 22.1% vol that gap is only σ²/2 ≈ 2.4pp). It is huge because the synthetic
`data/AAPL.csv` has ~1.55% volatility. A separate, real problem was found: the Task-9 learn
doc's example report line contained **fabricated** figures (`ann −33.4%, vol 22.1%, hit 42%,
trades 118`; real values `−31.08%, 1.55%, 8.5%, 7`). Those were corrected. Lesson: doc output
lines must be copied from an actual run, never reconstructed.

### D32. Daily bars are stamped at midnight (latent future-leak with multiple sources)
`CsvBarSource` stamps a daily bar at 00:00 UTC of its date while it carries that day's
close. With one source this is invisible. Once an intraday source is merged, the queue
would emit the daily bar before that day's ticks, leaking the close. Fills inherit the
bar's midnight ts too. Deferred fix (week 2+): stamp bars at their close time, or split
each bar into open/close events. Flagged now so it is not discovered after building on it.
**Resolved in Task 11 (D41): bars are now stamped at the 16:00 ET session close.**

### D33. Invariant 2 narrowed from "byte-identical" to "identical record content"
`verify` compares parquet **content** (`DataFrame.equals`), not raw bytes, because correct
parquet files can differ in incidental metadata (writer version, compression framing) while
holding identical rows. Determinism was confirmed across two subprocesses with different
`PYTHONHASHSEED`. ARCHITECTURE invariant 2 wording was corrected to match. This is a
deliberate narrowing, not drift left silent.

### D34. verify() checks output reproducibility, not environment match
`verify` re-runs the config and compares output; it does **not** assert that the current git
commit or library versions equal those in the manifest. So a run re-verified under a
different pandas could still pass. The manifest *records* commit/versions for humans; making
`verify` enforce them is a later enhancement.

### D35. No margin / buying-power check (week-1 limitation)
`Book.apply_fill` applies any quantity; there is no cash/margin check, so a strategy can
order beyond its cash and `Book.cash` goes negative (equity stays flat as the position is
marked at cost). Add this to the "naive model lies" list. A rejection path with a `reject`
record is a later feature.

### D36. Zero-event / zero-fill / never-trading runs succeed silently
A run over an empty event stream (or a strategy that never trades, or an order on the final
bar) completes with no error and possibly no records. This is the same silent-success that
let the Task-8 microsecond bug through. Documented as known behaviour; a future guard could
fail loudly when a run produces zero events or zero fills.

---

## Task 10: real data (Part 1 — fetch and validate)

**Raw material — rewrite in your own words.**

### D37. Use Stooq's split- and dividend-adjusted daily bars as the backtest default
Decided: the price series we backtest on are Stooq's US daily bars, which are **both split
and dividend adjusted** (visible in the data: AAPL's adjusted close is ~$0.95 in Jan 2005,
not its ~$32 unadjusted print, because the 7:1 2014 and 4:1 2020 splits plus every dividend
are folded back into history). Alternatives were raw/unadjusted prices, or split-only
adjustment. Adjusted prices are the right default because a backtest measures the return an
investor would actually have earned holding the position: a 2:1 split is not a −50% day, and
a dividend is cash received, not value lost. Unadjusted series inject fake overnight gaps at
every split/ex-div date that a momentum or reversal strategy would trade on as if they were
real moves — pure artifacts. The tradeoff: adjusted history is *revised* (today's adjusted
2005 price changes after the next split/dividend), so a run is only reproducible against a
pinned copy of the CSV — which is exactly why the manifest hashes the input data (seam 7).
Revisit if we ever model dividend capture or corporate actions explicitly, which needs the
raw series plus an actions table.

### D38. Backtest window widened from 2015–2024 to full history (2005–2026)
Decided: rather than the originally-planned ten-year window (2015-01-01…2024-12-31), use each
symbol's full Stooq history, 2005-01-03…2026-08-11 (~21.6 years, 5,435 bars each). All six
tickers (AAPL, MSFT, JPM, XOM, KO, NVDA) trade back to 2005, so "from 2005 for all" is clean.
Why: the extra decade contains the **2008 financial crisis** — every symbol's largest single
day lands in Sep 2008–Jan 2009 — which is a far stronger stress test for a momentum-vs-reversal
comparison than a 2015–2024 sample that never sees a real crash. Consequence: the Part-1
row-count gate ("~2,500 rows for ten years") was reinterpreted as a **density** gate (~252
bars/year, actual 251.6) rather than an absolute count, since 21.6 years is ~5,435 rows.
Revisit if we later want clean calendar-year boundaries (the current series ends mid-2026) or
a fixed out-of-sample tail.

### D39. Corporate-action gate: no single-day return sits near a split ratio (adjustment intact)
Decided: the data validation includes a corporate-action gate that flags any single-day return
within ±2 percentage points of a common forward-split signature (−50% = 2:1, −75% = 4:1,
−80% = 5:1, −90% = 10:1) or any jump above +90% (reverse split). The reasoning: an *unadjusted*
series prints a large fixed-ratio gap on every split date — a 10:1 split looks like a −90% day —
which a momentum or reversal strategy would trade on as if it were a real move. Adjusted data
folds the split back into history, so those gaps vanish. This is a stronger check than the vol
and mean/std gates because it targets the *specific* artifact adjustment is supposed to remove.
Result (from actual output): **0 flagged days across all six symbols**, even though the 2005–2026
window contains real splits — AAPL 7:1 (2014) and 4:1 (2020), NVDA 4:1 (2021) and 10:1 (2024),
KO 2:1 (2012). Unadjusted, those dates would show −86%, −75%, −90% drops; none appear, and the
most extreme move anywhere is NVDA −30.70% (a genuine 2008 event), far from any split band. This
is the evidence backing D37's claim that the Stooq series is correctly split/dividend adjusted.
Revisit only if a symbol with a split we can't rule out ever trips the gate — then confirm the
date against a known corporate-action calendar before trusting the series.

---

## Task 10: real data (Part 2 — running it, and the sizing fix)

**Raw material — rewrite in your own words.**

### D40. Example strategies size by fixed-fractional notional, not a fixed share count
Decided: `MaCrossover` and `Reversal` size positions as a target dollar **notional** =
`target_frac × initial_cash` (default `target_frac = 0.10`), converted to shares at the
current bar's close (`qty = notional / event.close`). The rejected status quo was a fixed
**share count** (`qty = 100`). On split-adjusted prices a fixed share count makes exposure a
function of price level: a 100-share AAPL position was 0.095% of a $100k account in 2005 and
18.4% by 2024, so the whole Part-2 table measured position sizing, not strategy — proven by
three symptoms (portfolio vol 0.48–3.75% vs underlying 18–48%; ma_crossover trade counts flat
at 127–150 but turnover spread 9.1×; only 12% of AAPL PnL and −0.34% of it from 2008). After
the fix those resolve: portfolio vol tracks underlying risk (NVDA highest, KO lowest), the
ma_crossover turnover spread falls to 1.45×, and the pre-2015 PnL share rises from 12% to 64%.

Alternatives weighed (see the option write-up): **fixed fraction of equity** — better in
principle but requires `equity` on `Context`, which Task 3/D8 deliberately excluded (equity
needs mark prices from accounting), so it would widen seam 2; and **volatility-targeted
sizing** — best for cross-symbol risk comparability but needs a trailing-vol estimate and more
parameters. Fixed notional was chosen for week 1 because it fixes the actual defect with **zero
interface change** (the strategy already sees `event.close`), keeps `Context` untouched, and
makes turnover/cost-drag comparable across symbols — which the transaction-cost story needs.

Two implementation points worth remembering:
- **`initial_cash` is injected by the runner, not passed via params.** `cli._make_strategy`
  passes `config.initial_cash` into any strategy whose constructor accepts it, so `target_frac`
  scales with `--cash` and lands in `RunConfig.params`/the manifest while `initial_cash` stays a
  top-level config field. It is a static config value, not market data, so this is not
  look-ahead. Reproducibility holds (the CLI verify test still passes).
- **Reversal orders the delta to target, which is where the flip lives.** `target = ±notional/
  close`; `delta = target − ctx.position(sym)`. An up day after a long produces a single order of
  size `held_long + short_target` that crosses zero — the exact `Book.apply_fill` split from
  Task 4. `test_reversal_flips_long_to_short_with_delta_sizing` pins this quantity.

Measured consequence (this **corrects an earlier draft of this entry**): constant-notional
targeting rebalances daily even when the signal direction is unchanged (the target drifts with
price), so reversal's trade *count* roughly doubled (AAPL 2,516 → 5,016). The first draft claimed
this "amplifies cost sensitivity" — that was **wrong**, and conflated trade count with notional.
Decomposing AAPL reversal @0bps traded notional: **flips are 99.26%** of notional (mean $20,035 ≈
2× the $10k target), **same-direction rebalances only 0.72%** (mean $146 ≈ target × daily return),
opens 0.02%. Because `BpsCostModel` charges on **notional, not trade count**, daily rebalancing
adds ~0.7% to cost — negligible. The turnover rise (AAPL reversal 250x → 455x) is attributable to
the **sizing fix, not rebalancing**: pre-2015 traded notional rose **11.8×** ($2.15M → $25.23M,
from ~100 shares of a $1–24 stock to a constant $10k) while fills only doubled. Whether reversal
rebalances daily or only on a signal flip is therefore a turnover/style choice with negligible
cost impact, not a cost problem. What we still give up (deferred, not free): positions don't
compound with the account (fixed-fraction) and aren't risk-equalised across symbols (vol-targeting)
— NVDA at 48% vol still carries ~2.6× KO's risk per dollar.

---

## Task 11: fix the D32 midnight-bar leak (stamp bars at session close)

**Raw material — rewrite in your own words.**

### D41. Daily bars are stamped at the 16:00 ET session close, not UTC midnight
Decided: `CsvBarSource` now stamps each daily bar at its **16:00 America/New_York regular-session
close**, converted to UTC, instead of 00:00 UTC of the bar's date. This closes the latent
look-ahead flagged as D32: a daily bar carries that day's high/low/close, which are only known
when the session ends, so stamping it at midnight placed it ~21h before its data existed. With
one daily source that was invisible (fills are next-open regardless), but the moment an intraday
source is merged in, the midnight bar sorted *ahead* of that day's ticks — leaking the close.
Chosen option A (move the one timestamp) over option B (split each bar into a separate open event
and close event). Why A: it is **numerically behavior-preserving** for the current single-daily-
source world — every bar shifts by the same rule, so relative order, next-open fills, equity, and
all metrics are unchanged; only the `ts` column moves. **Verified against the actual Task-10 grid
window** (AAPL, 2005-01-03…2024-12-31, 0 bps): re-running `ma_crossover` and `reversal` after the
change reproduces the baseline `runs/task10p2_fixed/` rows to machine precision — total return,
Sharpe, max drawdown, and trade count all identical (deltas 0.0), including reversal's 5016 fills /
455× turnover, the case most sensitive to any fill-ordering change. (An earlier 2015–2024 spot-check
was *not* comparable to the grid and was redone on the correct window.) It also stays confined to
`data/` plus one test (one component per session), whereas B would have edited seam 1 (a new
open-event type on `events.py`), the fill model (`execution/naive.py`), and rippled into metrics
(portfolio rows would double, breaking the ~252/yr annualization). B fixes a leak that **cannot
fire until week-2 intraday data exists**, so building it now is the speculative-scope failure mode
the plan warns about. Two implementation points that mattered: (1) the conversion constructs the
wall-clock 16:00 in `America/New_York` and converts to UTC (21:00 in winter / EST, 20:00 in summer
/ EDT), so the **DST boundary is correct** — a fixed +21h offset would be wrong twice a year; a
test pins both a winter and a summer date. `zoneinfo` is stdlib, so no new dependency. (2) The
vectorized loader path adds the 16:00 wall-clock offset to the *naive* date first, then localizes,
so DST is handled by the localize step (16:00 is never in a spring-forward gap).
Known limitations left for when intraday data actually lands (both become B's job): **half-day
early closes** (13:00 ET, ~13 days/yr) are not modelled — no market calendar dependency in week 1
— so those bars are stamped 3h late, which is harmless for a single daily source; and a **fill is
stamped at the next bar's close instant, not its true open** (its price is still the open) — a
~6.5h-late stamp that never breaks clock monotonicity but would misplace fills relative to a real
intraday tape. Revisit both when merging real intraday data (option B: split bars into open/close
events, fill and stamp at the true open, and add an exchange calendar for half-days).
Boundary caveat (found while verifying): `--start`/`--end` are now interpreted at the session
close on **both** ends, so a config that stored a raw `end_ts` computed under the *old* midnight
rule reinterprets — e.g. the grid's `end_ts` = 2024-12-31 00:00 UTC now sits *before* that day's
bar (21:00 UTC), silently dropping it. New runs (dates → `to_epoch_ns` under the new rule) are
self-consistent; old runs must be re-parameterized by date, not by their stored `ts`, to reproduce.
The before/after grid comparison above was run by date for exactly this reason.
Grep audit (checked no code assumes midnight stamping): no ns/day integer div/modulo anywhere; the
only `ts`→datetime conversion is `metrics/returns.py` `equity_curve`, which uses the datetime purely
as an index *label* — every metric is positional (`pct_change`, count-based `rolling`, annualization
by `n` and a fixed 252), so none reads absolute time; no `resample`/date-boundary logic in `metrics/`;
and the Task-10-Part-1 `np.busday_count` validation was interactive and never committed, so there is
no code path to break. A bar moving 00:00→20:00/21:00 UTC also stays within the same UTC calendar
date, so even a hypothetical floor-to-day would be unaffected.

### D42. verify() versions the timestamp convention and refuses to reproduce across a change (A detects; B, deferred, cures)
Decided: the run manifest now records a `timestamp_convention` string (currently
`session_close_v1`), and `verify` raises `ConventionMismatch` — loud and naming both
conventions — when a run's stored convention differs from the current code's, instead of
silently re-running under the new rule and returning a bare `False`. This closes a real
reproducibility break introduced by D41: because `verify` re-runs a config's stored **raw ns**
boundaries, moving bars from midnight to the session close made the grid's `end_ts`
(2024-12-31 00:00 UTC) fall *before* that day's bar (21:00 UTC), so a re-run silently dropped
the final bar (empirically confirmed: `verify` returned `False`, replay 5032 rows vs stored
5033). The convention lives in *our own code* (`to_epoch_ns`), where neither the data hash nor
the library versions can see it — exactly the class of environment/semantic drift **D34**
flagged as outside verify's remit. The version string is the single source of truth in
`csv_bars.py`, imported (never re-typed), and a tripwire test pins it to the actual
date→ns mapping so changing `to_epoch_ns` without bumping the string breaks a test rather than
passing silently — reducing reliance on memory, the weakness of any manual version tag.
**This is a detection fix, not a cure.** It makes the break loud; it does **not** make
pre-Task-11 runs reproduce (their recorded output carries midnight timestamps regardless), and
it does not stop a *future* convention change from reinterpreting boundaries — it only reports
it. The cure is **option B (deferred): store calendar dates, not raw ns, in `RunConfig`**, so a
boundary means "that date's session" under any convention and the bar *set* stays stable. B is a
seam-7 schema change with a back-compat migration, so it is scheduled for the next time we touch
`RunConfig` or when intraday bar-splitting lands (whichever first) — recorded here so the
deferral is a deliberate decision, not an omission. Revisit A itself only if the convention
identifier ever needs to encode more than the date→ns rule (e.g. a calendar or half-day policy),
at which point it should become a small structured version block rather than one string.

---

## Task 12: the margin / leverage check (D35 fix)

**Raw material — rewrite in your own words.**

### D43. A gross-leverage cap (default 1x) enforced by the engine, rejected fills recorded as `reject`
Decided: the engine now refuses a fill that would push **gross exposure above `max_leverage x
equity`** (default `max_leverage = 1.0`, i.e. no leverage), dropping the whole order and emitting
a `reject` record instead of applying it. This closes D35 — the only real return-inflation vector
the audit found: a strategy could buy 10M of notional on a 100k account, taking cash to −9.9M with
no rejection and a 10x return amplification at no financing cost. Three sub-decisions:
(1) **Where** — a pure predicate `accounting.admits_fill(book, symbol, signed_qty, price, prices,
cost)`, evaluated by the *engine* between producing a fill and applying it (chosen over putting it
in `Book.apply_fill`, which would grow a reject channel and drag mark prices into the book, and
over the fill model, which would need a seam-4 protocol change to see cash/equity). `apply_fill`
stays a pure mutation; the fill model is untouched; the engine owns the `reject` record (seam 6's
first use of that kind — `ParquetRecorder` already writes any kind to `<kind>.parquet`).
(2) **What happens** — the order is *dropped whole* and recorded as `reject`, not partially filled
to an affordable size (partial fills silently change the requested quantity, break the reversal
zero-crossing flip, and are broker-realism scope creep; deferred).
(3) **The rule** — gross exposure `G = Σ|qty x mark|` must satisfy `G ≤ max_leverage x equity`,
which covers **shorts** (a cash-non-negative rule alone would not: a short *generates* cash, so
unbounded shorting is unbounded leverage). Marks are look-ahead-safe: the traded symbol at its
fill price, others at last observed close (the engine's `prices`, which at fill time still holds
prior marks). A **de-risking carve-out** always admits a fill that does not increase gross
exposure, so an account pushed over the cap by mark-to-market drift (only reachable that way — no
fill can create an over-cap state) can still reduce and never locks up; a locked account would be
a worse failure than the bug. The limit lives as a `Book` field defaulting to 1.0, **not** wired
to `RunConfig`, to keep this change inside `portfolio/`+`core/` and avoid tripping the Task-11
option-B trigger (a `RunConfig` edit); making it configurable is a small follow-up that should be
paired with option B when `RunConfig` is next opened. Grid impact: fixed-fractional 10%-of-100k
sizing never approaches 1x, so as expected **zero rejections and identical metrics** on the
re-run AAPL rows (ma_crossover + reversal, 0 bps). Revisit if a strategy legitimately needs >1x
(raise `max_leverage`), if per-symbol or maintenance-margin rules are needed, or if partial fills
to the affordable size become worth the complexity.

**Affirmative reject count (follow-up).** A run with zero rejections writes no `reject.parquet`,
which is *ambiguous*: it looks identical to a run where reject recording silently broke. So the
manifest now stores `record_counts` (records emitted per kind, e.g. `{"fill": 5016, "order": 5017,
"portfolio": 5033, "reject": 0}`) with the canonical kinds — including `reject` — always seeded,
so **"0 rejections" is stated in provenance, not inferred from a missing file.** Chosen over always
writing an empty `reject.parquet`, which would invent a zero-row-schema convention no other record
kind follows (fills/orders/portfolio are likewise only written when non-empty); the manifest is the
artifact whose job is to *describe* the run, so an affirmative count belongs there. Honest boundary:
the count affirms what the *recorder* received; it is the D43 attack test that proves the *engine*
emits a reject when it should. Together they cover "did it reject?" from both ends.

---

## Validation suite: the engine vs independently computable ground truth

**Raw material — rewrite in your own words.**

### D44. Validate the engine against ground truth we can compute without it, not against published returns
Decided: add `tests/test_validation.py`, which checks that the engine *reads the instrument
correctly* — that a backtest's equity is the arithmetic consequence of the bars — against truth
derived **independently of the engine**, never against published trader returns (which are not
reproducible from daily bars and would validate nothing). Two kinds of check. (1) **Buy-and-hold
vs plain pandas on the six real symbols**: a new `BuyAndHold` benchmark strategy (buy once on the
first bar, hold forever) is run through the engine, and its total return, annualized return,
annualized vol, and max drawdown must match the same statistics computed directly from the bars in
numpy/pandas. They match to **0.0 (machine precision)** for all six; the *only* permitted
discrepancy from a naive first-close hold is that the entry fills at the next bar's **open** rather
than the first **close**, and the test asserts that residual equals **exactly the first overnight
gap** `(close_0 - open_1)/close_0` (e.g. AAPL -0.7557%, MSFT/KO 0.0000% because their day-2 open
equals day-1 close). Anything larger is a finding, not a tolerance to widen. (2) **Analytic anchors
with hand-derivable answers**: a constant-daily-return series where fully-invested buy-and-hold
equity must equal `initial x (1+r)^k` at every bar (asserted to 1e-12), and a fixed round trip
(buy 10 @ 100, sell 10 @ 110) whose realized PnL is `10 x (110-100) = 100` and whose cash/equity
are integer-exact. One interaction worth recording: a fully-invested buy-and-hold sized at the prior
*close* can read fractionally above 1x gross at the next-*open* fill on a gap-up day (AAPL/XOM/NVDA
do), which the Task-12 leverage cap would reject; that is an entry-timing artifact, not leverage, so
the buy-and-hold validation runs with the cap relaxed (the cap has its own tests). `BuyAndHold` lives
in `strategy/examples/` as a reusable benchmark but is **not** wired into the CLI yet (would need a
sub-1x default or the cap caveat) — a small follow-up if the README wants a buy-and-hold row.
Revisit by adding more analytic anchors (a known-Sharpe series, a scripted drawdown) as the engine
grows; these are the tests that back the project's core claim, so they must never be weakened to pass.
