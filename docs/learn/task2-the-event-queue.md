# Understanding Task 2: the event queue

A from-scratch explanation, no code required. By the end you should be able to
answer the three interview questions yourself, in your own words, and know *why*
each answer is right.

Read it once top to bottom, then try the "Answer these yourself" section.

---

## Part 0 — The problem we are actually solving

In Task 1 we built a single **event** (a thing that happened at an instant) and a
**clock** (what instant we're on). But real data doesn't come as one tidy stream.
It comes from **several sources at once**:

- daily bars for AAPL from one file,
- daily bars for MSFT from another file,
- later: a trades feed, a quotes feed, maybe a live socket.

The engine can only process **one event at a time, in time order**. So something
has to stand between "a pile of separate sources" and "one clean, correctly ordered
line of events." That something is the **queue**.

Its whole job: *take several streams and produce a single stream in time order,* so
the engine never has to think about where an event came from. And it has to do that
without (a) running out of memory, and (b) ever getting the order wrong — because
wrong order is the look-ahead bug from Task 1 sneaking back in through the side door.

Think of it like a **zip merge at a highway on-ramp**: several lanes of cars
feeding into one lane, and the merge rule has to be fair and consistent every time.

---

## Part 1 — The key assumption: each source is already sorted

Here's the observation that makes everything cheap: **each individual source is
already in time order.**

- A CSV of AAPL daily bars runs Jan 1, Jan 2, Jan 3… — chronological by nature.
- A live feed *arrives* in time order — that's what "live" means.

So we're never sorting from scratch. We're **merging already-sorted lines** into one
sorted line. That's a much easier problem than "sort a giant unsorted pile," and it's
the difference between an approach that scales and one that doesn't.

(What if a source is secretly *not* sorted — a bad file with a row out of order? We
treat that as a data bug and crash loudly. More in Part 4.)

---

## Part 2 — Three ways to merge, and why two of them are traps

Say we have 50 million events across a few sources. How do we produce them in order?

### Trap 1 — "Read everything, then sort"
Load every event from every source into one enormous list, sort the whole thing,
then hand them out one by one.

Why it's tempting: it's one line of code and the sort handles all the ordering.

Why it's a trap:
- **Memory.** You're holding all 50 million events in RAM at once. Even a lean event
  is ~100–200 bytes, so that's **5–10 gigabytes** sitting in memory before you
  process a single one. Your laptop chokes. And it gets *worse* as you add data —
  exactly the wrong direction.
- **A stall before anything happens.** Sorting 50M items takes real time, and you
  do all of it *up front*. The run freezes, then starts.
- **Live data is impossible.** You can't "read everything" from a live feed — it
  never ends. This approach structurally cannot ever stream.

### Trap 2 — "Pre-sort everything into one big file"
Run a separate step that merges all sources into a single sorted file on disk, then
have the engine just read that file top to bottom.

Why it's tempting: reading a pre-sorted file at run time is blazing fast and uses
almost no memory.

Why it's a trap *as the main mechanism*:
- It adds a **build step** and a **duplicate copy** of all your data on disk.
- The file goes **stale** the moment a source changes — now you have a caching bug.
- And again: **live data is impossible** — you can't pre-sort the future.

It's a fine *optimization* to keep in your back pocket for repeated runs over data
that never changes. It's a bad *foundation*.

### The good one — "Heap-based k-way merge"
This is the approach used to merge sorted files, and it's beautiful. Here's the
mental model.

Imagine each source is a **stack of cards, already sorted with the earliest on
top**. You want to produce one sorted pile from all the stacks. You don't need to
look at every card. You only ever compare the **top card of each stack**:

1. Look at the top card of every stack.
2. Take the earliest one, put it on your output pile.
3. That stack now has a new top card. Compare the tops again.
4. Repeat until all stacks are empty.

At any moment you're only holding **one card per stack** — not the whole deck. If
there are 4 sources, you're comparing 4 cards, whether the stacks are 10 cards tall
or 10 million.

The **heap** (a "priority queue") is just the efficient tool for step 2 — "which of
these few tops is the earliest?" — so you don't re-scan all of them each time. That's
the only reason the word "heap" appears; the idea is just *"always take the earliest
of the current tops."*

---

## Part 3 — Why "heap" beats "just sort them all"

This is question 1, so nail the contrast:

|  | Read-all-and-sort | Heap merge |
|---|---|---|
| How many events in memory at once | **all N** (all 50M) | **k** (one per source, ~4) |
| Memory at 50M events | ~gigabytes | ~a few hundred bytes |
| When does the first event come out | after sorting all N | **immediately** |
| Can it stream a live feed | no | **yes** |

The one-sentence version: **`sorted()` needs the whole dataset in memory and does
all its work before giving you anything; the heap holds only one event per source
and streams results as it goes.** At 50 million events that's the difference between
"needs gigabytes and stalls" and "needs kilobytes and starts instantly."

The technical footnote (nice to know): sorting everything costs about `N log N`
work; the heap merge costs about `N log k`, where `k` is the tiny number of sources.
Since `k` is like 4 and `N` is 50 million, `log k` is much smaller than `log N`. So
the heap merge is even a bit *less* total work — and uses almost no memory doing it.

---

## Part 4 — Handling ties, and handling bad data

**Ties (two events at the same instant, from different sources).** We already solved
this in Task 1: the ranking label `ordering_key = (ts, source_priority, seq)`. The
heap compares events by that label, so when timestamps tie, the source listed first
wins, then read order. Because that label is a *total order* (every event gets a
unique one), the merge is perfectly deterministic — run it a thousand times, same
output. (This is also why the heap never has to compare the events themselves,
which are deliberately not comparable — the labels are always unique, so it never
needs to fall back to comparing the actual event objects.)

**Bad data (a source that isn't actually sorted).** The whole heap merge *assumes*
each source is internally in order. What if a file has a row out of place — say
timestamps go 1, 3, **2**? We made a deliberate choice: **crash loudly.** The moment
a source hands out an event older than its own previous one, the queue stops with an
error naming the source and the two timestamps.

Why crash instead of quietly fixing it? Because an out-of-order source is a **data
bug** — your input is wrong, and you want to know *now*, at the door, not after a
week of confusing backtest results. "Silently re-sort it for them" would (a) hide
the bug and (b) drag back the memory/stall costs we just escaped by buffering the
source. Loud failure at the boundary is cheaper than a silent wrong answer
downstream. (This is the same philosophy as the clock refusing to move backward.)

---

## Part 5 — What "streaming" buys us, and the live-feed question

The heap merge produces events **lazily** — it computes the next one only when asked,
and it never reads ahead further than one event per source. That laziness is what
keeps memory flat, and it's also what makes **live data** a non-event later.

Here's the payoff for question 3. Today every source is a file. Later, one source
might be a live market feed. **What changes in the merge? Almost nothing** — a live
feed is *just another already-sorted source* (events arrive in time order). The heap
doesn't care whether a card came from a file or a socket; it just takes the earliest
top.

Two things *do* differ with a live feed, worth mentioning to sound complete:
- It can **block** — when you ask for the next event, it might have to *wait* for the
  market to produce one (whereas a file always has the next line ready). That's fine;
  it just means "wait," not "reorder."
- It **never ends** — there's no bottom of the stack. Also fine: the merge already
  handles sources of any length, including endless ones (our test literally merges
  two infinite sources and pulls the first ten).

So the design goal from the architecture holds: adding live data is "**add a
source**," not "rewrite the engine."

---

## Part 6 — What actually got built in Task 2

One file, plus a test.

- **`tessera/core/queue.py`** — a function `merge(sources)` that takes several
  event streams and lazily yields one time-ordered stream, using a heap and the
  Task 1 `ordering_key`. It holds one event per source at a time (flat memory), and
  raises `QueueError` if any source is internally out of order.
- **`tests/test_queue.py`** — proves: three sources merge into one correct order;
  identical timestamps break by source priority; an out-of-order source raises; and
  the merge is lazy (it merges two *infinite* sources and happily returns the first
  ten, which would be impossible if it tried to read everything first).

---

## Worked example with synthetic data

Let's hand-trace the heap merge on tiny synthetic sources. To keep it readable I'll
write timestamps as small day numbers (1, 2, 3…) instead of the giant nanosecond
integers — the logic is identical. Everything here is done by **`merge(sources)` in
`tessera/core/queue.py`**, using **`ordering_key` from `tessera/core/events.py`**.

Three sources (three CSV files), each already sorted in time:

```
Source 0 (priority 0)   Source 1 (priority 1)   Source 2 (priority 2)
   ts 1                     ts 2                     ts 3
   ts 4                     ts 5                     ts 6
   ts 7                     ts 8                     ts 9
```

### The trace

The heap holds **one pending event per source** at a time — its "top card." Each
entry is `(ordering_key, event)`, and `ordering_key = (ts, source_priority, seq)`.
`_push_next(...)` (a helper in `queue.py`) pulls the next event from a source and
pushes it; the main loop pops the smallest and refills from that same source.

```
PRIME: pull the first event from each source and push it.
  heap = [ (1,0,0)->S0,  (2,1,0)->S1,  (3,2,0)->S2 ]     # 3 items = one per source

pop (1,0,0) -> OUTPUT ts 1        refill S0 with ts 4 -> (4,0,1)
  heap = [ (2,1,0)->S1,  (3,2,0)->S2,  (4,0,1)->S0 ]

pop (2,1,0) -> OUTPUT ts 2        refill S1 with ts 5 -> (5,1,1)
  heap = [ (3,2,0)->S2,  (4,0,1)->S0,  (5,1,1)->S1 ]

pop (3,2,0) -> OUTPUT ts 3        refill S2 with ts 6 -> (6,2,1)
  heap = [ (4,0,1)->S0,  (5,1,1)->S1,  (6,2,1)->S2 ]

pop (4,0,1) -> OUTPUT ts 4        refill S0 with ts 7 -> (7,0,2)
  ... and so on ...

FINAL OUTPUT ORDER: 1, 2, 3, 4, 5, 6, 7, 8, 9
```

The thing to notice: **the heap never holds more than 3 items**, even though 9
events flow through it — and it would still be 3 if each source had ten million
events. That "3" is the number of sources (`k`). *That* is the O(k) memory story
from Part 3, made concrete. `sorted(all_events)` would instead have all 9 (all
10,000,000) sitting in memory at once.

### A tie, made concrete

Give source 0 and source 1 an event at the *same* ts = 5:

```
heap = [ (5,0,0)->S0,  (5,1,0)->S1 ]
```

Compare the keys left to right: `ts` ties (both 5), so look at the next number —
`0` vs `1`. `(5,0,0) < (5,1,0)`, so **source 0's event comes out first**. Same
tie-break rule as Task 1, now doing real work inside the merge. Because the keys are
always unique, the heap never has to compare the `Bar` objects themselves (which
aren't comparable) — it only ever compares keys.

### The out-of-order crash, made concrete

Feed a single source whose timestamps go `1, 3, 2` — the last one steps backward:

```
prime: push ts 1                       last_ts[0] = 1
pop ts 1 -> OUTPUT 1;  refill ts 3     3 >= 1 ok, last_ts[0] = 3
pop ts 3 -> OUTPUT 3;  refill ts 2     2 < 3  ->  QueueError!
   "source 0 yielded ts 2 after 3; each source must be sorted by non-decreasing ts"
```

The merge crashes the moment it sees the bad row (in `_push_next`), naming the
source and both timestamps — instead of silently emitting `1, 3, 2` and poisoning
the backtest. This is the "crash loudly" decision from Part 4 in action.

### Which file and function did each step

| Step above | File | Function / type |
|---|---|---|
| the synthetic `Bar` sources | `tessera/core/events.py` | `Bar` |
| the ranking keys | `tessera/core/events.py` | `ordering_key(ts, source_priority, seq)` |
| priming + refilling the heap, the sorted-check | `tessera/core/queue.py` | `_push_next(...)` |
| pop-smallest / yield / refill loop | `tessera/core/queue.py` | `merge(sources)` |
| the out-of-order crash | `tessera/core/queue.py` | `QueueError` |
| the "merges 3 sources correctly" test | `tests/test_queue.py` | `test_merges_three_sources_into_one_ordered_stream` |
| the "lazy over infinite sources" test | `tests/test_queue.py` | `test_merge_is_lazy_and_flat_memory` |

---

## Answer these yourself

Cover the text and try these.

1. **Why a heap-based merge instead of just `sorted(all_events)`?** (Parts 2–3.
   `sorted` holds all N in memory and does everything up front; the heap holds one
   event per source and streams. Memory and first-event latency are the crux.)

2. **What is the memory profile of the heap merge at 50 million events?** (Part 3.
   O(k) — one event per source, a few hundred bytes — versus O(N), gigabytes, for
   read-all-and-sort. It does **not** grow with the number of events.)

3. **What changes when one source is a live feed instead of a file?** (Part 5.
   Almost nothing — a live feed is just another sorted source. The only differences:
   asking for the next event may *block* (wait) rather than reorder, and the source
   never ends. The merge itself is unchanged.)

If those come out cleanly in your own words, you've got Task 2 cold.

---

## Mini-glossary

- **Source** — one stream of events (a file, later a live feed), already in time order.
- **Merge** — combining several sorted streams into one sorted stream.
- **k-way merge** — a merge of `k` sources at once (`k` = number of sources).
- **Heap / priority queue** — a structure that cheaply answers "which of these is smallest?"; used to pick the earliest current event.
- **Lazy / streaming** — producing the next item only when asked, instead of computing everything up front.
- **O(k) vs O(N)** — memory that grows with the number of *sources* (tiny) vs the number of *events* (huge).
- **ordering_key** — the `(ts, source_priority, seq)` label from Task 1 that gives every event a unique, repeatable rank.
- **QueueError** — raised when a source is internally out of time order (a data bug).
