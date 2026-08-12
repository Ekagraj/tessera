# Understanding Task 7: recorder, config, and manifest

A from-scratch explanation, no code required. This task is less about clever algorithms
and more about **reproducibility** — making a run something you can save, describe, and
prove you can reproduce later.

Read it once, then try the "Answer these yourself" section.

---

## Part 0 — The problem we are actually solving

The engine (Task 6) produces a *stream of records* and pushes them at a "recorder." Up
to now our recorder just kept things in a list in memory. That's fine for tests, useless
for real work. Task 7 answers three practical questions:

1. **Where do the records go?** → a **recorder** that writes them to disk.
2. **How do we describe a run so it can be repeated?** → a **RunConfig**.
3. **How do we *prove* a saved run is reproducible?** → a **manifest** + a **verify**
   function.

Together these turn "I ran a backtest" into "here is a folder that fully describes and
contains a backtest, and I can regenerate it byte-for-byte."

---

## Part 1 — RunConfig: a run as one description

A **RunConfig** is a single frozen object holding *everything* needed to define a run:
which strategy, its parameters, which symbols, the date range, the data source, the cost
in basis points, the latency, the random seed, and the starting cash.

The important idea (Seam 7): **the engine takes exactly one RunConfig and knows nothing
about where it came from.** Today a command-line builds one. Later, a parameter-sweep
tool might generate 500 of them, or an AI agent might generate one from a hypothesis. To
the engine they're all just "a RunConfig came in." That's what lets the system grow
without the engine ever changing.

Because it's just data, a RunConfig can be turned into a plain dictionary and back —
which is how it gets saved into the manifest as JSON.

> Recite: *a RunConfig is the complete, portable description of a run; the engine
> consumes one and doesn't care who produced it.*

---

## Part 2 — Recorders: three flavors of the same socket

The engine calls `recorder.record(kind, payload)` for every fill, order, and portfolio
snapshot, then `recorder.close()` at the end. Anything shaped like that "socket" (the
`Recorder` protocol) can be plugged in. We built three:

- **ParquetRecorder** — the real one. It **buffers** records by kind (all the fills
  together, all the orders together, all the portfolio snapshots together) and, on close,
  writes each group to a **parquet** file: `fills.parquet`, `orders.parquet`,
  `portfolio.parquet`. (Parquet is a compact, columnar table format that tools like
  pandas read instantly.)
- **NullRecorder** — throws everything away. Useful when you only care about speed (a
  benchmark) and don't want the cost of writing files.
- **MultiRecorder** — a splitter: hand it several recorders and it forwards every record
  to all of them (e.g. write to disk *and* stream to a dashboard later).

Why buffer and write once, instead of streaming each record to disk? Because parquet is
columnar — it wants whole columns at once — and our runs are small (a few thousand daily
bars). If runs ever got huge (tick data), we'd switch to a streaming writer; the
interface wouldn't change.

> Recite: *ParquetRecorder buffers by kind and writes fills/orders/portfolio parquet on
> close; NullRecorder drops everything; MultiRecorder fans out. All satisfy one protocol.*

---

## Part 3 — The manifest: a run's birth certificate

A **manifest** (`manifest.json`) is a small file saved alongside the parquet output that
records *everything about how the run was produced*:

- the **full config** (so you know exactly what was run),
- the **git commit hash** (so you know exactly *which version of the code* ran it),
- a **content hash of the input data** (so you know the data hasn't changed underneath
  you),
- the **seed** (so any randomness repeats),
- the **python and library versions** (so you can match the environment),
- **wall-clock timings** (how long it took).

Why all of it? Because "reproducible" is a strong claim, and it's only true if *nothing*
silently varies: not the code, not the data, not the randomness, not the library
versions. The manifest pins down each of those. If someone hands you a run a year later,
the manifest tells you precisely how to recreate it.

One rule preserved here: the manifest needs the **wall clock** (for timings) and **git**
(for the commit) — but both live in the *runner*, never in the engine. The engine still
has no concept of real time; only the machinery around it does.

> Recite: *the manifest pins config + code version + data hash + seed + library versions
> + timings — everything that must not vary for a run to reproduce.*

---

## Part 4 — verify: proving the claim

Anyone can *say* a run is reproducible. `verify` **proves it**: it reads the manifest's
config, **re-runs it** into a throwaway folder, and checks that the new output matches the
saved output. If they match, reproducibility isn't a promise — it's demonstrated.

One design detail: to re-run a config you have to turn it back into a live run (resolve
the strategy, load the data). That wiring is the command-line's job (Task 8), so `verify`
takes the "how to run a config" function as an argument rather than hard-coding it. That
keeps the manifest code independent and testable on its own now.

We compare the *content* of the parquet tables (are the rows identical?) rather than raw
bytes, because two correct parquet files can differ in incidental metadata while holding
identical data. Identical rows is the claim that matters.

> Recite: *verify re-runs the manifest's config and asserts the fresh output matches the
> saved one — reproducibility demonstrated, not asserted.*

---

## Part 5 — What actually got built in Task 7

Three small files, plus a test.

- **`tessera/runner/config.py`** — `RunConfig` and its dict round-trip.
- **`tessera/runner/recorder.py`** — `ParquetRecorder`, `NullRecorder`, `MultiRecorder`.
- **`tessera/runner/manifest.py`** — `write_manifest` / `read_manifest`, the data hash,
  library versions, git commit, and `verify`.
- **`tests/test_runner.py`** — a run produces the three parquet files + a manifest, and
  `verify` passes on an identical re-run and fails when the re-run diverges.

---

## Worked example with synthetic data

Run a small `FlipFlop` strategy (buy when flat, sell when long) over five AAPL bars into a
`ParquetRecorder` pointed at `runs/demo/`, then write a manifest and verify it.

```
config = RunConfig(strategy="flipflop", symbols=["AAPL"], seed=7,
                   initial_cash=100_000, cost_bps=5.0, ...)

rec = ParquetRecorder("runs/demo")
run(bars, FlipFlop(), NaiveFillModel(BpsCostModel(5.0)), Book(100_000), rec)
rec.close()
```

After close, the folder contains:

```
runs/demo/
  fills.parquet       # one row per fill
  orders.parquet      # one row per order submitted
  portfolio.parquet   # one row per bar (equity curve lives here)
```

Now stamp it and prove it:

```
write_manifest("runs/demo", config, input_hash=data_hash([...]), timings={"wall_seconds": 0.7})
   -> runs/demo/manifest.json  { config, git_commit, data_hash, seed, versions, timings }

verify("runs/demo", run_fn)
   -> re-runs the same config into a temp dir, compares every parquet table
   -> True   (identical) ... and if run_fn used cost_bps=999 instead -> False
```

The `verify -> True` is the whole payoff: the run in `runs/demo/` is demonstrably
reproducible.

### Which file and function did each step

| Step above | File | Function / type |
|---|---|---|
| the run description | `tessera/runner/config.py` | `RunConfig` |
| writing the three parquet files | `tessera/runner/recorder.py` | `ParquetRecorder.record`, `.close` |
| drop / fan-out recorders | `tessera/runner/recorder.py` | `NullRecorder`, `MultiRecorder` |
| the birth-certificate file | `tessera/runner/manifest.py` | `write_manifest`, `read_manifest` |
| input-data content hash | `tessera/runner/manifest.py` | `data_hash` |
| code version + env | `tessera/runner/manifest.py` | `git_commit`, `library_versions` |
| proving reproducibility | `tessera/runner/manifest.py` | `verify` |
| the whole thing under test | `tests/test_runner.py` | `test_verify_passes_on_identical_rerun` |

---

## Answer these yourself

Cover the text and try these.

1. **Why does the engine take a RunConfig and "know nothing about where it came from"?**
   (Part 1. So sweeps/agents/CLI can all just produce configs without the engine ever
   changing — one input shape, many producers.)

2. **What must a manifest capture, and why each item?** (Part 3. Config, git commit, data
   hash, seed, versions, timings — because reproducibility fails if *any* of code, data,
   randomness, or environment silently varies.)

3. **What does `verify` actually do, and why compare content instead of raw bytes?**
   (Part 4. Re-runs the config and checks the new output equals the saved output;
   content-compare because correct parquet files can differ in incidental metadata.)

4. **Why is it fine for the recorder to buffer everything in memory here, and when would
   that stop being fine?** (Part 2. Runs are small and parquet is columnar; it would stop
   being fine at tick-data scale, where a streaming writer would replace it — same
   interface.)

If those come out cleanly in your own words, you've got Task 7 cold.

---

## Mini-glossary

- **RunConfig** — the complete, immutable description of a run.
- **Recorder** — the sink the engine pushes records to.
- **Parquet** — a compact columnar file format for tables.
- **NullRecorder / MultiRecorder** — drop-everything / fan-out variants of a recorder.
- **Manifest** — a JSON file capturing how a run was produced.
- **Content hash** — a fingerprint of a file's bytes (sha256) used to detect changes.
- **Seed** — the number that makes any randomness repeatable.
- **verify** — re-run a manifest's config and confirm identical output.
- **Reproducible** — same inputs always produce the same output.
