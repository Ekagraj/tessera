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
