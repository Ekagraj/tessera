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

## Canonical 24-row grid (final code — every README number comes from THIS run)

Rerun on final code (Tasks 0–12 + validation), window 2005-01-03…2024-12-31, `initial_cash`
100k, 10% fixed-fractional sizing, `fast=10,slow=50`. **0 rejects across all 24 runs.**

### ma_crossover — cost-INSENSITIVE (≤0.75pp drag; positive on all six at both cost levels)
| symbol | total@0 | total@5 | drag (pp) | Sharpe@5 | vol | maxDD@5 | turnover | trades |
|--------|--------:|--------:|----------:|---------:|----:|--------:|---------:|-------:|
| AAPL | 53.41% | 52.74% | −0.67 | 0.96 | 2.24% | −4.16% | 10.13x | 127 |
| MSFT | 26.01% | 25.35% | −0.66 | 0.64 | 1.78% | −3.30% | 11.76x | 129 |
| JPM  | 14.41% | 13.76% | −0.65 | 0.28 | 2.39% | −6.25% | 12.45x | 129 |
| XOM  |  6.90% |  6.21% | −0.69 | 0.17 | 1.86% | −5.21% | 13.91x | 138 |
| KO   |  4.02% |  3.27% | −0.75 | 0.14 | 1.23% | −3.80% | 14.75x | 150 |
| NVDA | 82.94% | 82.24% | −0.70 | 0.79 | 3.91% | −10.13% | 10.63x | 132 |

### reversal — cost-SENSITIVE (~25–26pp drag; 3 of 6 flip NEGATIVE at 5 bps)
| symbol | total@0 | total@5 | drag (pp) | Sharpe@0 | Sharpe@5 | turnover@5 | trades |
|--------|--------:|--------:|----------:|---------:|---------:|-----------:|-------:|
| AAPL | 21.91% | **−3.47%** | −25.38 | 0.35 | −0.04 | 514.61x | 5016 |
| MSFT | 24.36% | **−1.53%** | −25.89 | 0.45 | −0.01 | 546.89x | 4980 |
| JPM  | 34.06% |  7.96% | −26.10 | 0.51 | 0.14 | 448.01x | 5005 |
| XOM  | 31.14% |  5.53% | −25.61 | 0.62 | 0.12 | 477.00x | 5010 |
| KO   |  8.35% | **−16.90%** | −25.25 | 0.23 | −0.44 | 537.49x | 4973 |
| NVDA | 54.89% | 29.00% | −25.89 | 0.56 | 0.31 | 473.27x | 4994 |

**The thesis in one line:** the same nominal edge survives or dies on `turnover × cost`.
ma_crossover trades ~130x and loses ~0.7pp to 5 bps; reversal trades ~5,000x and loses ~25pp,
flipping AAPL/MSFT/KO negative. Returns are on a 10%-of-capital sleeve, so absolute levels are
small by design — the comparison, not the level, is the point.

## Fabrication audit (rule 8) — result numbers re-verified on final code
Re-derived every result-figure staged for the README against fresh computation: turnover
decomposition (5,016 fills, $50.76M, 455.94x, 22.8x/yr), underlying annualized vols (KO 18.1% …
NVDA 48.6%, so "18–48%" is exact), AAPL sizing table (0.095% → 2.4% → 18.4%). **All match.** The
only fabrication ever found — the Task-9 `vol 22.1%` report line — was already caught and fixed in
the audit (AUDIT_FINDINGS.md). Historical pre-fix-code figures in `learn/task10` (e.g. old 100-share
portfolio vol 0.48–3.75%) describe superseded behavior and were not re-derived (would need an old
checkout); they are labeled as historical, not current results.
