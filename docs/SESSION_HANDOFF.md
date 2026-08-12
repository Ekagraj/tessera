# Session handoff — how to continue this project

**If you are a Claude Code session picking this project up cold, read this file
first.** It tells you *what has been done*, *what to do next*, and — most importantly
— *the exact way this project is being built*, so the work continues seamlessly in
the same style. Then read the documents listed in "Orient yourself" below.

This file is kept up to date after every task. If it disagrees with `PROGRESS.md`
about status, `PROGRESS.md` is the source of truth for *what's done*; this file is
the source of truth for *how we work*.

---

## Who the user is, and how they want to work

- The user is **building this to learn** (backtesting engine as a portfolio / resume
  project). Understanding matters more than speed. They are newer to some tooling
  (git, markdown), so **explain in plain language** and don't assume jargon.
- They explicitly asked for **teaching**: for each task there is a from-scratch
  learning guide in `docs/learn/`. Keep writing these.
- **Do not do outward-facing or irreversible things without asking.** Specifically:
  never `git push` or create a GitHub remote unless they ask; the repo is local-only
  on purpose. Commit only when they say to.
- Recommend, don't overwhelm: give a recommendation with a short "why," not an
  exhaustive survey.

---

## Orient yourself (read in this order)

1. `CLAUDE.md` (repo root) — the house rules. Hard rules override any convenience.
2. `docs/ARCHITECTURE.md` — the **nine seams**. These interfaces are fixed; do not
   modify them without explicitly flagging it and explaining why.
3. `docs/WEEK1.md` — the task-by-task plan (Tasks 0–10) with prompts and acceptance
   tests. This is the spine of the work.
4. `docs/PROGRESS.md` — current status, a file-by-file guide, and a changelog.
5. `docs/decisions.md` — the design decisions made so far and why.
6. `docs/learn/` — the plain-language teaching guide for each completed task.

---

## Current state (update this after each task)

- **Done:** Tasks 0–9. Full pipeline works from the CLI: `tessera run` → run dir,
  `tessera verify` → OK, `tessera report` → metrics line + tearsheet PNG. All three
  load-bearing tests pass.
- **Next:** Task 10 — real data + README + results. NOT new engine code: pull free
  daily bars (Stooq/Yahoo) for a few liquid tickers into `data/`, run both strategies
  over ~10 years with and without costs, and write the README (what it is, mermaid
  diagram, nine seams one-liners, how to run, results framed around transaction-cost
  sensitivity). Then the end-of-week "interview me on the whole codebase" prompt.
- **Git:** local repo, no remote. Commits so far: Task 0–8. Task 9 is implemented but
  **not yet committed** (user commits when they choose).
- **Total tests passing:** 51 (adds `test_metrics.py` 6 to the previous 45).

---

## The per-task ritual (do this every task, in order)

This is the loop this session has followed. Reproduce it exactly.

1. **Decide if the task is "explain-first" or "just-implement."**
   - **Explain-first** (per CLAUDE.md): anything in `core/`, `execution/`,
     `portfolio/`, and the strategy seam (`strategy/base.py`). For these: present
     **2–3 design options with tradeoffs**, give a recommendation, and **WAIT** for
     the user's decision before writing any code. Using the `AskUserQuestion` tool to
     capture the choice (recommended option first, labelled "(Recommended)") has
     worked well.
   - **Just-implement** (per CLAUDE.md): `data/`, `metrics/`, `runner/cli.py`, tests,
     plotting. Implement directly, then show the result.
2. **Write the code and its test together**, never test-after. Keep modules under
   ~200 lines and follow the style rules below.
3. **Verify before claiming done.** Run all three checks and make them pass:
   ```
   cd /Users/ekagrajain/Desktop/Project
   . .venv/bin/activate
   python -m pytest -q          # all tests green
   ruff check .                 # clean
   mypy tessera/core tessera/execution   # strict; clean
   ```
4. **Append a `docs/decisions.md` entry** — raw material (what/alternatives/why/when
   to revisit), 4–5 sentences, written so the user can rewrite it in their own words.
5. **Write `docs/learn/taskN-<slug>.md`** — a from-scratch teaching guide in the same
   style as the existing ones: Part 0 the problem, then build up the concepts in
   plain language, a "summary you can recite" line per section, an "Answer these
   yourself" section that points to (does not spoon-feed) the answers, and a
   mini-glossary. Match the tone of `docs/learn/task1-events-and-clock.md`. **Every
   guide must include** (a) a **"Worked example with synthetic data"** section that
   hand-traces concrete made-up inputs through the logic step by step, and (b) a
   **"Which file and function did each step"** table mapping each step of that
   example to the actual `.py` file and function/type that performs it, so the guide
   doubles as a map of the codebase.
6. **Update `docs/PROGRESS.md`**: flip the tracker row to ✅, rewrite the file's row
   in the file-by-file guide from "stub" to a real description, add a changelog
   entry, and add the new learn-guide link.
7. **Update this file's "Current state"** section.
8. **Quiz the user.** Ask 3–4 skeptical-quant-interviewer questions about the design
   choices (WEEK1.md lists "You must be able to answer" questions per task — use and
   sharpen them). Offer to tell them where an answer is thin.
9. **Commit only if asked.** See commit conventions below.

---

## Style & rules that must hold (from CLAUDE.md / ARCHITECTURE.md)

- **Timestamps are integer nanoseconds** since the UTC epoch — never floats, never
  `datetime` inside the engine. Convert at the loader boundary only.
- **No look-ahead, structurally**: strategies get one event + a read-only context,
  never a collection containing the future.
- **No wall clock, no unseeded randomness**: simulated time comes from the event
  stream; randomness comes from a generator seeded by `RunConfig.seed`.
- **Strategies emit `Order`s only**; the engine owns fills, PnL, positions.
- **The engine emits records** (to a `Recorder`); it does not return a dataframe.
- **Metrics live outside the engine**, computed from a run directory after the fact.
- Python 3.11+. `dataclass(frozen=True, slots=True)` for events/orders/fills.
  `typing.Protocol` for interfaces, not ABCs. Prefer functions over classes unless
  there's genuine state. No inheritance deeper than one level. Modules under ~200
  lines. Every public function typed; every module has a docstring saying what it
  *owns*.
- **mypy strict applies only to `tessera/core` and `tessera/execution`** (the future
  Rust-port hot loop). Keep those two clean under strict.
- **Do not build speculatively.** Order-book replay, walk-forward, dashboards, the
  agent loop, the Rust core, etc. are scheduled for later — do not add them early.
  If the model starts scope-creeping, stop.
- **Do not edit the nine seams** without explicitly flagging it and justifying it.

---

## Environment specifics

- Working dir: `/Users/ekagrajain/Desktop/Project` (this *is* the repo root).
- A virtualenv exists at `.venv/` (gitignored). Activate with `. .venv/bin/activate`.
  Dev deps installed via `pip install -e ".[dev]"`. Interpreter is Python 3.11+.
- `runs/` and `data/` are gitignored working directories.
- If a tool (`pytest`/`ruff`/`mypy`) is "not found," the venv isn't active — activate
  it first.

---

## Commit conventions

- Commit **only when the user asks**. The repo is local; there is no remote and we do
  not add one without being asked.
- Git identity is already configured (`Ekagra <ekagraj2003@gmail.com>`).
- One logical commit per task is fine (Task 0 and Task 1 were split into two initial
  commits because the first commit necessarily included the scaffold).
- Commit message: a short imperative title like `Task N: <thing>`, a body explaining
  what and why, and **always** end with the trailer:
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  ```
- Before committing, sanity-check that `.venv/`, `runs/`, `data/`, `.DS_Store` are
  ignored (they are) and that `git status` is otherwise as expected.

---

## What "done" means for a task

All of: code + test written, `pytest`/`ruff`/`mypy` green, `decisions.md` appended,
`docs/learn/` guide written, `PROGRESS.md` and this file updated, and the user quizzed.
Only then move on (and only commit if asked).
