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
