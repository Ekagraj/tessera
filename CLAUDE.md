# CLAUDE.md

Context for Claude Code working in this repository.

## What this is

An event-driven backtesting and strategy research platform. Timestamped market
events flow through an ordered queue, strategies react to them one at a time and
emit orders, an execution model decides fills, and every outcome is written to a
record stream that downstream tooling reads.

Read `docs/ARCHITECTURE.md` before writing code. The nine seams in section 2 are
fixed. Do not modify those interfaces without explicitly flagging it and explaining
why in your response.

## Hard rules

These override any instinct toward convenience.

1. **No lookahead, structurally.** Strategies receive one event at a time plus a
   read-only context. Never pass a dataframe, a full series, or any collection
   containing future data into strategy code. If a strategy needs history, it
   maintains its own rolling state.
2. **Timestamps are integer nanoseconds since the UTC epoch.** Never floats, never
   `datetime` objects inside the engine or event types. Convert at the loader
   boundary only.
3. **No wall clock, no unseeded randomness.** No `time.time()`, no
   `datetime.now()`, no bare `random` or `np.random`. Simulated time comes from the
   event stream; randomness comes from a generator seeded by `RunConfig.seed`.
4. **Strategies emit `Order` objects only.** They never compute fill prices, PnL,
   or position updates.
5. **The engine emits records, it does not return results.** Push to the `Recorder`.
   Never accumulate a dataframe inside the engine and return it.
6. **Metrics live outside the engine.** Anything derived from records goes in
   `metrics/`, computed from a run directory after the fact.
7. **Every public function gets a type hint. Every module gets a docstring
   explaining what it owns.** Not what it does line by line, what it is responsible
   for.

## Style

- Python 3.11+. `dataclass(frozen=True, slots=True)` for events, orders, fills.
- `typing.Protocol` for interfaces, not ABCs. Duck-typed and cheap to swap.
- Prefer plain functions over classes unless there is genuine state to hold.
- No inheritance deeper than one level.
- Keep modules under ~200 lines. If one grows past that, it is doing two jobs.
- Standard library and a short dependency list: numpy, pandas, pyarrow, pytest,
  matplotlib, typer. Ask before adding anything else.
- Format with ruff. Type check with mypy in strict mode on `tessera/core` and
  `tessera/execution`.

## How I want you to work with me

- **Explain before implementing anything in `core/`, `execution/`, or `portfolio/`.**
  Lay out two or three approaches with tradeoffs and wait for me to choose. These
  are the parts I need to understand deeply, so do not just write them.
- **For `data/`, `metrics/`, `runner/cli.py`, tests, and plotting, go ahead and
  implement.** Show me the result, do not ask first.
- **One component per session.** Do not touch files outside the component we are
  working on. If a change requires editing something else, say so and stop.
- **Write the test before or alongside the code**, never after as an afterthought.
- **When you finish a component, quiz me on it.** Ask three or four questions a
  skeptical interviewer would ask about the design choices, and tell me where my
  answers are weak. I need to be able to defend this code from memory.
- If you think a rule in this file is wrong for a specific case, say so directly
  rather than quietly working around it.

## Testing

`pytest`. Three tests matter more than the rest and should never be weakened to
make something pass:

- `test_no_lookahead.py`: a strategy that tries to reach beyond the current clock
  must raise, not silently succeed.
- `test_determinism.py`: the same config and seed run twice produces identical
  record files.
- `test_accounting.py`: cash plus mark-to-market equals equity at every timestamp.

## Decision log

After any non-trivial design choice, append an entry to `docs/decisions.md`:
what was decided, what the alternatives were, why this one, and what would make us
revisit. Keep entries short, four or five sentences. I write these in my own words,
so give me the raw material rather than the finished paragraph.

## Current state

Week 1. Building the minimum end-to-end path: load daily bars from CSV, run a
moving average crossover strategy, fill naively at next open with a fixed cost,
write records to disk with a manifest, produce a tearsheet from those records.

Not yet built, and do not add speculatively: order book replay, market impact,
walk-forward, purged CV, the dashboard, the agent loop, the Rust core.
