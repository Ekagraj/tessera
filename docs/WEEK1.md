# Week 1 plan and prompts

Goal by end of week 1: one command runs a moving average crossover strategy on
daily bars for one symbol, writes a reproducible run directory, and produces a
tearsheet. Roughly 800 lines of Python. This is already a resume line.

Work through the tasks in order. Each has a prompt to paste, an acceptance test,
and questions you must be able to answer before moving on. If you cannot answer
them, go back and read the code rather than continuing.

---

## Task 0: repo setup

Create the repo, drop in `CLAUDE.md` at the root and `ARCHITECTURE.md` in `docs/`,
then start Claude Code.

**Prompt:**

> Read CLAUDE.md and docs/ARCHITECTURE.md fully before doing anything.
>
> Then set up the project skeleton: pyproject.toml with the dependencies listed in
> CLAUDE.md, ruff and mypy config (mypy strict on tessera/core and
> tessera/execution only), the full directory tree from ARCHITECTURE.md section 4
> with empty `__init__.py` files and a one-line module docstring in each stating
> what that module owns, a .gitignore covering runs/ and data/, and an empty
> docs/decisions.md with a heading.
>
> Do not implement any logic yet. Show me the tree when done.

**Acceptance:** `pytest` runs and collects zero tests without erroring. `ruff check`
passes.

---

## Task 1: event types and the clock

This is a core component, so make it explain first.

**Prompt:**

> We are building the event types and simulated clock (seams 1 and 9 in
> ARCHITECTURE.md).
>
> Before writing code, give me two or three design options for each of these
> questions, with the tradeoff for each, then wait for my decision:
>
> 1. How do we handle two events with an identical timestamp? Ordering has to be
>    deterministic across runs and across data sources.
> 2. Should Event be a frozen dataclass hierarchy, a tagged union, or a struct of
>    arrays? Consider that we will later push this loop into Rust and that we will
>    process millions of events per run.
> 3. Where does the boundary sit between "loader converts human timestamps to
>    integer nanoseconds" and "engine only ever sees integers"?
>
> Do not write any code yet.

After you choose, second prompt:

> Implement the decisions we just made in tessera/core/events.py and
> tessera/core/clock.py. Include a test that the clock only moves forward and that
> identical-timestamp events order deterministically. Then append a decisions.md
> entry with the raw material for me to write up.

**Acceptance:** tests pass. Feeding events out of order raises rather than silently
reordering.

**You must be able to answer:**
- Why nanoseconds as an integer instead of a datetime or a float?
- Two trades arrive with the same nanosecond timestamp from two different files.
  What determines which one your strategy sees first, and why does it matter that
  this is stable?
- What breaks if the clock is allowed to move backward?

---

## Task 2: the event queue

**Prompt:**

> Now the ordered event queue (tessera/core/queue.py). It merges one or more event
> sources into a single time-ordered stream.
>
> Before coding, explain the tradeoff between: reading everything into memory and
> sorting, versus a k-way merge over sorted iterators using a heap, versus a
> single pre-sorted file. Consider memory at 50M+ events, whether sources arrive
> already sorted, and what happens when we later add live streaming.
>
> Recommend one, tell me why, then wait.

**Acceptance:** merging three unsorted-relative-to-each-other sources yields one
correctly ordered stream. Memory stays flat over a large synthetic input.

**You must be able to answer:**
- Why a heap-based merge instead of `sorted(all_events)`?
- What is the memory profile of your choice at 50 million events?
- What changes when one source is a live feed rather than a file?

---

## Task 3: strategy protocol and context

This is the seam that makes lookahead impossible. Understand it properly.

**Prompt:**

> Build tessera/strategy/base.py: the Strategy protocol, the Context object, and
> the Order dataclass, following seams 2 and 3 in ARCHITECTURE.md.
>
> Before coding, explain how you would make it structurally impossible for a
> strategy to read future data, and give me two options with different strictness
> and performance tradeoffs. Consider that Context is created or updated on every
> single event, so allocation cost matters.
>
> Also tell me: what should Context expose, and what should it deliberately not
> expose? Wait for my decision before writing anything.

Then:

> Implement it, plus tests/test_no_lookahead.py. The test should include a
> deliberately cheating strategy that tries to access data beyond the current
> clock, and assert that it fails loudly rather than succeeding.

**Acceptance:** the cheating strategy raises. A legitimate strategy maintaining its
own rolling window works fine.

**You must be able to answer:**
- Walk me through exactly how a strategy computes a 50 day moving average without
  ever seeing a dataframe.
- What is on Context and why is each thing safe to expose?
- Someone hands you a strategy that produced a Sharpe of 4. What is the first thing
  you check, and how does this design let you rule it out quickly?

---

## Task 4: portfolio accounting

**Prompt:**

> Build tessera/portfolio/book.py and accounting.py: positions, cash, realized and
> unrealized PnL, mark to market.
>
> Before coding, explain how you will handle: partial fills, a position flipping
> from long to short in one fill, and the choice between average-cost and FIFO lot
> accounting for realized PnL. Give me the tradeoff on that last one and a
> recommendation.

Then:

> Implement it plus tests/test_accounting.py asserting that cash plus mark-to-market
> position value equals total equity at every timestamp, including across a
> long-to-short flip and a partial fill.

**Acceptance:** the equity invariant holds through a scripted sequence of awkward
fills.

**You must be able to answer:**
- Average cost or FIFO, and what does the choice change?
- A fill takes you from long 100 to short 50. What happens to realized PnL?
- Where does unrealized PnL get marked, and what price do you mark it at?

---

## Task 5: naive fill model and costs

**Prompt:**

> Build tessera/execution/base.py (FillModel and CostModel protocols),
> naive.py (NaiveFillModel: market orders fill at the next bar's open) and
> costs.py (BpsCostModel: fixed basis points on notional).
>
> Include the latency plumbing from seam 5 now, with latency_ns defaulting to 0:
> submitted orders go into a pending queue with an arrival_ts and cannot fill
> before the clock reaches it.
>
> Before coding, explain why filling at the next open rather than the current close
> is the correct naive default, and what specifically goes wrong with the
> alternative. Then explain what NaiveFillModel is lying about, so I can list the
> lies in decisions.md.

**Acceptance:** an order submitted on bar N fills at bar N+1's open, never bar N's
close. Setting latency_ns above one bar's duration delays the fill accordingly.

**You must be able to answer:**
- What are the five things NaiveFillModel gets wrong about reality?
- Why build the pending-order queue now when latency is zero?
- A strategy is profitable only when filling at the close. What does that tell you?

---

## Task 6: the engine loop

The centerpiece. This is what interviewers will ask about.

**Prompt:**

> Build tessera/core/engine.py: the main loop tying together the queue, clock,
> strategy, fill model, portfolio, and recorder.
>
> Before coding, write out the exact ordering of operations within a single
> iteration and justify it. Specifically: when the clock advances to a new event,
> in what order do we (a) advance the clock, (b) let pending orders arrive,
> (c) attempt fills, (d) update the portfolio, (e) call the strategy, (f) record?
> Any wrong ordering here introduces subtle lookahead. Give me the ordering, then
> give me one plausible-but-wrong alternative ordering and explain precisely what
> bias it introduces.
>
> Also: keep the boundary narrow enough that this loop can later be replaced by a
> Rust implementation. Tell me what that constrains.
>
> Do not write code until I confirm the ordering.

**Acceptance:** end to end run completes on synthetic data. `test_determinism.py`
passes: same config and seed, byte-identical output.

**You must be able to answer:**
- Recite the iteration order and justify each position.
- Give me an ordering that looks reasonable and is subtly wrong, and name the bias.
- What in this loop would have to change to run it in Rust?
- Where would you add a latency model for the strategy's own compute time?

---

## Task 7: recorder, config, manifest

Let it implement this one directly.

**Prompt:**

> Implement tessera/runner/: RunConfig (seam 7), the Recorder protocol with
> ParquetRecorder, NullRecorder and MultiRecorder (seam 6), and manifest write plus
> verify.
>
> The manifest must capture: the full config, git commit hash, a content hash of
> the input data, seed, python and library versions, and wall clock timing. Add a
> `verify` function that re-runs a manifest and asserts identical output.
>
> Go ahead and implement, then show me.

**Acceptance:** a run produces `runs/<id>/` containing manifest.json, fills.parquet,
portfolio.parquet, orders.parquet. `verify` on that directory passes.

---

## Task 8: strategies and CLI

**Prompt:**

> Implement two example strategies in tessera/strategy/examples/: ma_crossover.py
> (buy when fast SMA crosses above slow SMA, flat otherwise) and reversal.py (buy
> after a down day, sell after an up day). Both maintain their own rolling state,
> no dataframes.
>
> Then a typer CLI at tessera/runner/cli.py: `tessera run --strategy ma_crossover
> --symbol AAPL --start 2015-01-01 --end 2024-12-31 --params fast=10,slow=50`.
>
> Also a CSV bar loader in tessera/data/sources/csv_bars.py. Go ahead and implement.

**Acceptance:** the command runs end to end and produces a run directory.

---

## Task 9: metrics and tearsheet

**Prompt:**

> Implement tessera/metrics/: returns.py computing equity curve, drawdown series,
> total and annualized return, annualized volatility, Sharpe, max drawdown,
> turnover, hit rate, and average win over average loss, all read from a run
> directory rather than from the engine. Then tearsheet.py producing a single
> matplotlib figure: equity curve, underwater drawdown plot, rolling 60 day Sharpe,
> and return distribution.
>
> Add `tessera report runs/<id>` to the CLI. Go ahead and implement.

**Acceptance:** `tessera report` on a completed run produces a PNG.

**You must be able to answer:**
- How do you annualize a Sharpe ratio from daily returns, and what assumption does
  that make?
- Why compute metrics from records rather than inside the engine?

---

## Task 10: get the data, run it, write it up

Pull free daily bars for a handful of liquid tickers. Stooq and Yahoo both work for
daily data with no key. Run both strategies over ten years.

Expect both to lose money after costs. That is the correct result and it is the
story you tell.

**Prompt:**

> Write the README: what the system is, the architecture diagram in mermaid, the
> nine seams summarised in one line each, how to run it, and a results section
> showing the MA crossover and reversal results with and without costs.
>
> Frame the results section around what the experiment demonstrates about
> transaction cost sensitivity, not around whether the strategies made money.

**Then, at the end of the week, run this:**

> You are a senior engineer at a quantitative trading firm interviewing me about
> this project. Read the whole codebase. Ask me ten questions, starting broad and
> getting progressively more specific, the way a real interviewer narrows in.
> Ask one at a time and wait for my answer. After each answer, tell me honestly
> whether it would satisfy an interviewer, and if not, what was missing.

That last prompt is the single most valuable one in this document. Run it at the
end of every week.

---

## Resume line after week 1

Even at this stage you can write:

> Built an event-driven backtesting engine with structurally enforced point-in-time
> data access, deterministic seeded replay, and manifest-based run reproducibility;
> benchmarked momentum and reversal strategies across 10 years of daily data to
> quantify transaction cost sensitivity.

Every claim there is true after week 1 and every claim is defensible in an
interview. Add to it as the platform grows.

---

## What to watch for

Three failure modes to catch early:

**Scope creep from the model.** If Claude Code starts adding order book handling,
walk-forward, or a web server before week 6, stop it. The architecture is designed
so those are cheap later. Adding them early is how the project stalls at 60 percent.

**Code you cannot explain.** After each task, close the editor and try to sketch the
component on paper. If you cannot, you have accumulated debt that will surface in an
interview. Go back and read it.

**Silent interface drift.** If a change requires editing one of the nine seams, that
is a signal something was designed wrong, not a routine edit. Stop and think about
it rather than letting the interfaces erode one convenience at a time.
