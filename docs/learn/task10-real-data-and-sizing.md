# Understanding Task 10 (Parts 1–2): real data, running it, and position sizing

A from-scratch explanation, no code required. This task swaps synthetic data for real
market data, proves the data is real *before* trusting it, runs both strategies across six
symbols with and without costs, and then fixes a sizing bug the real prices exposed. Read it
once, then try the "Answer these yourself" section.

(Part 3 — the README write-up — is deliberately deferred until after Tasks 11 and 12, so it
reflects whatever those add. This guide covers Parts 1 and 2.)

---

## Part 0 — The problem we are actually solving

Up to Task 9 the engine ran on a hand-made `data/AAPL.csv` — a smooth synthetic sine wave.
That was fine for wiring the pipeline, but it lies about the world: its daily *mean loss was
larger than its daily standard deviation*, which no real market does. So every result so far
measured the plumbing, not a strategy.

Task 10 replaces it with **real, verified daily bars** for six liquid tickers (AAPL, MSFT,
JPM, XOM, KO, NVDA), 2005–2024, then runs the two example strategies over them. Two things
turned out to matter more than the runs themselves:

1. **Proving the data is real before running anything on it** (Part 1).
2. **Discovering that fixed-share position sizing made every result a measure of price level,
   not strategy** — and fixing it (Part 2).

---

## Part 1 — How do you know the data is real?

You never trust a downloaded CSV blindly. You compute a small **validation table** — one row
per symbol — and apply **gates** that a real series must pass and a broken/synthetic one fails:

- **Annualized volatility in [10%, 60%].** Real large-cap stocks live here. Outside it means
  the data is wrong (mis-parsed, mis-scaled), not that the stock is exotic.
- **mean|daily return| / daily std well below 1.** This is the tell that caught the old
  synthetic file: a real market's average *move* is a fraction of its *spread* (ours: ~0.68).
  A deterministic drift series has this ratio ≥ 1.
- **~252 rows per year.** Ten years ≈ 2,500 bars; far fewer means the date range or parsing
  is broken. (We widened to 2005–2026, so we check the *density* ~252/yr, not an absolute count.)
- **No single-day move > 30% unless it maps to a named event.** One did: NVDA **−30.70% on
  2008-07-03** — the July 2008 defective-GPU-charge warning. A real event, so it passes.

> Recite: *validate before you run. Vol band, mean/std ≪ 1, ~252 rows/yr, no unexplained 30% day.*

### The adjustment gate (the sharp one)

Stooq's US series are **split- and dividend-adjusted**: a 2:1 split is folded back into
history so it is *not* a −50% day, and dividends are added back as the return you actually
earned. Adjusted prices are the right default because a backtest should measure the return an
investor really got holding the position — not fake gaps at every split/ex-dividend date that
a momentum or reversal strategy would trade on as if they were real.

How do you *prove* the adjustment is intact rather than trusting the vendor? A **corporate-
action gate**: flag any single-day return within ±2pp of a split ratio (−50/−75/−80/−90%) or
above +90% (reverse split). On adjusted data none should appear. Result: **0 flagged days**,
even though NVDA split 4:1 (2021) and 10:1 (2024) inside the sample — unadjusted, that 10:1
would be an unmissable −90% day. Its absence *is* the proof.

> Recite: *the split that isn't there is the evidence the data is adjusted.*

---

## Part 2 — The sizing bug the real prices exposed

Both example strategies originally traded a **fixed 100 shares**. On synthetic data around
$100 that was invisible. On real *adjusted* prices it breaks everything, because adjusted
history spans a huge price range: AAPL is ~$0.95 in 2005 and ~$250 by 2024.

100 shares is therefore a wildly different *bet* over time:

| year | AAPL close | 100-share notional | % of $100k account |
|-----:|-----------:|-------------------:|-------------------:|
| 2005 | 0.95 | $95 | **0.095%** |
| 2015 | 24.21 | $2,421 | 2.4% |
| 2024 | 183.73 | $18,373 | **18.4%** |

Three symptoms proved the table was measuring *price*, not *strategy*:

- **Idle account:** portfolio volatility was 0.48–3.75% while the underlying stocks carried
  18–48% — the position was a rounding error against the cash.
- **Turnover driven by price, not behaviour:** across ma_crossover's six symbols the trade
  *counts* were nearly identical (127–150) but turnover ranged 1.48×–13.48× — a 9× spread that
  can only come from price level.
- **Back-loaded PnL:** only 12% of AAPL's PnL came before 2015, and the 2008 crisis we widened
  the window to capture contributed −0.34% — because in 2008 the position was ~0.1% of the account.

### The fix: fixed-fractional notional

Instead of a share count, target a **dollar notional** = `target_frac × initial_cash` (default
10%), converted to shares at the price the event already carries: **`qty = notional / close`**.
Now every symbol takes the same-sized bet regardless of price, and the bet is stable over time.

After the fix: portfolio vol tracks *underlying risk* (NVDA highest, KO lowest); the
ma_crossover turnover spread collapses 9.1× → 1.45×; AAPL's pre-2015 PnL share rises 12% → 64%.
The comparison finally reflects the signal.

Two design points worth remembering:
- **The strategy needs to know starting capital.** `Context` deliberately excludes equity
  (Task 3), so we don't put it there. Instead the runner *injects* `initial_cash` (a static
  config value, not market data — no look-ahead) into the strategy at construction; `target_frac`
  rides in `RunConfig.params` and the manifest.
- **The reversal flip is the danger zone.** Reversal orders the **delta** to its target
  (`delta = target − held`), so an up day after a long flips long→short in a *single* order that
  closes the long *and* opens the short — the exact zero-crossing `Book.apply_fill` splits (Task 4).

---

## Part 3 — Turnover is exposure, not cost

The fix made reversal *rebalance daily* (its constant-dollar target drifts with price), which
doubled its trade *count*. It's tempting to say "more trades → more cost." That is wrong, and
the numbers say why. Cost is `bps × notional`, so what matters is **notional**, not trade count.

Decomposing AAPL reversal's $50.76M of traded notional:

| kind | fills | % of count | % of **notional** | mean/fill |
|------|------:|-----------:|------------------:|----------:|
| flip (crosses zero) | 2,515 | 50.1% | **99.26%** | $20,035 (≈ 2× target) |
| same-direction rebalance | 2,500 | 49.8% | **0.72%** | $146 (≈ target × daily return) |

A flip trades ~2× the target notional; a rebalance trades ~target × the *daily return* (~1%).
So the rebalancing that doubles the trade count is **0.7% of cost** — negligible. And the
turnover figure itself (455× over 20 years = **~23×/year**) is a perfectly ordinary *exposure*
measure for a daily strategy, not a red flag. It just isn't a proxy for cost.

> Recite: *cost follows notional, not trade count; turnover measures exposure, not cost.*

---

## Worked example with synthetic data

Take a tiny reversal run, `target_frac = 0.10`, `initial_cash = 100_000` → notional = **$10,000**.
Closes: **10, 9, 11, 10.9** (day 0…3). Hand-trace the orders:

- **Day 0 (close 10):** no previous close → no order. Position 0.
- **Day 1 (close 9):** 9 < 10, a *down* day → target **long** `10000/9 = 1111.11` shares.
  Held 0, so `delta = +1111.11`. **Buy 1111.11.** Notional ≈ `1111.11 × 9 = $10,000` (an open).
- **Day 2 (close 11):** 11 > 9, an *up* day → target **short** `10000/11 = −909.09` shares.
  Held +1111.11, so `delta = −909.09 − 1111.11 = −2020.20`. **Sell 2020.20** — this single order
  **closes the long (1111.11) and opens the short (909.09)**: a flip through zero. Notional ≈
  `2020.20 × 11 = $22,222` ≈ **2× target**. This is the zero-crossing Task 4 splits.
- **Day 3 (close 10.9):** 10.9 < 11, a *down* day → target **long** `10000/10.9 = 917.43`.
  Held −909.09, so `delta = 917.43 − (−909.09) = 1826.52` — another flip (short→long).

Now imagine day 3 had instead been another *up* day at 10.8 (same direction as day 2's short):
target `−10000/10.8 = −925.93`, held −909.09, `delta = −925.93 − (−909.09) = −16.84` shares.
Notional ≈ `16.84 × 10.8 = $182` — a **same-direction rebalance**, ~target × the day's return.
Two orders, same trade count, *wildly* different notional ($22,222 vs $182). That is exactly why
trade count is a bad cost proxy and notional is the right one.

## Which file and function did each step

| Step in the story | File | Function / type |
|-------------------|------|-----------------|
| Parse a CSV date → int-ns `Bar` | `data/sources/csv_bars.py` | `CsvBarSource.events`, `to_epoch_ns` |
| Validation table + gates (Part 1) | *(Task-10 validation script)* | reads bars from `CsvBarSource` |
| Fixed-fractional sizing `qty = notional/close` | `strategy/examples/ma_crossover.py` | `MaCrossover.__init__` / `on_event` |
| Reversal delta-to-target (the flip) | `strategy/examples/reversal.py` | `Reversal.on_event` |
| Inject `initial_cash` into the strategy | `runner/cli.py` | `_make_strategy`, `run_from_config` |
| A market order → fill at next open | `execution/naive.py` | `NaiveFillModel.on_event` |
| Cost = bps × notional | `execution/costs.py` | `BpsCostModel.cost` |
| Zero-crossing flip splits close+open | `portfolio/book.py` | `Book.apply_fill` |
| Turnover = notional ÷ mean equity | `metrics/returns.py` | `compute_metrics` |
| Run via the real CLI | `runner/cli.py` | `run_cmd`, `report_cmd` |

---

## Answer these yourself

Cover the text and try these.

1. **The old synthetic file passed a naïve eyeball check. Which single gate catches it, and
   why?** (Part 1. mean|r|/std ≪ 1 — the synthetic series had mean loss > std, the signature of
   a deterministic drift, not a market.)

2. **How do you prove the prices are split-adjusted rather than just trusting the vendor?**
   (Part 1. The corporate-action gate: NVDA's 10:1 (2024) and 4:1 (2021) splits produce *no*
   single-day move near a split ratio — 0 flags across all six. The absent −90% day is the proof.)

3. **Why did fixed 100-share sizing make the whole Part-2 table meaningless on adjusted data?**
   (Part 2. On adjusted prices spanning $0.95→$250, 100 shares is 0.1% of the account in 2005 and
   18% in 2024, so results measured price level and time, not the strategy.)

4. **Where does the strategy get `initial_cash` from, and why isn't that look-ahead?** (Part 2.
   The runner injects it at construction; it's a static config value, not market data, so it
   reveals nothing about the future. `Context` still carries no equity.)

5. **Reversal trades ~5,000 times with 455× turnover. Is that a cost alarm?** (Part 3. No —
   turnover is ~23×/year exposure; 99.3% of notional is flips, and the daily rebalancing that
   doubles the trade count is 0.7% of notional, hence of cost.)

If those come out cleanly in your own words, you've got Task 10 Parts 1–2 cold.

---

## Mini-glossary

- **Bar** — one day's open/high/low/close/volume, stamped at an integer-ns timestamp.
- **Split/dividend adjustment** — folding corporate actions back into history so returns reflect
  what an investor actually earned; a split becomes a non-event, not a −50% day.
- **Validation gate** — a pass/fail check on the data (vol band, mean/std, row density, split gate)
  run *before* any strategy touches it.
- **Notional** — the dollar value of a trade or position (`shares × price`).
- **Fixed-fractional sizing** — target a fixed *fraction of capital* as notional, converted to
  shares at the current price, so exposure is comparable across symbols and stable over time.
- **Flip / zero-crossing** — an order that takes a position from long to short (or vice versa) in
  one shot, split by the book into a close and an open.
- **Rebalance** — a same-direction order that only re-sizes an existing position; tiny notional.
- **Turnover** — traded notional relative to account size; an *exposure* measure, not a cost proxy.
