# Architecture

This document exists so that every later feature is an *addition*, never a rewrite.
Read this before writing code. Read it again before agreeing to any change that
touches the interfaces in section 2.

## 1. What this system is

An event-driven simulation engine for testing trading strategies against historical
market data, plus the tooling around it: reproducible runs, distributed parameter
sweeps, and a research dashboard.

The engine is domain-agnostic in shape: timestamped events go in, a stream of
records comes out, and everything else reads that stream. Nothing downstream of
the engine reaches back into it.

## 2. The nine seams

These are the decisions that must be right on day one. Everything else is cheap to
change. If a proposed change modifies one of these, stop and think hard.

### Seam 1: everything is a timestamped event

Even on day one, when the only data is daily bars, data flows as discrete events
through an ordered queue. Never as a dataframe the strategy iterates over.

Why this is load-bearing: swapping daily bars for tick data or full order book
updates later becomes "add a new event type and a new loader." If you start with a
dataframe loop, that swap is a ground-up rewrite of the engine, the strategy API,
and every strategy you wrote.

```python
@dataclass(frozen=True, slots=True)
class Event:
    ts: int          # nanoseconds since epoch, UTC, always
    symbol: str

@dataclass(frozen=True, slots=True)
class Bar(Event):
    open: float; high: float; low: float; close: float; volume: float

@dataclass(frozen=True, slots=True)
class Trade(Event):
    price: float; size: float; aggressor: int   # +1 buy, -1 sell

@dataclass(frozen=True, slots=True)
class Quote(Event):
    bid: float; bid_size: float; ask: float; ask_size: float
```

Timestamps are integer nanoseconds. Not floats, not datetimes. Floats lose
precision at nanosecond scale and make ordering non-deterministic. Datetimes are
slow and carry timezone traps.

### Seam 2: the strategy never touches the data store

The strategy receives one event at a time plus a read-only context. It cannot
request "the last 50 bars" from a dataframe, because that dataframe would contain
the future.

```python
class Strategy(Protocol):
    def on_event(self, event: Event, ctx: Context) -> list[Order]: ...
```

`Context` exposes only: current simulated time, current positions, current cash,
and rolling state the strategy itself accumulated. If a strategy wants a 50-day
moving average it maintains its own ring buffer, updated as events arrive.

This feels annoying. It is the entire point. Lookahead bias becomes structurally
impossible rather than something you have to remember not to do.

### Seam 3: strategies emit intent, never outcomes

A strategy returns `Order` objects. It never computes a fill price, never computes
its own PnL, never updates a position. The engine owns all of that.

```python
@dataclass(frozen=True, slots=True)
class Order:
    symbol: str
    side: int              # +1 buy, -1 sell
    qty: float
    type: str              # "market" | "limit"
    limit_price: float | None = None
    tag: str = ""          # free-form, for attribution
```

Why: when you later replace the naive fill model with order book matching, no
strategy code changes. If strategies computed their own fills, every strategy
becomes wrong the moment execution gets realistic.

### Seam 4: execution is an interface with swappable implementations

```python
class FillModel(Protocol):
    def submit(self, order: Order, ts: int) -> None: ...
    def on_event(self, event: Event) -> list[Fill]: ...
```

Week 1 ships `NaiveFillModel`: market orders fill at the next bar's open, plus a
fixed cost in basis points. Later you add `QueuePositionFillModel` that matches
against level-2 book updates with queue priority and partial fills. Same interface,
zero changes elsewhere.

Costs live behind their own interface so commission, spread, and market impact can
each be swapped or turned off independently:

```python
class CostModel(Protocol):
    def cost(self, order: Order, fill_price: float, qty: float, ctx: MarketCtx) -> float: ...
```

### Seam 5: latency exists from day one, even at zero

Every order submitted at time `t` gets `arrival_ts = t + latency_ns`, and sits in a
pending queue until the clock reaches it. Week 1 sets `latency_ns = 0`, so behaviour
is unchanged.

Why build a delay queue you don't need yet: adding latency later means threading a
pending-order queue through the engine loop, the fill model, and the portfolio
accounting all at once. Building the plumbing now costs about twenty lines and
makes the later change a config value.

### Seam 6: the engine emits a stream, it does not return a result

The engine does not build a dataframe and return it. It pushes records to a `Recorder`
as they happen.

```python
class Recorder(Protocol):
    def record(self, kind: str, payload: dict) -> None: ...
    def close(self) -> None: ...
```

Week 1 ships `ParquetRecorder` writing to disk. Later, `WebSocketRecorder` streams
the same records to the dashboard for live run monitoring, and `NullRecorder` makes
benchmark runs fast. A `MultiRecorder` fans out to several at once.

Why: if the engine returns a dataframe at the end, live monitoring is a rewrite of
the engine. With a recorder, it is a new class.

Record kinds emitted: `fill`, `order`, `position`, `portfolio`, `signal`, `reject`.

### Seam 7: a run is a Config in and a Manifest out

```python
@dataclass(frozen=True)
class RunConfig:
    strategy: str                  # import path
    params: dict[str, Any]
    symbols: list[str]
    start_ts: int
    end_ts: int
    data_source: str
    fill_model: str
    cost_bps: float
    latency_ns: int
    seed: int
    initial_cash: float
```

The engine takes exactly one `RunConfig` and knows nothing about where it came from.

Week 1: the CLI builds one config. Week 7: the orchestrator generates 500 configs
and fans them out to workers. Week 10: the agent generates configs from a
hypothesis. All three are just "something produced a RunConfig." The engine never
learns about sweeps, queues, or agents.

Every run writes a `manifest.json` containing the config, the git commit hash, a
hash of the input data, the seed, the library versions, and wall clock timings.
Re-running a manifest must reproduce results exactly.

### Seam 8: metrics are computed offline from the record stream

The engine does not compute Sharpe ratios. A separate `metrics` package reads a run
directory and computes everything.

Why: you will add deflated Sharpe, walk-forward stitching, purged cross-validation,
and per-signal attribution later. All of those are new functions over the same
records, not engine changes. It also means you can recompute metrics for old runs
without re-running them.

### Seam 9: one clock, owned by the engine

No `time.time()`, no `datetime.now()`, anywhere in the engine, strategies, or fill
models. Simulated time comes only from the event stream. Randomness comes only from
a seeded generator passed down from the config.

Why: determinism. Without this, two runs of the same config differ, reproducibility
claims are false, and debugging is guesswork.

## 3. The Rust seam

The hot loop is `core/engine.py` plus `execution/`. Keep the boundary between that
and everything else narrow and boring: plain dataclasses with primitive fields
crossing in and out, no Python callbacks into user code from inside the loop other
than `Strategy.on_event`.

If the interface stays narrow, replacing the loop with a Rust implementation via
PyO3 later is a drop-in swap behind the same `Engine` class. If rich Python objects
leak across, it is not.

Do not write Rust yet. Just do not close the door on it.

## 4. Directory layout

```
tessera/
  core/
    events.py        # Event, Bar, Trade, Quote, BookUpdate
    clock.py         # simulated clock
    queue.py         # ordered event queue, merges multiple sources
    engine.py        # the loop
  execution/
    base.py          # FillModel, CostModel protocols
    naive.py         # NaiveFillModel
    costs.py         # BpsCostModel
  portfolio/
    book.py          # positions and cash
    accounting.py    # mark to market, realized vs unrealized
  strategy/
    base.py          # Strategy protocol, Context
    examples/
      ma_crossover.py
      reversal.py
  data/
    loader.py        # DataSource protocol -> Iterator[Event]
    sources/
      csv_bars.py
  runner/
    config.py        # RunConfig
    manifest.py      # manifest write and verify
    recorder.py      # Recorder protocol, ParquetRecorder, NullRecorder
    cli.py           # entry point
  metrics/
    returns.py       # equity curve, drawdown, Sharpe
    tearsheet.py     # plots
tests/
  test_no_lookahead.py
  test_determinism.py
  test_accounting.py
docs/
  ARCHITECTURE.md
  decisions.md
runs/                # gitignored, run output directories
data/                # gitignored, raw and processed data
```

Later additions and where they land, none of which disturb the above:

- Order book replay: `core/events.py` gains `BookUpdate`, `data/sources/lobster.py`,
  `execution/queue_position.py`
- Distributed sweeps: `orchestrator/` package, produces RunConfigs
- Dashboard: `api/` FastAPI service reading run directories, `web/` Next.js app
- Agent loop: `research/` package, produces RunConfigs and reads metrics
- Rust core: `rust/` crate, swapped in behind `core/engine.py`

## 5. Invariants

Any of these breaking is a bug, and each has a test.

1. A strategy can never observe an event with `ts` greater than the current clock.
2. Two runs of the same config with the same seed produce byte-identical records.
3. Cash plus mark-to-market position value equals total equity at every timestamp.
4. No order fills at a timestamp earlier than its `arrival_ts`.
5. Total fill quantity never exceeds order quantity.
6. Every run directory contains a manifest sufficient to reproduce it.

## 6. Things deliberately not built yet

Listed so they do not get quietly added. Each is scheduled, none belongs in week 1.

Order book matching, market impact, multi-asset portfolio optimization, live data
feeds, walk-forward, purged CV, deflated Sharpe, the dashboard, the agent loop,
the Rust core, short borrow costs, corporate actions.
