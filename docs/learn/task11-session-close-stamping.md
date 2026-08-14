# Understanding Task 11: the midnight-bar leak and session-close stamping

A from-scratch explanation, no code required. This task fixes a *latent* look-ahead bug —
one that cannot cause a wrong number today, but would silently corrupt results the day we add
intraday data. The fix is one line of intent ("stamp a daily bar when its data becomes known,
not at the start of the calendar day") with one subtlety worth understanding deeply: daylight
saving time. Read it once, then try the "Answer these yourself" section.

---

## Part 0 — The problem we are actually solving

Every event in this engine carries an integer-nanosecond timestamp, `ts`. The queue merges all
sources into **one stream ordered by `ts`** (Task 2), and a strategy is allowed to see the data
inside an event the moment that event arrives. So `ts` is a *promise*: "everything in this event
was known to the world at this instant." Break that promise and you have look-ahead.

A **daily bar** carries the day's open, high, low, and close. Here is the catch: the high, low,
and close are **not known until the session ends** (16:00 in New York). But the loader was
stamping the bar at `to_epoch_ns("2020-01-02")` = **00:00 UTC** — the very start of that calendar
day in UTC, which is actually *before* the US market even opens. So the bar was labelled with a
time ~21 hours *before* its own close existed. The `ts` promise was a lie by most of a day.

Why did nobody notice? Because with **one daily source** the lie is invisible:

- The fill model fills orders at the *next* bar's open, never the current bar — so a strategy
  reacting to today's close still transacts tomorrow, regardless of the exact stamp.
- Every bar is shifted by the *same* rule, so the relative order of daily bars is unchanged.

The bug only *activates* when you merge the daily bars with an **intraday** source.

> Recite: *`ts` promises "known to the world by now." A midnight-stamped daily bar breaks that
> promise — it's stamped ~21h before its close existed.*

---

## Part 1 — When the latent leak becomes a real one

Imagine merging a daily bar for 2020-01-02 with that same day's intraday trades at 10:00 and
15:00 New York time. Sort everything by `ts`:

| event | old stamp (UTC) |
|-------|-----------------|
| **daily bar** (carries the *close*) | 2020-01-02 **00:00** |
| trade @ 10:00 ET | 2020-01-02 15:00 |
| trade @ 15:00 ET | 2020-01-02 20:00 |

The daily bar sorts **first** — so a strategy would see the whole day's *closing price* before
it saw that day's morning and afternoon trades. That is textbook look-ahead: the future (the
close) delivered before the past (the intraday ticks). This is the finding recorded as **D32**
in the audit — flagged early precisely so it wouldn't be discovered *after* building intraday
features on top of it.

> Recite: *the leak is dormant with one daily source and fires the instant a daily bar is merged
> with intraday data on the same day.*

---

## Part 2 — The fix, and the two options we weighed

There were two honest ways to fix it.

**Option A — move the timestamp to the session close.** One event per bar, stamped at 16:00 ET
instead of 00:00 UTC. Small, confined to the loader.

**Option B — split each bar into two events**: an *open* event at 09:30 ET carrying only the open
(the one price known then), and a *close* event at 16:00 ET carrying the full OHLC. This models
the day's shape correctly and would let a fill land at the true open — but it adds a new event
type (touching a fixed interface), changes the fill model, and doubles the number of portfolio
rows (which would break the "≈252 periods per year" assumption the metrics use to annualize).

We chose **A**, for three reasons:

1. **It is behavior-preserving today.** Every daily bar shifts by the same rule, so relative
   order, next-open fills, equity, and every metric are unchanged — only the `ts` column moves.
   (Verified on the real grid window: re-running AAPL 2005-01-03…2024-12-31 at 0 bps reproduces the
   baseline `ma_crossover` *and* `reversal` rows to machine precision — total return, Sharpe, max
   drawdown, trade count all identical, including reversal's 5016 fills / 455× turnover, the case
   most likely to expose a fill-ordering change.)
2. **It stays in one component** (`data/` + a test), honoring the "one component per session" rule.
   B would have edited the event types, the fill model, *and* the metrics.
3. **B fixes a leak that cannot fire until week-2 intraday data exists.** Building it now is
   speculative scope — the exact failure mode the plan warns about. B is the *right* week-2 move;
   it is the wrong week-1 move.

> Recite: *fix the smallest thing that closes the leak now; defer the fuller model to when the
> data that needs it actually arrives.*

---

## Part 3 — The subtlety that makes A non-trivial: daylight saving

"Stamp it at the close" sounds like "add 21 hours to midnight." **That is wrong twice a year.**

The New York close is **16:00 America/New_York**, a *wall-clock* time. But our timestamps are in
UTC, and the offset between New York and UTC is **not constant**:

- Winter (Eastern Standard Time): 16:00 ET = **21:00 UTC** (offset −5h).
- Summer (Eastern Daylight Time): 16:00 ET = **20:00 UTC** (offset −4h).

A fixed "+21h from midnight" would put summer bars an hour late. Worse, if you ever merged real
UTC-timestamped intraday data, that one-hour error could reorder events across the boundary.

The correct method is to construct the **wall-clock 16:00 in the `America/New_York` timezone** and
let the timezone database convert it to UTC — it knows which dates are EST and which are EDT. In
this codebase that's `pd.Timestamp("2020-01-02 16:00", tz="America/New_York").tz_convert("UTC")`.
The timezone `America/New_York` is stdlib (`zoneinfo`, Python 3.11), so **no new dependency**.

> Recite: *the UTC close moves with DST — 21:00 winter, 20:00 summer — so build the close in the
> market's timezone and convert, never add a fixed offset.*

Two known limitations we deliberately left for week 2 (they become option B's job):

- **Half-day early closes** (13:00 ET, ~13 days/year around holidays) aren't modelled — we don't
  pull in an exchange calendar yet. Those bars are stamped 3h late, which is *harmless* for a
  single daily source (every bar still shifts monotonically) but wrong once intraday data cares.
- **A fill is still stamped at the next bar's *close* instant, not its true *open*** (its *price*
  is the open). That's a ~6.5h-late stamp that never breaks the clock's forward-only rule, but it
  would misplace fills on a real intraday tape. Splitting bars (option B) fixes it.

---

## Part 4 — the reproducibility break the fix caused, and the guard for it

Here is a subtlety that only shows up *because* the engine has a reproducibility mechanism.
`verify()` (Task 7) re-runs a saved run's config and checks it produces identical output. But a
`RunConfig` stores its window as **raw nanosecond boundaries** (`start_ts`/`end_ts`) — integers
computed by `to_epoch_ns` *at the time the run was created*. Change what `to_epoch_ns` means, and
those stored integers now point somewhere else.

Concretely: the Task-10 grid stored `end_ts` = 2024-12-31 **00:00** UTC (the old midnight rule).
Under the new rule the 2024-12-31 bar lands at **21:00** UTC — which is *greater* than the stored
`end_ts` — so re-running silently **drops the final bar**. `verify()` on a pre-Task-11 run returned
`False`: 5032 replayed rows against 5033 stored. The mechanism whose whole job is proving
reproducibility was quietly reporting a failure, and not saying *why*.

The insight: **this convention lives in our own code**, not in the data or the libraries. So the
manifest's data-hash (bytes unchanged) and version list (pandas irrelevant) can't detect it. This
is the exact category the earlier audit finding **D34** predicted — `verify` checks output, not the
environment/semantics a run was made under.

The fix (option **A**, D42): stamp a **`timestamp_convention`** string (`"session_close_v1"`) into
every manifest, and have `verify` **refuse loudly** — raising `ConventionMismatch` that names both
conventions — when a run's stored convention differs from the current code's. A missing field means
a pre-Task-11 run (`"midnight_v0"`), which also trips it. Two things keep the tag honest: it is
defined *once* in the loader and imported everywhere (no second copy to drift), and a **tripwire
test** pins the string to `to_epoch_ns`'s actual output, so changing the mapping without bumping the
string fails a test instead of passing silently.

Crucially, **A detects, it does not cure.** It makes the break loud; it does not make old runs
reproduce (their recorded timestamps are midnight regardless). The *cure* is option **B** — store
calendar *dates* in `RunConfig` instead of raw ns, so a boundary means "that date's session" under
any convention — which is deferred to the next time we touch `RunConfig` or add intraday.

> Recite: *a config that stores convention-dependent integers isn't reproducible across a convention
> change; version the convention so verify fails loudly, and store dates to make it robust.*

## Worked example with synthetic data

One row of CSV: `2020-01-02,10,11,9,10.5,1000` (a winter date), plus two intraday trades on the
same day. Hand-trace the stamps.

- **Load the bar.** Date `2020-01-02`, close `10.5`. The loader builds the wall-clock close
  `2020-01-02 16:00` **in America/New_York**, then converts to UTC. January is EST (−5h), so the
  stamp is **2020-01-02 21:00 UTC**. (A July date would land at 20:00 UTC — EDT, −4h. The loader
  test pins *both* to prove DST is handled.)
- **The two intraday trades:** 10:00 ET → **15:00 UTC**; 15:00 ET → **20:00 UTC**.
- **Merge and sort by `ts`:**

  | order | event | ts (UTC) |
  |------:|-------|----------|
  | 1 | trade @ 10:00 ET | 15:00 |
  | 2 | trade @ 15:00 ET | 20:00 |
  | 3 | **daily bar** (the close) | **21:00** |

  The bar now sorts **last** — after both of that day's trades. A strategy sees the morning tick,
  the afternoon tick, *then* the close. No look-ahead. Under the old midnight stamp the bar sat at
  00:00 and led the whole day. That reordering, from first to last, *is* the fix — and it's exactly
  what `test_daily_bar_does_not_leak_ahead_of_same_day_intraday` asserts.

Notice what did **not** change: with only the daily source, that bar is still just "the 2020-01-02
bar, before the 2020-01-03 bar." Its neighbors are other daily bars, all shifted by the same +21h/
+20h rule, so the single-source backtest is byte-for-byte the same story at a different clock.

## Which file and function did each step

| Step in the story | File | Function / type |
|-------------------|------|-----------------|
| Parse a CSV date → int-ns at the session close | `data/sources/csv_bars.py` | `to_epoch_ns` (scalar), `CsvBarSource.events` (vectorized) |
| Build 16:00 ET in the market tz, convert to UTC | `data/sources/csv_bars.py` | `to_epoch_ns` — `_MARKET_TZ`, `_SESSION_CLOSE` |
| `--start` / `--end` use the same close-stamp rule | `runner/cli.py` | `run_cmd` → `to_epoch_ns(start/end)` |
| Merge sources into one `ts`-ordered stream | `core/queue.py` | `merge` (uses `ordering_key`) |
| Enforce forward-only time on the merged stream | `core/clock.py` | `Clock.advance` |
| Fill still lands at the *next* bar's open | `execution/naive.py` | `NaiveFillModel.on_event` |
| Prove the leak is closed (bar sorts after ticks) | `tests/test_strategies_and_cli.py` | `test_daily_bar_does_not_leak_ahead_of_same_day_intraday` |
| Pin the DST-correct close (winter + summer) | `tests/test_strategies_and_cli.py` | `test_csv_loader_stamps_bars_at_session_close` |
| Single source of truth for the convention tag | `data/sources/csv_bars.py` | `TIMESTAMP_CONVENTION` |
| Record the convention in the manifest | `runner/manifest.py` | `write_manifest` |
| Refuse to reproduce across a convention change | `runner/manifest.py` | `verify`, `ConventionMismatch` |
| Report the mismatch on the CLI (exit 2) | `runner/cli.py` | `verify_cmd` |
| Prove the guard fires + tripwire the tag | `tests/test_runner.py` | `test_verify_reports_convention_mismatch_not_generic_false`, `test_timestamp_convention_pins_loader_behavior` |

---

## Answer these yourself

Cover the text and try these.

1. **Why is a daily bar stamped at midnight a look-ahead bug, and why doesn't it change any
   number in a single-source backtest?** (Part 0/1. The bar carries the close, known only at
   16:00 ET, but was stamped at 00:00 UTC — ~21h early. With one source it's invisible because
   fills are next-open and every bar shifts identically; it only fires when merged with intraday
   data, where the midnight bar sorts ahead of that day's ticks.)

2. **We picked "move the timestamp" over "split each bar into open and close events." Give the
   strongest reason for each side, and why A won for *this* week.** (Part 2. A is behavior-
   preserving and confined to `data/`; B models the day correctly and enables true open fills but
   edits a fixed interface, the fill model, and the metrics. A won because B fixes a leak that
   can't fire until week-2 intraday data exists — building it now is speculative scope.)

3. **Why can't the fix just add 21 hours to midnight?** (Part 3. The UTC offset of the New York
   close changes with daylight saving — 21:00 UTC in winter, 20:00 in summer. A fixed offset is
   wrong half the year. You must construct 16:00 in `America/New_York` and convert to UTC.)

4. **After the fix, is a *fill* stamped at the true open? Is that a problem yet?** (Part 3. No —
   the fill's price is the next open but its stamp is that bar's *close* instant. It never breaks
   the clock's forward-only rule and is invisible with one daily source, so it's a documented
   limitation deferred to option B when intraday data lands.)

5. **The grid re-run reproduced every metric exactly — why is that the *expected* result, and
   what one boundary detail did you have to control for?** (Part 2. Expected because every bar
   shifted by the same rule, so ordering, fills, and marks are identical; only the `ts` column moved.
   The detail: `--start/--end` now bound at the session close, so you must re-run by *date* — reusing
   the baseline's stored old-midnight `end_ts` would move it before the last bar and silently drop
   it, a boundary artifact that has nothing to do with fill ordering.)

6. **Changing `to_epoch_ns` silently broke `verify()` on old runs. Why couldn't the manifest's
   data-hash or version list catch it, and what did we add instead?** (Part 4. The convention lives
   in our own code, not the data bytes or the libraries, so neither hash nor versions see it — the
   D34 category. We added a versioned `timestamp_convention` in the manifest and made `verify` raise
   `ConventionMismatch`; a tripwire test pins the string to the actual mapping so it can't drift.)

7. **Does that guard make the old Task-10 runs reproducible again? If not, what would?** (Part 4. No
   — it only makes the failure *loud and explained*; the old runs' recorded timestamps are midnight
   regardless. The cure is option B: store calendar dates in `RunConfig` so a boundary is
   convention-stable. A detects, B cures.)

If those come out cleanly in your own words, you've got Task 11 cold.

---

## Mini-glossary

- **Look-ahead** — a strategy seeing information before it was knowable in the real world; the one
  thing this engine is built structurally to prevent.
- **Latent bug** — a defect that produces no wrong output under current conditions but will once a
  condition changes (here: adding an intraday source). Flagged as D32, fixed in D41.
- **Session close** — 16:00 America/New_York, the instant a regular-session daily bar's OHLC is
  fully known; the correct timestamp for a daily bar.
- **Daylight saving (EST/EDT)** — New York is UTC−5 in winter, UTC−4 in summer, so 16:00 ET maps
  to 21:00 UTC or 20:00 UTC depending on the date. The reason the fix uses a timezone, not an offset.
- **`zoneinfo`** — the Python 3.11 standard-library timezone database; lets us convert 16:00 ET to
  UTC correctly without adding a dependency.
- **Half-day close** — an early 13:00 ET close on ~13 holiday-adjacent days a year; not modelled
  yet (needs an exchange calendar), so those bars are stamped 3h late — harmless for one daily source.
- **Behavior-preserving change** — an edit that provably leaves all observable outputs identical
  (here every metric), changing only an internal representation (the `ts` values).
- **Timestamp convention** — the rule mapping a calendar date to an integer-ns timestamp (midnight
  vs session close). It lives in *code* (`to_epoch_ns`), so it needs its own version tag; nothing in
  the data or library list captures it.
- **`ConventionMismatch`** — the error `verify` raises when a run's stored convention differs from
  the current code's, so a convention change fails loudly instead of silently reproducing a
  different run. Detection (option A); storing dates in `RunConfig` would be the cure (option B).
