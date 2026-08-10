# Understanding Task 3: the strategy protocol and Context

A from-scratch explanation, no code required. By the end you should be able to
answer the interview questions yourself, in your own words, and know *why* each
answer is right.

Read it once top to bottom, then try the "Answer these yourself" section.

---

## Part 0 — The problem we are actually solving

We now have events (Task 1) flowing in time order through a queue (Task 2). It's
time to let a **strategy** — the actual trading idea — react to them.

But this is the single most dangerous place in the whole system. Remember the enemy
from Task 1: **look-ahead**, a strategy seeing the future. If we're careless *here*,
we hand the strategy a way to cheat, and every result above becomes fantasy.

So Task 3 isn't really "let the strategy see data." It's "**give the strategy exactly
what it could have known at this instant, and make it physically impossible to get
anything more.**" That's the entire job.

Three pieces make it work:
1. the **Order** — how a strategy expresses *what it wants to do*,
2. the **Strategy** — the shape every trading idea must fit,
3. the **Context** — the sealed, present-only view of the world the strategy is given.

---

## Part 1 — Order: a strategy states *intent*, never *outcome*

When a strategy decides to trade, what exactly does it produce? A naive design would
let it say "I bought 10 shares at $100." That's a disaster, and here's why:

The strategy doesn't get to decide the price it trades at — **reality** does. In a
real market you send an order and *then* find out what price you got, a moment later,
after costs and slippage. If the strategy computes its own fill price, it's just
making up favorable numbers.

So a strategy emits an **Order** — a little frozen note that says only *what it wants*:

- `symbol` — what to trade,
- `side` — buy (`+1`) or sell (`-1`),
- `qty` — how much,
- `type` — `"market"` (take whatever price is available) or `"limit"` (only at a price),
- `limit_price` — the price ceiling/floor for a limit order (else empty),
- `tag` — a free-form label, handy for "why did I make this trade?" analysis later.

That's it. No price it got, no profit, no position update. The **engine** takes this
intent and, later, decides what actually happened. This separation is why we can
someday swap in a realistic fill model without touching a single strategy: strategies
only ever said what they *wanted*, never what they *got*.

> Recite: *a strategy emits intent (an Order), not outcome. It never computes its own
> fill price or profit — reality (the engine) decides that.*

---

## Part 2 — Strategy: one event in, a list of orders out

Every trading idea, no matter how simple or fancy, fits one shape:

> given **one event** and a **context**, return a list of orders (possibly empty).

That's the whole interface: `on_event(event, ctx) -> list[Order]`. One at a time. The
strategy never gets a spreadsheet, never gets "the next week," never gets to loop over
history. It's spoon-fed the present, one bite at a time, and it answers "do I want to
trade right now?"

Because the interface is this narrow, we can hold many different strategies to the
exact same contract, and the engine doesn't care which one it's running.

---

## Part 3 — Context: the sealed present (the heart of Task 3)

Here's the crucial design move. How do we stop a strategy from peeking at the future?

The tempting answer is "add a rule that checks timestamps and blocks future access."
But rules can have holes, and a determined (or careless) strategy finds them. The
**better** answer is: **don't give it anything to peek at.**

The `Context` we hand the strategy contains *only the present*:
- `ts` — what time it is right now,
- `cash` — how much money it has right now,
- `positions` — what it's holding right now (and a helper `position(symbol)`).

And it contains, by deliberate design, **no reference to**:
- the data source or the queue (that would let it pull the next event — the future!),
- any "give me the last 50 bars" history function,
- future prices, the recorder, or the machinery that decides fills.

Look-ahead is impossible not because we forbid it, but because **there is no door to
the future in the room.** A strategy literally has no object it could ask. This is the
big idea: *prevent the mistake by construction, not by vigilance.*

> Recite: *the Context exposes only present state — time, cash, positions — and no
> handle to the data feed or future. You can't look ahead because there's nothing
> ahead to look at.*

### "But my strategy needs a 50-day average!"

Fine — it keeps its **own** running memory. As each day's event arrives, the strategy
adds that price to its own little rolling buffer (a fixed-length queue it owns). After
50 days it has 50 days of history *that it collected itself, one legitimate event at a
time.* It never needed to be handed a window, and a handed window is exactly the thing
that would have contained the future. The discipline feels slightly annoying; that
annoyance **is** the safety.

---

## Part 4 — Two design choices we made (and why)

### Choice 1: a fresh, frozen Context every event (vs. reusing one)

We build a **brand-new** Context object for each event, and it's **frozen** (can't be
changed after creation) — including a read-only snapshot of positions.

- *Why frozen + fresh:* it's a true photograph of one instant. A strategy can't
  secretly rewrite its cash to fake buying power, can't scribble a position in
  directly (it must emit an Order and let the engine do it), and can't stash the
  object and watch it silently change later. Maximum safety, dead simple to reason
  about.
- *The cost:* we allocate a small object each event. The alternative — reuse one
  Context and just update its fields — avoids that allocation and is faster, but it's
  a *live* object that can surprise a strategy that holds onto it, and it needs extra
  machinery to stay read-only.
- *The call:* for daily bars (~2,500 events a year) the allocation is nothing, so we
  took the safe, clear version. If we ever go to tick data and a profiler says Context
  allocation hurts, we switch to the reused version — and because only the *engine*
  builds Context, no strategy code changes. (Same "clarity now, optimize behind the
  boundary later" move as Task 1.)

### Choice 2: keep the Context surface minimal

We expose only time, cash, positions — not derived things like total equity or average
entry cost. Equity needs current market prices to compute, and that's the accounting
layer's job (Task 4), not something to bolt onto the strategy's view. Average cost is
the strategy's own bookkeeping. Every extra field is a wider seam and a bigger surface
to get wrong, so we keep it tight and widen it only when a real strategy demands it.

> Recite: *fresh + frozen = a tamper-proof snapshot of one instant; minimal surface =
> a tight seam. Both trade a little convenience for a lot of safety.*

---

## Part 5 — What actually got built in Task 3

One file, plus the first of the three load-bearing tests.

- **`tessera/strategy/base.py`** — `Order` (frozen intent), the `Strategy` protocol
  (`on_event`), and `Context` (the fresh, frozen, present-only snapshot with `ts`,
  `cash`, read-only `positions`, and a `position(symbol)` helper).
- **`tests/test_no_lookahead.py`** — proves the seam holds: three *cheating*
  strategies each fail loudly, and a *legitimate* strategy that keeps its own rolling
  average works fine.

---

## Worked example with synthetic data

Let's play four days of rising prices through both a cheater and an honest strategy.
The harness advances a `Clock` and builds a fresh `Context` for each bar — this is
what the engine will do for real in Task 6.

Synthetic bars (one symbol, AAPL), close prices:

```
Day 1: 10.0    Day 2: 11.0    Day 3: 12.0    Day 4: 13.0
```

### (a) The cheater is stopped — three ways

```
CheatByPeekingFuture:  reads ctx.future_bars
   -> there IS no such attribute on Context  ->  AttributeError (crash)

CheatByForgingCash:    does ctx.cash = 1_000_000_000
   -> Context is frozen  ->  FrozenInstanceError (crash)

CheatByMutatingPositions:  does ctx.positions["AAPL"] = 999
   -> positions is a read-only view  ->  TypeError (crash)
```

Every attempt to reach the future or forge state dies immediately. Not "returns wrong
numbers" — *crashes*, at the exact line of the cheat. That's "fails loudly."

### (b) The honest strategy, step by step

`RollingMean(window=3)` keeps its **own** 3-slot memory and buys when today's price is
above its own trailing average (and it's currently flat):

```
Day 1  close 10:  memory=[10]           <3 prices yet -> no trade
Day 2  close 11:  memory=[10,11]        <3 prices yet -> no trade
Day 3  close 12:  memory=[10,11,12]     avg=11.0; 12 > 11 and flat -> BUY 1 AAPL
Day 4  close 13:  memory=[11,12,13]     avg=12.0; 13 > 12 but already long -> no trade
```

Notice: the strategy computed a 3-day average **without ever being handed three bars**.
It accumulated them itself, one legitimate present-moment event at a time. At every
step `ctx.ts` equals that day's timestamp — never a future one. That's the whole design
working: history by self-accumulation, never by look-ahead.

### Which file and function did each step

| Step above | File | Function / type |
|---|---|---|
| the synthetic bars | `tessera/core/events.py` | `Bar` |
| advancing time per bar | `tessera/core/clock.py` | `Clock.advance()` |
| the present-only snapshot | `tessera/strategy/base.py` | `Context`, `Context.position()` |
| the buy note the strategy emits | `tessera/strategy/base.py` | `Order` |
| the shape every strategy fits | `tessera/strategy/base.py` | `Strategy` protocol |
| cheaters crash / honest strat works | `tests/test_no_lookahead.py` | `CheatBy*` classes, `RollingMean` |

---

## Answer these yourself

Cover the text and try these.

1. **Walk me through how a strategy computes a 50-day moving average without ever
   seeing a dataframe.** (Parts 2–3. It keeps its own fixed-length buffer and appends
   each day's price as that day's event arrives; after 50 events it has a 50-day
   window it built itself.)

2. **What's on Context, and why is each thing safe to expose?** (Parts 3–4. `ts`,
   `cash`, `positions` — all *present* state. Nothing about the future or the data
   feed is there, so none of it can leak look-ahead.)

3. **Someone hands you a strategy with a Sharpe of 4. What's the first thing you check,
   and how does this design let you rule out the usual culprit fast?** (Part 3.
   Look-ahead is the usual cause of a too-good result; here it's structurally
   impossible because the strategy is given no channel to the future — so you can rule
   that class of bug out by construction and look elsewhere, e.g. costs or a bug in
   the fill assumptions.)

4. **Why does a strategy emit an Order instead of just recording the trade it made?**
   (Part 1. It states intent; the engine decides the actual fill price and outcome, so
   swapping in a realistic fill model later changes no strategy code.)

If those come out cleanly in your own words, you've got Task 3 cold.

---

## Mini-glossary

- **Order** — a frozen note of *intent*: what to trade, which side, how much, what type.
- **Intent vs outcome** — the strategy says what it *wants*; the engine decides what *happened*.
- **Strategy** — anything with `on_event(event, ctx) -> list[Order]`.
- **Context** — the sealed, present-only view handed to the strategy each event.
- **Frozen** — cannot be changed after creation (tamper-proof).
- **Snapshot** — a copy of state at one instant that won't change afterwards.
- **Read-only view** — lets you look at positions but not modify them.
- **Rolling buffer** — a fixed-length memory the strategy keeps itself to hold recent history.
- **Look-ahead** — seeing the future; here made impossible by giving no channel to it.
