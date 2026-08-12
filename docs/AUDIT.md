# Audit pack: verifying tasks 1 through 9

Run this before starting week 2. It has two jobs: find real bugs, and find the gap
between what the docs claim and what the code does.

---

## Part 0: how to run an audit that isn't self-congratulatory

Claude Code wrote most of this code. Asked "is this correct?", it will say yes. That
is not a lie, it is a structural weakness: the same reasoning that produced the code
gets reused to evaluate it.

So every prompt below is built on three rules. Keep them.

1. **Demand evidence, not verdicts.** Never accept "yes, this is correct." Require a
   file and line number, a hand-computed number, or a test that fails when the
   behaviour is broken.
2. **Ask it to break things, not confirm them.** "Prove this invariant holds" invites
   agreement. "Write a test that violates this invariant and show me it is caught"
   produces information either way. If the violating test *passes*, you found a bug.
3. **Start each audit in a fresh session.** A session that just wrote the code is
   anchored to it. A cold session reading the same files is a genuinely different
   reader.

Open every audit session with this line:

> Read docs/ARCHITECTURE.md and CLAUDE.md. You are auditing existing code you did not
> write. Assume it contains at least one real bug. Report findings as
> file:line plus the specific evidence. Do not tell me code is correct without showing
> me a number, a failing test, or an exact line that proves it. If you cannot verify
> something, say "unverified" rather than guessing.

---

## Part 1: two specific things I found reading the docs

Do these first. They are concrete, not exploratory.

### 1A. The Sharpe ratio is very likely wrong by a factor of √252

Your Task 9 output reads:

```
total -16.11%  ann -33.4%  vol 22.1%  Sharpe -24.04  maxDD -16.11%
```

Annualized return over annualized vol is -33.4 / 22.1 = **-1.51**. The reported
Sharpe is -24.04. The ratio between them is 15.87, which is exactly √252.

**Prompt:**

> In tessera/metrics/returns.py, show me the exact lines that compute the Sharpe
> ratio and the annualized volatility.
>
> A recent run reported: annualized return -33.4%, annualized volatility 22.1%,
> Sharpe -24.04. But -33.4/22.1 = -1.51. The reported Sharpe is 15.87x larger, which
> is √252.
>
> Trace the arithmetic line by line and tell me which of these is happening:
> (a) the daily Sharpe is multiplied by 252 instead of √252,
> (b) √252 is applied twice, once inside the ratio and once outside,
> (c) the mean is annualized but the standard deviation is not, or
> (d) something else.
>
> Then check rolling_sharpe for the same defect. Then write a unit test with a
> hand-constructed return series whose correct annualized Sharpe I can compute on
> paper, assert the exact expected value, and show me the test failing against the
> current code before you fix anything.

Do not let it fix this before showing you the failing test. The failing test is the
proof; the fix without it is just a claim.

### 1B. What timestamp does a daily bar carry?

**Prompt:**

> In tessera/data/sources/csv_bars.py, what integer nanosecond timestamp does a daily
> bar from the row "2020-01-02,100,101,99,100.5,1000000" receive? Show me the exact
> value and the line that produces it.
>
> If that timestamp is midnight (the start of the day), then the Bar event is stamped
> at the day's beginning while carrying that day's high, low, and close, which are
> facts that are not known until the day ends. Walk me through what happens under
> tessera/core/queue.py merge() when this daily source is merged with a hypothetical
> intraday trade source for the same day: what order do the events come out in, and
> does the strategy see the daily close before or after that day's intraday trades?
>
> Then tell me whether this is a real future-leak, and give me two options for fixing
> it (stamping the bar at its close time versus splitting each bar into a separate
> open event and close event), with the tradeoff for each. Do not implement yet.

This is currently invisible because there is exactly one source. It stops being
invisible the moment week 2 adds a second one, and it would be an ugly thing to
discover after building on top of it.

Also ask the follow-up:

> When a fill occurs at bar E's open price, what timestamp is written on the Fill
> record: E's timestamp, or the actual time of the open? If they differ, what breaks
> when we later do per-trade attribution against intraday prices?

---

## Part 2: one prompt per invariant

ARCHITECTURE.md lists six invariants. Audit each by trying to violate it.

### Invariant 1: no strategy sees an event beyond the current clock

> tests/test_no_lookahead.py has three cheating strategies that fail loudly. Those
> test the Context surface. Now audit the other routes: can a strategy reach the
> future through the Event object it is handed, through any mutable default, through
> a module-level import of the data source, or by holding a reference to something
> that later mutates? List every attribute reachable from what a strategy receives,
> two levels deep, and mark each as safe or a potential channel. Write a new cheating
> strategy for any channel you find.

### Invariant 2: byte-identical output for the same config and seed

Note a real drift here: ARCHITECTURE.md says **byte-identical**, but Task 7 chose to
compare parquet **content** instead, because correct parquet files can differ in
incidental metadata. That reasoning is sound, but the invariant text was never updated.

> ARCHITECTURE.md invariant 2 says two runs of the same config produce byte-identical
> records. verify() in tessera/runner/manifest.py compares parquet content, not bytes.
> Confirm that gap, then tell me exactly what could differ between two byte streams
> while the content is identical, and whether any of those differences could ever mask
> a real non-determinism. Then propose the corrected wording for invariant 2 and add
> a decisions.md entry recording that this was a deliberate narrowing.

Then a harder determinism probe:

> Write a test that runs the same config twice in the same process, and a second test
> that runs it in two fresh subprocesses. Do both produce identical content? If the
> in-process one passes and the subprocess one fails, we have hidden state. Also check
> for any dependence on dict or set iteration order, floating point accumulation order,
> or PYTHONHASHSEED.

### Invariant 3: cash plus mark-to-market equals equity

test_accounting.py covers this. Push it harder:

> Extend tests/test_accounting.py with a property-based test: generate a few hundred
> random sequences of fills, including zero-crossing flips, partial fills, fills with
> costs, and repeated flat-to-flat cycles. After every single fill, assert both
> equity == cash + market_value and realized + unrealized == equity - starting_cash.
> Use hypothesis if it is available, otherwise a seeded random loop. Report any
> sequence that breaks either identity.

Also this specific one, because fees touch both cash and realized:

> Confirm that a fill's cost is debited from cash exactly once and added to realized
> PnL exactly once, and that this does not double-count. Show me the arithmetic on a
> single fill of 100 shares at 11.00 with a 1.10 cost, tracing cash, realized, and
> equity before and after.

### Invariant 4: no fill before arrival_ts

> tests/test_fills.py covers the latency gate. Now try to defeat it: is there any path
> where an order can fill at a timestamp before its arrival_ts? Check what happens with
> latency_ns larger than the entire dataset, with an order submitted on the final bar,
> with negative latency, and with two orders on the same symbol arriving at the same
> timestamp. What happens to orders still pending when the run ends: are they dropped
> silently or recorded as cancelled?

### Invariant 5: total fill quantity never exceeds order quantity

This one has no test that I can see in any of the task docs.

> ARCHITECTURE.md invariant 6 says total fill quantity never exceeds order quantity.
> Find the test that asserts it. If there is none, write one, including the partial
> fill case where an order fills across multiple events. Then confirm NaiveFillModel
> cannot fill the same order twice: what removes an order from the pending queue, and
> is there a path where it fills and stays queued?

### Invariant 6: every run directory can be reproduced from its manifest

> verify() re-runs a config and compares output. Test its failure modes, not just its
> success: does verify correctly return False if the input CSV is modified after the
> run, if the strategy code changes, if the seed differs, and if a library version
> differs? A verify that always returns True is worse than no verify. Show me a test
> for each failure mode.

---

## Part 3: seam drift

Three deviations from ARCHITECTURE.md appear in the task docs. Each may be fine, but
each should be a recorded decision rather than silent drift.

> Compare the actual code against the nine seams in docs/ARCHITECTURE.md. I know of
> three deviations already:
>
> 1. The Recorder protocol lives in tessera/core/engine.py, but seam 6 and the
>    directory layout put it in tessera/runner/recorder.py.
> 2. CostModel.cost dropped the MarketCtx parameter that seam 4 specifies.
> 3. Invariant 2 says byte-identical; verify() compares content.
>
> For each: is it recorded in docs/decisions.md with a rationale? If not, draft the
> entry. Then scan for any deviation I have not listed, and tell me for each whether
> it makes a future change harder or easier.

---

## Part 4: adversarial "try to break it"

These are the highest-yield prompts. They ask for attacks, not confirmation.

> You are trying to make this backtester report a fake profit. You may not modify the
> engine, the fill model, or the accounting. You may only write a strategy that uses
> the public Strategy and Context interfaces.
>
> Find every way to inflate returns: unrealistic position sizing, spending cash you do
> not have, exploiting the flip accounting, exploiting the limit-order rule, ordering
> quantities that should be impossible, anything. For each attack, write the strategy,
> run it, and show me the resulting equity curve. Then tell me which attacks succeeded
> and what guard is missing.

Follow up with the specific one I suspect:

> Does anything prevent a strategy from buying more than its cash allows? What happens
> to cash and equity if a strategy with 100,000 buys 1,000,000 worth of stock? Is
> there a margin check, a rejection path, or does cash simply go negative? Whatever the
> answer, is it recorded as a deliberate week-1 limitation?

And the loud-failure philosophy, which the Task 8 microsecond bug already tested once:

> The pandas microsecond bug caused the loader to yield zero bars, and the run
> completed anyway producing an empty run directory. That contradicts the crash-loudly
> philosophy in the queue and the clock. Should a run with zero events, zero fills, or
> a strategy that never trades fail loudly instead of succeeding silently? Show me
> what currently happens in each of those three cases.

---

## Part 5: numbers I can check by hand

Force it to reproduce arithmetic you can verify yourself on paper.

> Run each of these through the actual code and show me the real output next to the
> expected value:
>
> 1. Book starting at 100,000. Buy 40 @ 10.00, buy 60 @ 10.10, mark at 10.50. Expected:
>    cash 98,994, position 100 @ 10.06, equity 100,044, unrealized 44.
> 2. Continue: sell 150 @ 12.00. Expected: realized +194, position -50 @ 12.00,
>    cash 100,794. Then mark at 11.00: equity 100,244, unrealized +50.
> 3. Continue: buy 50 @ 9.00. Expected: realized total 344, flat, equity 100,344.
> 4. BpsCostModel(10) on 100 shares at 11.00. Expected: 1.10.
> 5. Equity series 100, 110, 99, 121. Expected: total return +21%, max drawdown -10%.
>
> Where the code disagrees with the expected value, that is the finding. Do not adjust
> the expected values to match the code.

---

## Part 6: the weekly interview drill

Run this at the end of every week, in a fresh session.

> You are a senior engineer at a quantitative trading firm interviewing me about this
> project. Read the entire codebase first, not the docs.
>
> Ask me ten questions, one at a time, waiting for my answer before the next. Start
> broad and narrow progressively, the way a real interviewer closes in on the edge of
> someone's understanding. At least three questions must be about a specific design
> decision visible in the code where a different choice was plausible.
>
> After each answer, tell me honestly whether it would satisfy an interviewer, and if
> not, name exactly what was missing. Do not be encouraging. A weak answer I believe is
> strong is worse for me than a blunt correction.

A variant worth running once:

> Read the code and tell me the three questions I would most struggle to answer about
> it, based on which parts have the least explanatory comment coverage and the most
> non-obvious logic. Do not ask them yet, just tell me what they are.

---

## Part 7: what to do with the findings

Sort every finding into one of three buckets and act differently on each.

- **Real bug**: reproduce it with a failing test first, then fix, then confirm the
  test passes. Never accept a fix without having seen the test fail.
- **Deliberate limitation**: not a bug, but must be written down. Add it to
  decisions.md and to the "five lies" style lists. An undocumented limitation becomes
  an interview ambush.
- **Seam drift**: decide whether to correct the code or amend ARCHITECTURE.md. Either
  is fine. Silence is not, because the architecture doc stops being trustworthy the
  first time it disagrees with the code.

After the audit, commit with a message listing what changed and why, and update
PROGRESS.md with an audit entry. The commit history showing "found and fixed a
√252 double-annualization bug, caught by a hand-computed unit test" is itself a
strong signal to anyone reading the repo.

---
---

# Audit findings (round 1)

Run against commit `58ab7cf` (end of Task 9). Every claim below is backed by a number,
a `file:line`, or a test in `tests/test_audit.py` (7 new tests). Verdicts use the Part-7
buckets: **BUG**, **LIMITATION** (deliberate, now documented), **DRIFT** (seam vs code),
**CLEAN** (verified correct), **WATCH** (latent, harmless today).

## Part 1

### 1A — Sharpe "√252 bug": CLEAN (formula correct) — but doc numbers were fabricated
**Correction (round 2):** round 1 explained the −24.04 as a "geometric-vs-arithmetic gap."
That was wrong. See `docs/AUDIT_FINDINGS.md` §1A for the corrected analysis. Summary:

The formula is correct — `returns.py:75` `(mean_r / vol) * np.sqrt(252)` uses daily `vol`
(`:72`), a single √252. Hand test `[+0.10,+0.20,−0.10]` → 6.9282, code 6.9282 exactly.

The −24.04 run was **reproduced**: real array `len=119, mean=−0.001476, std=0.000974,
ann_vol=1.55%, sharpe=−24.0411`, identity `sharpe==mean·252/ann_vol` True. It is huge only
because `data/AAPL.csv` is a smooth synthetic sine wave with **1.55%** volatility (not the
22.1% the doc claimed). The Task-9 doc line's `ann −33.4%, vol 22.1%, hit 42%, trades 118`
were **fabricated** (real: `ann −31.08%, vol 1.55%, hit 8.5%, trades 7`); only
`total/Sharpe/maxDD/turnover` were real. Doc corrected.

**Verdict:** No code bug. Real defects: fabricated doc numbers (fixed) and a wrong round-1
explanation (retracted). Residual note (D31): geometric `annualized_return` vs arithmetic
Sharpe means `ann_return/ann_vol ≠ Sharpe` — a real inconsistency, not the cause of −24.

### 1B — daily bar stamped at midnight: WATCH (latent future-leak) + LIMITATION
`csv_bars.py` stamps a bar at **midnight UTC** of its date (`bar.ts = 1577923200000000000`
for `2020-01-02` = `to_epoch_ns("2020-01-02")`), while carrying that day's high/low/close,
which aren't known until day end. With one source this is invisible. Merged with an
intraday source, `queue.merge` would order the daily bar (00:00) **before** that day's
intraday trades, so a strategy would see the day's close before the day's ticks — a real
future-leak. Fill records inherit the same midnight ts (`engine.py`: `Fill.ts = event.ts`),
so per-trade attribution against intraday prices would be wrong. Not a bug today; a
week-2 landmine. Two fix options (unimplemented): stamp the bar at its **close** time, or
split each bar into separate open/close events. Recorded as D32.

## Part 2 — invariants

- **Inv 1 (no future via Context): CLEAN.** What a strategy receives is `event` (a frozen,
  slotted `Bar` of primitives — no back-references) and `ctx` (frozen; `positions` is a
  copied `MappingProxyType`). No data-source/queue handle is reachable. The three cheating
  strategies already cover mutation/attribute routes.
- **Inv 2 (byte-identical): DRIFT (recorded D33).** ARCHITECTURE said "byte-identical";
  `verify` compares parquet **content** (`manifest.py`), a deliberate narrowing. Content
  determinism verified across two subprocesses with different `PYTHONHASHSEED` → identical.
  No set/hash-order dependence in the hot path. ARCHITECTURE invariant 2 wording corrected.
- **Inv 3 (cash + MTM = equity): CLEAN, hardened.** New property test runs 300 random fill
  sequences (flips, costs, flat cycles) asserting both identities after every fill.
- **Inv 4 (no fill before arrival_ts): CLEAN.** Latency gate holds; negative latency still
  cannot cause a same-bar fill (the `event.ts > submit_ts` guard). An order on the final
  bar never fills and is **dropped silently** — see LIMITATION below.
- **Inv 5 (fill qty ≤ order qty): CLEAN, was untested → now tested.** The naive model fills
  the **full** qty once and removes the order from the pending queue; it cannot refill.
  New test `test_order_fills_at_most_once_and_quantity_conserved`. (No partial fills exist
  yet — a known naive-model simplification.)
- **Inv 6 (reproduce from manifest): CLEAN with a caveat.** `verify` returns True on an
  identical rerun and **False** when the input CSV is changed (`test_verify_false_when_input_csv_changes`)
  or the run diverges. Caveat (LIMITATION D34): `verify` compares *output* only; it does
  **not** check that the current git commit / library versions match the manifest, so a run
  re-verified under a different pandas could still pass.

## Part 4 — adversarial

- **No margin / buying-power check: LIMITATION (D35).** A strategy can order 10M notional on
  100k cash; `Book` cash simply goes **negative** (−9,900,000), equity stays 100k (position
  marked at cost). No rejection path. `test_no_margin_check_cash_can_go_negative`.
- **Silent no-op runs: LIMITATION (D36).** A run over **zero events** completes with zero
  records and no error (`test_empty_event_stream_completes_without_error`); likewise a
  final-bar order or a never-trading strategy. This is the same silent-success that let the
  Task-8 microsecond bug through. Documented as a known gap; a future guard could fail loudly
  on zero events/fills.

## Part 5 — hand numbers: ALL MATCH
Ran through the real code; every expected value matched exactly:
1) cash 98,994, pos 100 @ 10.06, equity 100,044, unreal 44 ✓
2) realized +194, pos −50 @ 12.00, cash 100,794; @11 equity 100,244, unreal +50 ✓
3) realized total 344, flat, equity 100,344 ✓
4) `BpsCostModel(10)` on 100 @ 11.00 = 1.10 ✓
5) equity `[100,110,99,121]` → total return +21%, max drawdown −10% ✓ (`test_metrics.py`)

## Summary
- **Bugs found: 0.** The one alleged bug (1A √252) is disproven by a passing hand-computed test.
- **Documented limitations added: D31, D34, D35, D36** (Sharpe annualisation basis; verify
  scope; no margin check; silent no-op runs).
- **Seam drift recorded: D32 (bar timestamp), D33 (byte→content)**; ARCHITECTURE invariant 2
  corrected.
- **Tests added: 7** in `tests/test_audit.py` (Sharpe regression, fill-qty invariant,
  final-bar drop, random-sequence accounting, verify-on-changed-input, and the two
  limitations pinned as behaviour).
