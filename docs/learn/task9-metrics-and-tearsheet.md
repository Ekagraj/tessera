# Understanding Task 9: metrics and the tearsheet

A from-scratch explanation, no code required. This task turns a folder of raw records
into the numbers and the picture you'd actually show someone. Read it once, then try the
"Answer these yourself" section.

---

## Part 0 — The problem we are actually solving

The engine wrote a pile of records to disk (fills, orders, a portfolio snapshot each
day). That's the *raw material*, but nobody reads raw parquet to judge a strategy. They
ask: *did it make money? how bumpy was the ride? was the return worth the risk?*

Task 9 answers those by computing **performance metrics** and drawing a one-page
**tearsheet** — and it does all of it **after the fact, from the saved records**, never
from inside the engine.

That "after the fact" part is a real architectural choice, not an accident (see Part 5).

---

## Part 1 — The equity curve and returns

Everything starts from one column the engine saved: **equity** (total account value)
once per day. Line those up over time and you have the **equity curve** — the single most
important picture of a strategy.

From equity you get **returns**: the percentage change from one day to the next. A jump
from 100 to 101 is a +1% day. The whole list of daily percentage changes is the
`returns` series, and almost every metric is built from it.

---

## Part 2 — The headline metrics, in plain words

- **Total return** — how much you made end to end: final equity ÷ starting equity − 1.
- **Annualized return** — that total, expressed as a per-year rate, so runs of different
  lengths are comparable.
- **Volatility** — how *bumpy* the returns are (their standard deviation). High
  volatility = a wild ride.
- **Sharpe ratio** — return *per unit of* bumpiness: average return ÷ volatility. It
  answers "was the return worth the risk?" Higher is better; below ~1 is weak.
- **Max drawdown** — the worst peak-to-trough fall along the way. If you hit 100, sank to
  85, then recovered, your max drawdown was −15%. It measures pain, not just outcome.
- **Turnover** — how much trading you did relative to your account size. High turnover
  means costs matter a lot.
- **Hit rate** — the fraction of days that were positive.
- **Average win / average loss** — the typical up move vs the typical down move.

> Recite: *total & annualized return = how much; volatility = how bumpy; Sharpe = return
> per unit of bump; max drawdown = worst fall; turnover = how much you traded.*

---

## Part 3 — The one with a tricky assumption: annualizing Sharpe

This is the interview question. We measure returns **daily**, but people quote Sharpe
**per year**. How do you convert?

You compute the Sharpe from daily numbers (average daily return ÷ daily volatility), then
multiply by **√252** — the square root of the number of trading days in a year.

Why √252 and not 252? Because returns *add* over time but volatility grows with the
*square root* of time (that's how randomness accumulates). Return scales by 252, risk
scales by √252, so the ratio scales by 252/√252 = √252.

**The assumption baked in:** that daily returns are **independent and identically
distributed** — each day a fresh, unrelated draw — and that the **risk-free rate is
zero**. Real returns are often *autocorrelated* (a trend persists, or a bounce reverses),
and when they are, √252 **overstates** the annualized Sharpe. It's a standard, useful
approximation — but you should know it's an approximation.

> Recite: *annualize by multiplying the daily Sharpe by √252, because return scales with
> time and risk scales with √time. It assumes returns are independent, identically
> distributed, and a zero risk-free rate — √252 flatters autocorrelated returns.*

---

## Part 4 — The tearsheet: four panels

The tearsheet is one image with four panels that together tell the story:

1. **Equity curve** — did the account grow or shrink over time?
2. **Underwater plot** — the drawdown over time, shaded below zero. Shows *how deep* and
   *how long* the painful stretches were.
3. **Rolling Sharpe** — the risk-adjusted return computed over a moving 60-day window, so
   you can see whether the edge was steady or came in bursts.
4. **Return distribution** — a histogram of the daily returns: are they tight around zero,
   fat-tailed, skewed?

It's rendered "headless" (no screen needed) straight to a PNG, so it works on a server or
in a script. And the command that builds it loads the plotting library *only when asked*,
so a plain `tessera run` stays fast.

---

## Part 5 — Why compute metrics from records, not in the engine?

This is the deeper point (seam 8). The engine could have computed Sharpe as it went — but
it deliberately doesn't. Metrics live in a separate layer that reads the saved records.
Three payoffs:

1. **New metrics don't touch the engine.** Want deflated Sharpe, or per-trade attribution,
   or a new plot? It's a new function over the same records — the engine never changes.
2. **Old runs can be re-measured.** Because the records are on disk, you can compute a
   brand-new metric on a run from six months ago without re-running it.
3. **The engine stays lean and single-purpose.** Its one job is producing correct records;
   analysis is somebody else's job.

> Recite: *metrics read the saved records, so new metrics are new functions (no engine
> change), old runs can be re-measured, and the engine stays lean.*

---

## Part 6 — What actually got built in Task 9

- **`tessera/metrics/returns.py`** — loads a run directory's parquet and computes the
  equity curve, returns, drawdown, rolling Sharpe, and the headline metrics dictionary.
- **`tessera/metrics/tearsheet.py`** — `render` draws the four-panel PNG.
- **`tessera report <run_dir>`** — a CLI command that prints the metrics line and writes
  the tearsheet.
- **`tests/test_metrics.py`** — known-value checks (total return, max drawdown, turnover)
  and that the tearsheet actually writes a non-empty PNG.

---

## Worked example with synthetic data

Take a tiny hand-made equity curve and read the metrics off it:

```
equity:  100 -> 110 -> 99 -> 121   (four daily snapshots)

returns:      +10%    -10%   +22.2%
total return  = 121/100 - 1               = +21%
peak so far   = 100,  110,  110,  121
drawdown      =   0%,   0%,  -10%,   0%    (99 is 10% below the peak of 110)
max drawdown  = -10%
```

So from four numbers we get: made 21% overall, but suffered a 10% peak-to-trough dip along
the way. Run it through the real code and `tessera report` prints the same, plus the
annualized figures, Sharpe, turnover, and a PNG:

```
$ tessera report runs/reversal-AAPL-...
total -16.12%  ann -31.08%  vol 1.55%  Sharpe -24.04  maxDD -16.13%  turnover 1.58x  hit 8.5% trades 7
runs/reversal-AAPL-.../tearsheet.png
```

(That reversal run *lost* money — a steady bleed from trading costs, exactly the
transaction-cost story week 1 is meant to demonstrate.)

**Read the Sharpe of −24.04 with care — it is a data artifact, not a good/bad signal.**
It looks absurd because the *synthetic* `data/AAPL.csv` used here is a smooth sine wave,
so its annualized volatility is only **1.55%**. Sharpe = mean ÷ std × √252, and dividing
a small negative mean by a *tiny* std gives a huge magnitude. On realistic market data
(volatility ~15–25%), the identical formula produces sane Sharpes in the −3…+3 range.
The formula is correct; the toy data is not representative. (This exact point was
verified during the week-1 audit — see `docs/AUDIT_FINDINGS.md` §1A.)

### Which file and function did each step

| Step above | File | Function / type |
|---|---|---|
| equity over time | `tessera/metrics/returns.py` | `equity_curve` |
| daily percentage changes | `tessera/metrics/returns.py` | `period_returns` |
| drawdown from the running peak | `tessera/metrics/returns.py` | `drawdown_series` |
| all headline numbers | `tessera/metrics/returns.py` | `compute_metrics` |
| rolling 60-day Sharpe | `tessera/metrics/returns.py` | `rolling_sharpe` |
| the 4-panel PNG | `tessera/metrics/tearsheet.py` | `render` |
| the CLI command | `tessera/runner/cli.py` | `report_cmd` |
| known-value + PNG tests | `tests/test_metrics.py` | `test_total_return_and_max_drawdown_known_values`, `test_tearsheet_writes_a_png` |

---

## Answer these yourself

Cover the text and try these.

1. **How do you annualize a Sharpe ratio from daily returns, and what assumption does that
   make?** (Part 3. Multiply the daily Sharpe by √252; assumes independent, identically
   distributed daily returns and a zero risk-free rate — √252 overstates it when returns
   are autocorrelated.)

2. **Why compute metrics from records rather than inside the engine?** (Part 5. New
   metrics are new functions over the same records, old runs can be re-measured without
   re-running, and the engine stays lean.)

3. **What is max drawdown, and why report it alongside total return?** (Part 2. The worst
   peak-to-trough fall; it measures the pain/risk of the path, which total return alone
   hides.)

4. **What do the four tearsheet panels each tell you?** (Part 4. Equity = growth;
   underwater = depth/length of losses; rolling Sharpe = steadiness of the edge;
   histogram = shape of daily returns.)

If those come out cleanly in your own words, you've got Task 9 cold.

---

## Mini-glossary

- **Equity curve** — account value over time.
- **Return** — percentage change from one period to the next.
- **Volatility** — standard deviation of returns; how bumpy they are.
- **Sharpe ratio** — return per unit of volatility (risk-adjusted return).
- **Annualize** — rescale a per-day figure to a per-year figure.
- **Drawdown** — how far below the running peak you are, as a percentage.
- **Max drawdown** — the worst drawdown over the whole run.
- **Turnover** — traded notional relative to account size.
- **Hit rate** — fraction of positive periods.
- **Tearsheet** — a one-page figure summarizing a run's performance.
- **Headless / Agg** — rendering an image without a screen.
