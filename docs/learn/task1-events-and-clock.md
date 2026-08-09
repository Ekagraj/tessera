# Understanding Task 1: events and the clock

A from-scratch explanation. No code required to read it. By the end you should be
able to answer the three interview questions yourself, in your own words, and
*know why* each answer is right — not just recite it.

Read it top to bottom once. Then read the "Answer these yourself" section at the
end and try to answer before looking back up.

---

## Part 0 — The problem we are actually solving

A **backtest** asks one question: *if I had run this trading strategy over the last
ten years, what would have happened?* You replay history, let the strategy make
decisions as if it were living through each day, and measure the result.

The whole thing is only trustworthy if you get **one** thing right: the strategy
must only ever know what it *could* have known at that moment. Not one second more.
The instant a strategy can peek even slightly into the future, its results become
fantasy — and the fantasy always looks *good*, because "knowing the future" is the
most profitable edge there is.

So the enemy of a backtest is **look-ahead**: the strategy seeing information from
the future. Almost every serious bug in backtesting is some sneaky form of it.

Everything in Task 1 exists to build the two most basic tools you need before you
can fight look-ahead:

1. A way to represent *"a thing that happened at a specific instant"* — an **event**.
2. A way to track *"what instant are we currently living through"* — a **clock**.

Get these two right and the rest of the system has a solid floor to stand on. Get
them subtly wrong and every result above them is quietly poisoned.

---

## Part 1 — Why data is a stream of *events*, not a spreadsheet

The obvious way to store ten years of daily prices is a table (a spreadsheet /
dataframe): one row per day, columns for open/high/low/close/volume.

The problem: if you hand a strategy the whole table, **the future is sitting right
there in the same object.** Nothing physically stops the strategy from peeking at
row 500 while it's "supposed" to be on row 100. You're relying on the programmer to
remember not to cheat. People forget. That's how a strategy accidentally gets a
Sharpe of 4 that evaporates in live trading.

So instead we treat data as a **stream of events**. An event is just a small,
frozen record of one thing that happened at one instant:

- a **Bar** — the OHLCV summary of one day (or minute) for one symbol,
- a **Trade** — a single executed trade at a price,
- a **Quote** — the current best bid and ask.

The engine pulls events off the stream **one at a time, in time order**, and hands
the strategy exactly one. The strategy never receives the container. It can't look
ahead because it was never given anything to look ahead *into*. This is the core
idea of the whole platform: **make cheating structurally impossible instead of
merely discouraged.**

(If the strategy wants history — say a 50-day average — it keeps its *own* little
running tally as events arrive. It remembers the past; it is never shown the
future. More on that in Task 3.)

**Why start with events on day one, even though we only have daily bars?** Because
later we'll add tick data and order-book data. If today's design is "loop over a
table," adding those means rewriting everything. If today's design is "stream of
events," adding those means "add a new event type." The shape we pick now decides
whether future features are additions or rewrites.

---

## Part 2 — Time: why integer nanoseconds, and not the two obvious alternatives

Every event needs a timestamp: *when did this happen?* The question is how to store
that number. Three candidates: a `datetime` object, a floating-point number, or a
plain integer counting nanoseconds since a fixed origin (midnight, Jan 1 1970 UTC —
"the epoch"). We chose the integer. Here is why the other two fail, concretely.

### Why not a floating-point number?

A computer's standard "double" float (float64) is clever but has a hard limit: it
can represent **consecutive whole numbers exactly only up to about 9,000,000,000,000,000**
(that's 2⁵³, roughly 9×10¹⁵). Past that, it starts skipping — it literally cannot
hold every integer, so it rounds to the nearest one it *can* hold.

Now, how big is a nanosecond timestamp today? About **1,750,000,000,000,000,000**
(1.75×10¹⁸). That is more than a hundred times past the point where float64 stops
being exact.

The concrete failure: two events one nanosecond apart — say `t` and `t + 1` — can
round to the **exact same float**. Two genuinely different instants become
indistinguishable, or two events silently swap places when you sort them. You've
lost ordering, and ordering is the one thing a backtest cannot afford to get wrong.
Floats are great for prices; they are disqualifying for nanosecond time.

### Why not a `datetime` object?

Two independent problems, either one fatal:

1. **It literally can't hold a nanosecond.** Python's `datetime` has only
   **microsecond** resolution — a microsecond is a thousand nanoseconds. It cannot
   represent nanosecond-level time *at all*. The moment you move from daily bars to
   tick data (where events land nanoseconds apart), `datetime` is out before you
   even discuss anything else.
2. **It drags baggage into the hot loop.** A `datetime` carries timezone and
   daylight-saving rules. Comparing two of them is a heavyweight operation, and
   you'll process *millions* of events per run, so that cost adds up. Worse, saving
   a `datetime` to disk is ambiguous (which timezone? what offset? what
   resolution?), and we need our saved records to be **byte-for-byte identical**
   across runs. Ambiguous serialization quietly breaks that guarantee.

### Why an integer wins

An integer number of nanoseconds is **exact** (no rounding, ever), **fast** (a
single-instruction comparison), and **unambiguous to save** (the same run always
writes the same bytes). It carries no timezone traps because it's just a count from
a fixed origin. One number, one meaning, forever.

**The one rule that keeps this clean:** humans write times like "2015-01-01". That
messy, human, timezone-y form is allowed to exist in exactly one place — the
**loader** that reads raw data files. The loader converts "2015-01-01" into an
integer nanosecond count *once*, at the door. From that point inward — the queue,
the clock, the strategy, the engine — nothing ever sees anything but integers. We
convert at the boundary and never again.

> **Two-reason summary you can recite:** (1) *Representability* — a float can't
> exactly hold numbers this large (it goes fuzzy above ~9×10¹⁵, and today's
> timestamps are ~1.75×10¹⁸), and a `datetime` can't hold nanoseconds at all (it
> stops at microseconds). (2) *Determinism and speed* — an integer compares fast
> and saves to identical bytes every time, with no timezone/daylight-saving traps,
> which is exactly what reproducible records need.

---

## Part 3 — The clock: time only moves forward

The **clock** is the engine's single source of "what time is it right now in the
simulation." One rule governs it:

**Time may stay the same or move forward. It may never move backward.**

Why allow it to *stay the same*? Because lots of things can happen at the very same
instant — three exchanges can each print a trade at the identical nanosecond. So
"the clock didn't advance" is normal and allowed. (This matters for Part 4.)

Why forbid moving *backward*? Because moving backward **is** look-ahead, dressed up.
Walk through it:

- Suppose the strategy has already lived through Tuesday and updated its running
  averages accordingly. Now the clock jumps back to Monday and hands it Monday's
  bar. The strategy's memory already contains Tuesday. Relative to Monday, Tuesday
  is *the future*. You have just fed it information it could not possibly have had.
  That's look-ahead bias, and it inflates returns.
- An even cleaner disaster: later we'll add a rule that an order placed at time `t`
  can only be filled at some later time `t + delay` (real orders take time to
  reach the exchange). That rule assumes time never reverses. If the clock can go
  backward, you could fill an order at a timestamp *earlier than you placed it* — a
  trade that happens before it exists. Physically impossible, and it fakes profit.

So the clock does something strict: if you ever ask it to go to a timestamp earlier
than where it already is, it **stops the whole program with an error** rather than
quietly reordering. A loud crash you notice beats a silent bias you ship.

Two smaller strictness choices in the same spirit:

- **Reading the time before the first event is also an error.** We don't let the
  clock secretly default to "zero" or "now," because a default is exactly the kind
  of silent wrong-answer we're trying to eliminate. No event seen yet → no time to
  report → say so loudly.
- **The timestamp must be an actual integer.** If someone passes a float (or a
  `True`/`False`, which sneaks through because in Python a boolean secretly counts
  as an integer), the clock rejects it. We *validate* the invariant here rather
  than merely trusting that the type checker caught it upstream.

---

## Part 4 — Two events at the exact same instant: who goes first?

This is the subtle one, and interviewers love it.

Say two trades happen at the **identical** nanosecond, from two different data
files. The engine has to hand them to the strategy in *some* order — it can only
pass one at a time. Which first?

Here's the key insight most people miss: **it genuinely does not matter which one
you pick.** They're simultaneous; "A first" and "B first" are equally defensible.
The thing that matters is that you make the **same** choice **every single time you
run.** Arbitrary-but-consistent is fine. Inconsistent is a catastrophe.

### How we pick (consistently)

We rank each event by a three-part label, compared left to right:

1. **Timestamp** — earlier time always goes first. (This is the normal case.)
2. **Source priority** — if timestamps tie, the data source listed *first* in the
   run's configuration wins. Each source gets a fixed number (its position in the
   list), decided by config, not by the data.
3. **Sequence number** — if it's the *same* source producing two events at the same
   instant, the one read first (lower sequence number) goes first.

Because every event gets a unique three-part label, there is always exactly one
correct order, and it depends only on configuration and read order — never on
anything random or machine-dependent.

### Why "stable across runs" is the whole point

Imagine we *didn't* pin this down — imagine the order at a tie depended on things
like how the computer happened to load files that day, or internal details of a
sorting structure. Then:

- **Your saved results stop matching themselves.** Run the identical backtest twice
  and the two simultaneous trades process in different orders → the strategy takes
  slightly different actions → the recorded fills differ → the files are no longer
  byte-identical. The promise "same inputs give same outputs" is now false.
- **Your profit number quietly drifts.** If one of those two trades nudges the
  strategy (fills its position, trips a signal) before the other, your final P&L
  and Sharpe come out slightly different each run. Nothing *looks* broken — it just
  won't reproduce. You'll burn days hunting a "flaky strategy" that is really a
  flaky sort.
- **Reproducing an old run fails randomly.** We save enough metadata to re-run any
  old backtest and expect an exact match. An unstable tie-break makes that check
  fail at random, which destroys trust in the entire system.

That's why the fix is a *total order* — a rule that gives every pair of events a
definite, repeatable winner. Not because the winner is meaningful, but because
"definite and repeatable" is what makes the whole platform trustworthy.

> **Summary you can recite:** at a tie, the source listed first wins, then read
> order — decided by config, not by data. It must be stable across runs because the
> choice is arbitrary but has to be *identical every time*; otherwise saved records
> stop matching, P&L drifts between runs, and old runs won't reproduce — all
> silently.

---

## Part 5 — Two small design words you'll be asked about

The events are built as **frozen, slotted** records. Plain English:

- **Frozen** = once created, an event can never be changed. A Tuesday bar is a
  historical fact; nothing should be able to quietly edit it mid-run. If code tries
  to modify one, it crashes instead. This kills a whole category of "something
  mutated the past" bugs.
- **Slotted** = each event is stored in a lean, fixed shape instead of a flexible
  general-purpose one. With millions of events per run, the lean form uses less
  memory and is faster to create. The trade-off (you can't bolt random extra fields
  onto an event) is exactly the discipline we want anyway.

There's a third, forward-looking reason for keeping events this plain: someday the
speed-critical loop might be rewritten in a faster language (Rust). That only stays
easy if the data crossing in and out is *simple* — just numbers and short text, no
rich fancy objects. Frozen-slotted-primitives keeps that door open. We're not
walking through it now; we're just not nailing it shut.

---

## Part 6 — What actually got built in Task 1

Two files, plus a test.

- **`tessera/core/events.py`** — defines the event records (`Event` and its
  `Bar` / `Trade` / `Quote` variants), all frozen and slotted, all timestamped in
  integer nanoseconds. It also defines the three-part ranking label from Part 4
  (`ordering_key`) that gives simultaneous events a stable order.
- **`tessera/core/clock.py`** — defines the `Clock` from Part 3: it moves forward
  or stays put, crashes on any attempt to go backward, refuses to report a time
  before the first event, and rejects non-integer timestamps.
- **`tests/test_events_clock.py`** — proves the two promises: the clock rejects
  backward time, and simultaneous events come out in the same order no matter how
  you shuffle the input. (Shuffling the input and getting the identical output is
  precisely the "stable across runs" property from Part 4, demonstrated.)

Notably *absent*: order-book events. They're scheduled for much later, and we don't
build them speculatively — adding things "just in case" is how projects stall.

---

## Answer these yourself

Cover the text above and try these. If you can't, re-read the linked part.

1. **Why integer nanoseconds rather than a `datetime` or a `float`? Give two
   distinct reasons.** (Part 2. One reason is about numbers being representable at
   all; the other is about speed + identical saved output + no timezone traps.)

2. **Two trades share the same nanosecond, from two sources. What decides which the
   strategy sees first? Then: why must this be stable across runs, and what breaks
   silently if it isn't?** (Part 4. Decider: source priority, then read order. The
   subtlety: the choice is arbitrary, but must be *identical every run*, or records
   stop matching, P&L drifts, and reruns fail.)

3. **What concretely breaks if the clock can move backward?** (Part 3. Either:
   look-ahead bias, because the strategy's memory already contains the future
   relative to the older event; or an order filling at a time earlier than it was
   placed — a trade before it exists.)

If those three come out cleanly in your own words, you've got Task 1 cold.

---

## Mini-glossary

- **Event** — an immutable record of one thing that happened at one instant.
- **Bar / Trade / Quote** — the three event kinds we have today.
- **Epoch** — the fixed origin for time: midnight, 1 Jan 1970, UTC.
- **Nanosecond** — one-billionth of a second; our unit of time, stored as an integer.
- **Look-ahead (bias)** — a strategy seeing information from the future; the cardinal sin.
- **Clock** — the engine's single source of "current simulated time."
- **Monotonic (non-decreasing)** — only stays the same or increases, never decreases.
- **Total order** — a rule that gives *every* pair of items a definite, repeatable ranking.
- **Frozen** — cannot be modified after creation.
- **Slotted** — stored in a lean fixed shape; smaller and faster at scale.
- **Deterministic / reproducible** — same inputs always produce byte-for-byte the same outputs.
