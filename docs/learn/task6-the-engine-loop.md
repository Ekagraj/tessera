# Understanding Task 6: the engine loop

A from-scratch explanation, no code required. This is the centerpiece — the part an
interviewer will dig into hardest. By the end you should be able to recite the
iteration order, justify it, name the bias in a wrong order, and explain the Rust
constraint.

Read it once top to bottom, then try the "Answer these yourself" section.

---

## Part 0 — The problem we are actually solving

We've built the parts: events, a clock, a queue, a strategy surface, a portfolio, a
fill model. The **engine** is the conductor that makes them play together. It pulls
events off the queue one at a time and, for each one, decides *in what order* to do
things: advance time, fill orders, update the books, ask the strategy, record results.

Sounds like plumbing. It isn't. **The order of those steps is the single most
important design decision in the whole system**, because getting it even slightly
wrong quietly re-opens the door to look-ahead — the exact bias every other part was
built to prevent. This is why WEEK1 calls it "the centerpiece... what interviewers
will ask about."

So Task 6 is really one question: *within a single event, what happens first?*

---

## Part 1 — The one rule everything follows

Here is the principle that dictates the entire order:

> An order decided from bar N must **not** fill at bar N. Its first possible fill is
> bar N+1's open.

We established *why* in Task 5 (you only know bar N's close once the bar is over, so
filling there is trading on a price you couldn't have acted on). The engine's job is
to make that rule *mechanically* true through the order in which it does things.

The trick: **fills for old decisions happen at the start of an event; new decisions
are placed at the end of an event.** Since a new order is placed *after* the fill step
has already run for this event, it simply *can't* fill until the next event comes
around. The ordering enforces the rule for free.

---

## Part 2 — The correct order, step by step

For each event **E** at time **t**, the engine does exactly this:

```
1. advance the clock to t
2. fill PAST orders against E        -> fills happen at E's open
3. apply those fills to the book     (cash, positions, realized PnL) + record them
4. update the latest price from E    (for marking)
5. ask the strategy about E          -> it returns new orders
6. submit the new orders             (they can only fill on a FUTURE event) + record
7. record the portfolio snapshot     (equity marked at E)
```

Why each step sits where it does:

- **1 first** — everything below reads "what time is it"; the clock also refuses to
  go backward, catching any bad ordering of events.
- **2 before 5** — orders placed on *earlier* bars execute at *this* bar's open. That
  execution is based on past decisions; it must not wait for, or be influenced by,
  this bar's decision.
- **3 before 5** — the strategy should see its *true* current position. If its order
  from yesterday just filled at today's open, today's decision must know that. (The
  open comes before the close within the bar, so the fill genuinely precedes the
  decision.)
- **5 before 6** — obviously: decide, *then* route the orders.
- **6 after 2** — the decisive one. Because we already ran the fill step (2) earlier
  in this same event, the orders we submit now have missed their chance to fill on E.
  They wait for the next event. **Fill-then-submit is what makes same-bar look-ahead
  impossible.**
- **7 last** — record the settled end-of-event state.

> Recite: *clock, fill-past, apply, mark, strategy, submit, record. Past orders fill
> before the strategy decides; new orders are submitted after the fill step, so they
> can only fill on a later bar.*

---

## Part 3 — The tempting wrong order (and its exact bias)

Here's an ordering that sounds *more* natural, not less: "advance the clock, **ask the
strategy**, submit its orders, **then** fill everything against this event." Decide,
then execute. Isn't that how trading works?

No — and the reason is precise. If you fill *after* submitting on the same event, then
an order the strategy decided from bar E fills at **bar E's own open**. The fill price
is contemporaneous with — and was chosen using — the very bar that produced the
decision. That is **same-bar look-ahead bias**. The backtest systematically gets fills
it could never have gotten in reality, and returns come out inflated. A strategy can
look brilliant purely because of this one swapped step.

(There's also a *milder* wrong order: applying fills **after** asking the strategy.
That's not look-ahead, but now the strategy acts on stale cash and positions — it
doesn't know its last order filled — so it can double-order or mis-size. A correctness
bug, not a bias, but still wrong.)

> Recite: *decide-then-fill-on-the-same-event fills orders at the bar they were decided
> from — same-bar look-ahead, which inflates returns. That's why fills come before the
> strategy and submits come after.*

---

## Part 4 — Marking, and recording a stream (not a result)

Two smaller but important points.

**Marking.** To record equity at each step, the engine keeps a little map of the
**latest price seen** for each symbol, updating it from each bar's close (step 4). It
hands that map to the accounting functions from Task 4. Because the map only ever holds
prices we've *already* seen, a mark can never sneak a future price in — no-look-ahead
holds even in the bookkeeping.

**The engine emits a stream, it doesn't return a result.** The loop never builds a big
table of results and hands it back. Instead it *pushes* records — one per fill, one per
order, one portfolio snapshot per event — to a **recorder** as they happen. Why? Because
that way, swapping "write to disk" for "stream to a live dashboard" or "throw away for a
speed benchmark" is just a different recorder, not an engine change. The engine returns
nothing; the record stream is the output.

(Small architecture note: the *recorder interface* lives with the engine in `core`, so
the engine never has to reach "upward" into other packages. The concrete recorders that
write parquet files come in Task 7.)

---

## Part 5 — The Rust constraint

The architecture wants the option to rewrite this hot loop in a fast language (Rust)
later. That only stays possible if the loop's edges are kept narrow:

- **Only plain records of primitives cross the boundary** — events in, orders and fills
  out. No rich, dynamic Python objects.
- **The only call back into user code from inside the loop is `strategy.on_event`.**

Keep to that, and porting the loop is a drop-in swap: the loop, fill model, and book
move to Rust, while the strategy call remains the one bridge back to Python. Let rich
objects or arbitrary callbacks leak into the loop, and that door slams shut.

*(Related interview question: where would you model the strategy's own "thinking time"?
Answer: at the submit step — place the order at `t + compute_time` instead of `t`, so a
slow strategy's orders arrive later. We left a note for it but didn't build it.)*

> Recite: *primitives across the boundary, and on_event the only callback in. Then the
> loop + fill model + book can become Rust with on_event as the one Python bridge.*

---

## Part 6 — What actually got built in Task 6

One file, plus two tests (two of the three load-bearing tests are now live).

- **`tessera/core/engine.py`** — `run(events, strategy, fill_model, book, recorder)`,
  the loop implementing the seven-step order, plus the `Recorder` protocol it emits to.
- **`tests/test_engine.py`** — an end-to-end run records fills/orders/portfolio, and an
  order decided on bar 0 fills at bar 1's open (proving no same-bar fill).
- **`tests/test_determinism.py`** — two identical runs produce an identical record
  stream (Task 7 will make it byte-identical files).

---

## Worked example with synthetic data

A strategy `BuyOnFirstBar` sends one market buy on the first bar, then nothing. Three
daily AAPL bars — note **close = open + 0.5**, so if a fill ever used the close we'd
catch it:

```
Bar day0:  open 10.0, close 10.5
Bar day1:  open 11.0, close 11.5
Bar day2:  open 12.0, close 12.5
```

Tracing the loop (cash starts at 100,000):

```
EVENT day0:
  clock -> day0
  fill past orders: none pending
  strategy decides: BUY 100 AAPL market      (submitted now, arrival = day0)
  submit -> order queued (cannot fill on day0, the fill step already ran)
  record portfolio: cash 100,000, equity 100,000

EVENT day1:
  clock -> day1
  fill past orders: the day0 buy fills at day1 OPEN = 11.0   <-- not day0's close 10.5!
  apply: cash -= 100 * 11.0 = 1,100  -> cash 98,900 ; position 100 @ 11.0
  strategy decides: (already done) -> nothing
  mark @ day1 close 11.5:
    equity = 98,900 + 100 * 11.5 = 100,050
  record portfolio

EVENT day2:
  clock -> day2
  fill past orders: none
  mark @ day2 close 12.5:
    equity = 98,900 + 100 * 12.5 = 100,150
    unrealized = 100 * (12.5 - 11.0) = 150
  record portfolio
```

The fill landed at **11.0 (day1 open)**, never **10.5 (day0 close)** — the seven-step
order made that automatic. And the equity series (100,000 → 100,050 → 100,150) is what
the metrics in Task 9 will turn into a return curve. (These three equity values are
asserted by `tests/test_engine.py::test_final_equity_reflects_fill_and_mark`.)

### Which file and function did each step

| Step above | File | Function / type |
|---|---|---|
| the whole per-event loop | `tessera/core/engine.py` | `run(...)` |
| advancing time | `tessera/core/clock.py` | `Clock.advance` |
| filling past orders at the open | `tessera/execution/naive.py` | `NaiveFillModel.on_event` |
| applying fills to cash/positions | `tessera/portfolio/book.py` | `Book.apply_fill` |
| the decision | `tessera/strategy/base.py` | `Strategy.on_event`, `Context` |
| marking equity | `tessera/portfolio/accounting.py` | `equity`, `unrealized_pnl` |
| the record sink | `tessera/core/engine.py` | `Recorder` protocol |
| next-open (not same-bar) proof | `tests/test_engine.py` | `test_order_on_bar0_fills_at_bar1_open` |
| identical-runs proof | `tests/test_determinism.py` | `test_two_identical_runs_produce_identical_records` |

---

## Answer these yourself

Cover the text and try these.

1. **Recite the per-iteration order and justify each position.** (Part 2. clock, fill-
   past, apply, mark, strategy, submit, record — with fill-before-strategy and
   submit-after-fill as the load-bearing pair.)

2. **Give an ordering that looks reasonable but is subtly wrong, and name the bias.**
   (Part 3. Decide → submit → fill-on-the-same-event = same-bar look-ahead bias.)

3. **What in this loop would have to change to run it in Rust?** (Part 5. Nothing about
   the *order*; the constraint is keeping only primitive records crossing the boundary
   and `on_event` as the only Python callback, so the loop/fill model/book can be ported
   with `on_event` as the bridge.)

4. **Where would you add a model of the strategy's own compute-time latency?** (Part 5.
   At the submit step: place the order at `t + compute_time` rather than `t`.)

If those come out cleanly in your own words, you've got Task 6 cold.

---

## Mini-glossary

- **Engine loop** — the routine that processes events one at a time in a fixed order.
- **Iteration** — one pass of the loop, handling a single event.
- **Fill-then-submit** — filling past orders before submitting new ones; the core anti-look-ahead move.
- **Same-bar look-ahead** — the bias from filling an order at the very bar it was decided from.
- **Marking** — valuing positions at the latest observed price to compute equity.
- **Recorder** — the sink the engine pushes records to (fills, orders, portfolio).
- **Record stream** — the engine's output; it returns nothing and emits records instead.
- **Rust seam** — keeping the loop's boundary narrow enough to reimplement in Rust later.
