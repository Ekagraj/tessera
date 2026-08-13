# README notes — staging for Task 10 Part 3

The README (Task 10 Part 3) is deferred until **after Tasks 11 and 12**, so its results
section can reflect whatever those add. This file accumulates confirmed, ready-to-use
material so nothing is lost in the meantime. Every line here is backed by actual program
output (CLAUDE.md rule 8); do not paste anything unverified into the README.

## Data provenance / integrity (from Task 10 Part 1)

- Six liquid tickers, Stooq US daily bars, split- and dividend-adjusted, full history
  2005-01-03 … 2026-08-11 (5,435 bars each, 251.6 bars/yr, 0 duplicates).

- **Adjustment verified (one line for the README):**
  > Adjustment verified: NVDA underwent 4-for-1 (2021) and 10-for-1 (2024) splits within
  > the sample; neither appears as a price move, and no single-day return across the six
  > series falls near a split signature.

  Backing evidence (decisions D37/D39): the corporate-action gate flagged **0** days within
  ±2pp of any forward-split ratio (−50/−75/−80/−90%) or above +90% (reverse split), across
  all six symbols; the most extreme single-day move anywhere is NVDA −30.70% on 2008-07-03
  (a genuine event: the July 2008 defective-GPU charge + margin warning).

## Reading turnover (from Task 10 Part 2)

- **Line for the README, so 455x isn't misread as alarming:**
  > Reversal's turnover of ~455x is over the full 20-year sample — about **23x per year**
  > (the account value traded ~23 times annually; across the six symbols, 20–27x/year). That
  > is an ordinary figure for a daily mean-reversion strategy, not a red flag. Turnover is an
  > *exposure* measure, not a cost proxy: 99.3% of that notional is position flips (each ≈2×
  > the target notional), while the daily constant-notional rebalancing that doubles the
  > *trade count* is only ~0.7% of notional — and cost is charged on notional, not trades.

  Backing evidence (decision D40): AAPL reversal @0bps, $50.76M total traded notional over
  5,016 fills — flips 99.26% (mean $20,035), same-direction rebalances 0.72% (mean $146),
  open 0.02%. Annualized turnover = 455.94x / 20.0 yr = 22.8x/yr.
