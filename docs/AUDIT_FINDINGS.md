# Audit findings — per-prompt, with evidence

Self-contained answers to every prompt in `docs/AUDIT.md`, run against commit `58ab7cf`
(end of Task 9) on Python 3.14 / pandas 3.0. Each entry gives: **what was run**, the
**evidence** (numbers / `file:line` / test), and a **verdict** using the Part-7 buckets:

- **CLEAN** — verified correct with a number or passing test
- **BUG** — reproduced defect (none found)
- **LIMITATION** — deliberate week-1 gap, now documented
- **DRIFT** — code deviates from ARCHITECTURE, now recorded
- **WATCH** — latent, harmless today, dangerous later

Regression tests live in `tests/test_audit.py` (7 tests). Full suite: **58 passing**.

---

## Part 1

### 1A — "Sharpe is wrong by √252"  →  CLEAN (formula correct); doc numbers were fabricated
**Ran:** traced the formula, hand-computed a known series, AND reproduced the exact `-24.04`
run (`reversal` on `data/AAPL.csv`, 5bps) printing the real returns array.
**Evidence:**
- `tessera/metrics/returns.py:75` → `sharpe = (mean_r / vol) * np.sqrt(periods_per_year)`
  uses `vol` = **daily** std (`:72`), not `ann_vol` (`:73`). Single √252 = textbook.
- Hand check: daily returns `[+0.10, +0.20, −0.10]` → Sharpe **6.9282**; code returns
  **6.9282** exactly (double-annualising = 109.98). → `test_sharpe_matches_hand_computed_value`.
- **Reproduced the −24.04 run.** Real array: `len=119`, `mean=−0.001476`, `std(ddof1)=0.000974`,
  `ann_vol=`**`1.55%`**, `sharpe=−24.0411`. Identity `sharpe == mean·252/ann_vol` holds
  (True). So −24.04 is the *correct* output of the formula on this data.
- **Why it is huge:** the volatility is genuinely tiny (1.55%), because `data/AAPL.csv` is a
  smooth synthetic sine wave (std ≈ 0.1%/day). A small negative mean ÷ a tiny std = a
  large-magnitude Sharpe. On realistic data (~15–25% vol) the same formula gives −3…+3.
**Correction to the earlier audit round:** my first pass claimed the −24.04 was explained by a
"geometric-vs-arithmetic gap." That was **wrong** — with a real 22.1% vol that gap is only
σ²/2 ≈ 2.4pp and cannot produce −24. The claim was anchored to a **fabricated** figure: the
Task-9 learn doc's report line (`ann −33.4%, vol 22.1%, hit 42%, trades 118`) did not come from
the code. The real values are `ann −31.08%, vol 1.55%, hit 8.5%, trades 7`; only
`total/Sharpe/maxDD/turnover` in that line were real (from the PNG title). The doc line has been
corrected and annotated.
**Verdict:** No code bug — the Sharpe formula is correct and reproduces −24.04 exactly. The real
defects were (1) fabricated numbers in the Task-9 doc, now fixed, and (2) a wrong explanation in
audit round 1, now retracted. Residual note (**D31**): reported `annualized_return` is geometric
while Sharpe is arithmetic-basis, so `ann_return/ann_vol ≠ sharpe` — a real inconsistency, but
NOT the cause of the −24.

### 1B — timestamp on a daily bar  →  WATCH + LIMITATION (D32)
**Ran:** loaded one row; merged a daily bar with two intraday trades.
**Evidence:**
- `2020-01-02` → `bar.ts = 1577923200000000000` = `to_epoch_ns("2020-01-02")` = **midnight UTC**,
  yet the bar carries that day's close (known only at day end).
- `merge([daily], [10:00 trade, 15:00 trade])` emits, in order:
  `BAR(close=100.5)@00:00`, `trade@10:00`, `trade@15:00`. The **daily close is seen before that
  day's intraday trades → future leak.**
- Fills inherit it: `engine.py` sets `Fill.ts = event.ts`, i.e. the bar's midnight, not the
  real open time — so per-trade attribution against intraday prices would be off.
**Verdict:** Invisible with one source; a real leak the moment a second (intraday) source is
added in week 2. Fix options (deferred): stamp bars at close time, or split each bar into
open/close events.

---

## Part 2 — invariants

### Invariant 1 — no strategy sees beyond the clock  →  CLEAN
**Ran:** enumerated everything reachable from what a strategy receives.
**Evidence:**
- `event` (a `Bar`) public attrs: `close, high, low, open, symbol, ts` — all primitives;
  `event.__dict__` is **False** (slots, no arbitrary references).
- `ctx` public attrs: `cash, position, positions, ts`. `ctx.positions` is a **`mappingproxy`**;
  mutating it raises `TypeError`.
- Nothing reachable exposes the queue, data source, engine, or recorder. No mutable default,
  no module-level source import in the strategy path.
**Verdict:** No additional channel found beyond the three the existing test already blocks.

### Invariant 2 — byte-identical output  →  DRIFT (D33), determinism CLEAN
**Ran:** two runs in two subprocesses with different `PYTHONHASHSEED`.
**Evidence:** identical parquet **content** across processes. No set/hash-order dependence in
the hot path; dict iteration is insertion-ordered and deterministic. `verify` compares content
(`DataFrame.equals`), not raw bytes, because correct parquet can differ in incidental metadata.
**Verdict:** Reasoning is sound but the invariant text said "byte-identical." **ARCHITECTURE
invariant 2 corrected** to "identical record content"; recorded as deliberate narrowing (D33).

### Invariant 3 — cash + MTM = equity  →  CLEAN (hardened)
**Ran:** a property test over **300 random fill sequences** (flips, partials-by-value, costs,
flat cycles) asserting `equity == cash + market_value` **and**
`realized + unrealized == equity − initial` after every fill. Plus the single-fee trace.
**Evidence:**
- Property test passes for all 300 seeds (`test_accounting_identities_hold_over_random_fill_sequences`).
- Fee trace: buy `100 @ 11.00` cost `1.10` → cash `100000 → 98898.90` (−1100 −1.10),
  realized `−1.10`, equity@11 `99998.90`. The cost hits **cash once** and **realized once**;
  equity falls by exactly 1.10; `realized + unrealized == equity − 100000` holds. **No double count.**
**Verdict:** Correct.

### Invariant 4 — no fill before arrival_ts  →  CLEAN
**Ran:** final-bar order; negative latency; latency > whole dataset; two same-ts orders.
**Evidence:**
- `latency_ns = 1000·DAY`, 4 bars → fills `[0,0,0,0]`; the order never fills and is **silently
  discarded** at run end (no cancel/reject record).
- Negative latency: fills on same-ts bar **False**, next bar **True** — the `event.ts > submit_ts`
  guard prevents a same-bar fill.
- Two orders, same symbol, same submit ts → both fill on the next bar `[(10,11),(20,11)]`,
  FIFO, quantity conserved.
**Verdict:** Gate holds in every edge. (Silent drop of unfilled orders → see D36.)

### Invariant 5 — total fill qty ≤ order qty  →  CLEAN (was untested)
**Ran:** submit one order, feed two later bars.
**Evidence:** first bar fills `100.0` (the full order qty); second bar `[]` — the order is
removed from the pending queue and cannot refill (`test_order_fills_at_most_once_and_quantity_conserved`).
The naive model fills the **full** qty once; **no partial fills exist yet** (a known simplification).
**Verdict:** Correct; invariant now has a dedicated test.

### Invariant 6 — reproduce from manifest  →  CLEAN + caveat (D34)
**Ran:** identical rerun; input CSV changed after the run; seed-only change.
**Evidence:**
- Identical rerun → `verify` **True**; CSV changed underneath → **False**
  (`test_verify_false_when_input_csv_changes`).
- Seed-only change → output **identical** (week-1 strategies ignore the seed), so `verify`
  cannot detect a seed-only difference *yet*.
- `verify` compares **output only**; it does not check the manifest's git commit / library
  versions against the current environment.
**Verdict:** Works for its purpose. Caveat (D34): it validates output reproducibility, not
environment match; a rerun under a different pandas could still pass.

---

## Part 3 — seam drift
**Ran:** compared code to the nine seams; scanned for deviations beyond the three known.
**Evidence & verdict:**
- Recorder protocol in `core/engine.py` not `runner/` — recorded (**D19**). DRIFT, benign.
- `CostModel.cost` dropped `MarketCtx` — recorded (**D16**). DRIFT, benign.
- Invariant 2 byte→content — now recorded (**D33**) + ARCHITECTURE fixed. DRIFT, benign.
- **New (previously unlisted):** seam 6 lists record kinds `fill, order, position, portfolio,
  signal, reject`; the engine emits only **`fill, order, portfolio`** (`position/signal/reject`
  not emitted). Subset, not conflict — makes future work *easier* (add kinds later). Noted here;
  candidate for a decisions entry if you want it formal.
- **New (minor):** `RunConfig` reorders seam-7 fields and adds defaults (`params={}`,
  `fill_model="naive"`, `cost_bps=0`, `latency_ns=0`). Superset/ergonomic, no capability change.

---

## Part 4 — adversarial "try to break it"

### Buy more cash than you have  →  LIMITATION (D35)
**Ran:** `Book(100k).apply_fill("AAPL", 1_000_000, 10.0)` (10M notional).
**Evidence:** cash → **−9,900,000**; equity stays 100k (position marked at cost). No rejection,
no margin check (`test_no_margin_check_cash_can_go_negative`).

### Unlimited leverage amplifies P&L  →  LIMITATION (sharpest attack, extends D35)
**Ran:** buy 1× vs 10× notional, then move price +1%.
**Evidence:** +1% price → 1× notional equity **101,000**; 10× notional equity **110,000** — a
**10× gain with no borrow cost**. A strategy can lever arbitrarily to inflate returns with no
financing penalty. This is the most effective way to fake performance today.

### Flip / limit / negative-qty exploits  →  CLEAN (no free profit)
**Ran:** flip cycles, limit-order pricing, negative-qty order.
**Evidence:** flips realize PnL only against real average cost (property test = sound; no profit
from flipping at a constant price). Limit orders fill at the **open** (market) price, never at a
better limit price → no price improvement. A negative-qty order just becomes a short
(`pos.qty = −100`) — odd input, no validation, but no fake profit.
**Verdict:** The only real return-inflation vectors are **leverage/margin** (above), not the
accounting or fill logic.

### Loud-failure philosophy  →  LIMITATION (D36)
**Ran:** empty event stream; order on the final bar; never-trading strategy.
**Evidence:** an empty stream completes with **0 records and no error**
(`test_empty_event_stream_completes_without_error`); a final-bar order is dropped silently.
This is the same silent-success that let the Task-8 microsecond bug through.
**Verdict:** Known gap; a future guard could fail loudly on zero events / zero fills.

---

## Part 5 — numbers checked by hand  →  ALL MATCH
Ran each through the real code:
1. Buy 40@10 + 60@10.10 → cash **98,994**, pos **100 @ 10.06**, equity@10.50 **100,044**, unreal **44** ✓
2. Sell 150@12 → realized **+194**, pos **−50 @ 12.00**, cash **100,794**; @11 equity **100,244**, unreal **+50** ✓
3. Buy 50@9 → realized total **344**, flat, equity **100,344** ✓
4. `BpsCostModel(10)` on 100 @ 11.00 = **1.10** ✓
5. Equity `[100,110,99,121]` → total return **+21%**, max drawdown **−10%** ✓
No disagreements; expected values were not adjusted to match code.

---

## Part 6 — interview drill  →  NOT self-run (for you)
This is an interactive exercise for the human to answer aloud; running it against myself would
defeat its purpose. Suggested "three hardest questions" for you, based on the least-commented,
most non-obvious logic:
1. Why does `engine.run` fill *past* orders before calling the strategy, and submit *after* —
   and what exact bias appears if you swap those two steps? (`engine.py` step order)
2. Why is a daily bar stamped at midnight, and when does that become a look-ahead bug? (1B)
3. Why is Sharpe `(mean/std)·√252` rather than `annualized_return/annualized_vol`, given the
   two disagree in your own output? (1A / D31)

---

## Doc numeric-integrity sweep (round 3)
Every numeric example/output across `docs/` was classified: (a) copied from real program
output, (b) hand-computed and verifiable, (c) written from expectation without running.
All (b) values were then **re-executed** to confirm them (raising them to (a)-verified).

**(c) defects found — 2, both corrected (never by adjusting prose to fit):**
1. `learn/task9`: the reversal `tessera report` line — `ann −33.4%, vol 22.1%, hit 42%,
   trades 118` were invented. Real: `ann −31.08%, vol 1.55%, hit 8.5%, trades 7`. Replaced.
2. `learn/task6`: final equity `101,150` was an arithmetic error (98,900 + 100·12.5 =
   **100,150**). Replaced; the three equity values are now tied to a named test assertion.

**Re-executed and confirmed correct (a/b):**
- `task1`: day timestamps `1420070400000000000 / …800… / …243200…` ✓; float ULP 256 near 1.42e18 ✓.
- `task3`: rolling-mean averages 11.0 (day3), 12.0 (day4) ✓.
- `task4`: full ledger — 98,994 / avg 10.06 / equity 100,044 / unreal 44 / realized 194 /
  −50@12 / cash 100,794 / 100,244 / realized 344 / equity 100,344 ✓ (also Part 5).
- `task5`: next-open fill 11.0, latency fill 12.0, bps cost 1.10 ✓.
- `task6`: equity 100,000 → 100,050 → 100,150, unrealized 150 ✓ (now correct).
- `task9`: `[100,110,99,121]` → +21% / −10% ✓; ma_crossover report line reproduces exactly.
- Test counts in PROGRESS/handoff: 58 total, per-file counts match `pytest --collect-only`.

**Borderline (left, flagged):** `task7` shows `timings={"wall_seconds": 0.7}` — an
illustrative *input argument* in an example call, not a claimed measurement.

Per CLAUDE.md rule 8 (added by the user): a numeric result goes in a doc only if copied from
real output or hand-computed-and-correct; otherwise write `[not yet run]`, never a plausible value.

## Bottom line
- **Code bugs: 0.** The Sharpe formula is correct and reproduces −24.04 exactly; the value is
  large only because the synthetic data has 1.55% volatility.
- **Doc/process defects found (round 2): 2, both fixed.** (1) The Task-9 learn doc's reversal
  report line contained fabricated `ann/vol/hit/trades` figures — corrected to the real
  reproduced values. (2) Audit round 1's "geometric-vs-arithmetic" explanation of −24.04 was
  wrong — retracted and corrected here and in `decisions.md` D31.
- **Biggest real code risk:** unlimited leverage / no margin check (D35) — the one true
  return-inflation vector — plus the latent midnight-bar leak (D32) that activates in week 2.
- **Documented:** D31–D36; ARCHITECTURE invariant 2 corrected; 7 regression tests added.
- **Process lesson:** every numeric output line in docs must be pasted from a real run, never
  reconstructed; and an explanation must be checked against a reproduced number, not asserted.
