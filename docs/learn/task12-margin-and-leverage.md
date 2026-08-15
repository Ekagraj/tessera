# Understanding Task 12: the margin / leverage check

A from-scratch explanation, no code required. This task closes the one bug in the audit that
actually let you *fake returns*: with no buying-power check, a strategy could lever up without
limit and multiply its gains at no cost. The fix is small, but three sub-questions each have a
subtle wrong answer, so it's worth understanding properly. Read it once, then try the "Answer
these yourself" section.

---

## Part 0 — The problem we are actually solving

Until now `Book.apply_fill` did `self.cash -= qty * price` with no floor. So a strategy on a
$100,000 account could submit an order for **1,000,000 shares of a $10 stock** — $10,000,000 of
notional — and the book would happily apply it, taking cash to **−$9,900,000**. Nothing rejected it.

Why that matters: it isn't just untidy bookkeeping, it's a way to **fake performance**. The audit
ran it: buy 1× notional vs 10× notional, then move the price +1%.

| exposure | equity after +1% |
|----------|------------------|
| 1× ($100k) | $101,000 |
| 10× ($1M) | $110,000 |

A **10× gain on the same price move, with no borrowing cost.** A strategy can lever arbitrarily to
inflate its returns, and pay nothing for the privilege. This was the audit's single real
return-inflation vector (finding D35). Task 12 puts a limit on it.

> Recite: *no buying-power check means unlimited leverage, and unlimited leverage is free fake
> returns.*

---

## Part 1 — Where should the check live?

Three places could hold the rule. Two are traps.

- **In the fill model?** Tempting — it's "execution." But the fill model's job is *market
  microstructure*: would this order fill, and at what price? It has no idea what's in your account
  (it only sees the event and its own pending queue). To check affordability there, you'd have to
  hand it cash, positions, and mark prices — a change to the `FillModel` interface (a fixed seam) —
  and you'd be mixing "what the market does" with "what your account can afford." Wrong layer.
- **Inside `Book.apply_fill`?** The book owns cash, so it *knows* affordability. But `apply_fill`
  is a pure state mutation that returns realized PnL; making it *reject* means growing it a new
  return channel (or raising), and the book would need mark prices pulled into it (marks live in
  `accounting.py`, deliberately separate). And the book can't emit the `reject` record — recording
  is the engine's job, not the book's. So the book would have to signal refusal upward anyway.
- **In the engine, between fill and apply (chosen).** The engine already owns the sequence
  *produce fill → apply to book → record*. So it asks a **pure predicate** — `admits_fill(...)` in
  `accounting.py` — "would applying this fill stay within the leverage cap?" If yes: apply and
  record a `fill`. If no: record a `reject` and skip. `apply_fill` stays pure, the fill model is
  untouched, and the reject record is emitted exactly where records are emitted.

> Recite: *microstructure is the fill model's job; affordability is the account's; recording is the
> engine's — so the engine asks a pure account-level predicate and records the outcome.*

---

## Part 2 — What happens to a rejected order?

The architecture (seam 6) has always listed a `reject` record kind — and the engine had **never
emitted one**. This is its first use. So a rejected order is *recorded*, not silently swallowed;
you get an `order → reject` trail for that tag.

The real choice is **drop the whole order** vs **partially fill it to the affordable size**.
Partial-fill sounds friendlier, but it silently hands the strategy a *different quantity than it
asked for*, and it wrecks the reversal strategy's flip (a flip that can only half-afford leaves a
weird residual position). That's broker-realism scope creep for week 1. So: **drop the whole order,
record a `reject`.** Honest and simple; partial fills can come later if ever.

> Recite: *reject the whole order and record it (seam 6's `reject`, used for the first time); don't
> silently resize it.*

One trap to close: a clean run has *no* rejections, so it writes no `reject.parquet` at all — which
looks identical to a run where reject-recording silently broke. Absence is ambiguous. So the
**manifest records a per-kind count** (`record_counts`, with `reject` always present), turning "0
rejections" into an affirmative statement in the run's provenance rather than something you infer
from a missing file. (The count affirms what the recorder *saw*; the attack test in Part 0 is what
proves the engine actually *rejects* when it should.)

---

## Part 3 — The rule, exactly (and why shorting needs care)

The naïve rule is "cash can't go negative." **It's not enough**, because of shorting.

A **short sells shares you don't own**, so it *generates* cash rather than spending it. Short
$10M of notional and your cash goes *up* $10M while you hold a −$10M position. A cash-non-negative
rule never triggers — yet you're just as levered as the 10× buyer. So a cash floor fixes the long
exploit and leaves the identical short exploit wide open.

The rule that catches both is **gross exposure**:

```
gross exposure  G = Σ |qty × mark|      (add up the absolute value of every position)
equity          E = cash + Σ qty × mark
admit a fill iff  G_after ≤ max_leverage × E     (default max_leverage = 1.0)
```

Both the $10M long and the $10M short produce `G = 10M` on `E = 100k`, and `10M ≤ 1 × 100k` is
false, so both are rejected. A 10%-of-account position has `G = 10k ≤ 100k` and sails through.

Two details that matter:

- **Marks are look-ahead-safe.** We mark the traded symbol at its *fill price* (known now) and every
  other position at its *last observed close* — never today's close, which isn't known yet. In the
  engine, the margin check runs at step 2 (fills), *before* step 4 updates the price map, so it
  literally cannot see the current bar's close.
- **The de-risking carve-out.** A fill is *also* admitted if it **doesn't increase** gross exposure.
  Why: no fill can ever *create* an over-limit state (the check forbids it), but **mark-to-market
  drift can** — a short that moves against you loses equity faster than it sheds exposure, pushing
  `G/E` above the cap. From that state you must be able to *reduce* (buy back, close), or the
  account **locks up and can never trade again** — a worse failure than the leverage bug. So
  reducing exposure is always allowed; only *increasing* it past the cap is rejected.

> Recite: *cap gross exposure, not cash, so shorts are covered; mark without look-ahead; and always
> let a drifted-over account de-risk.*

---

## Part 4 — Where the limit is stored (and a trigger we didn't trip)

The cap could live in `RunConfig` (so the manifest records it). But **editing `RunConfig` is the
scheduled trigger for Task 11's option B** (store dates, D42) — and we don't want to drag that in.
So the limit is a **`Book` field defaulting to `1.0`**, not yet wired to config. It's universal and
code-versioned, so reproducibility holds; making it tunable is a small follow-up to pair with option
B when we next open `RunConfig`. This kept Task 12 inside `portfolio/` + `core/` — one component.

> Recite: *the cap is a Book default (1×), not a config field yet — deliberately, to avoid opening
> RunConfig before we mean to.*

---

## Worked example with synthetic data

$100,000 account, `max_leverage = 1.0`, one stock at $10.

- **Attempt buy 1,000,000 shares** ($10M). Hypothetically: cash → 100k − 10M = −9.9M; position
  +1,000,000 marked at 10 = +10M. `E = −9.9M + 10M = 100k`. `G = |10M| = 10M`. Is `10M ≤ 1×100k`?
  **No → reject.** No fill applied; equity stays 100k.
- **Attempt short 1,000,000 shares.** cash → 100k + 10M = 10.1M; position −1,000,000 = −10M.
  `E = 10.1M − 10M = 100k`. `G = |−10M| = 10M`. `10M ≤ 100k`? **No → reject.** Same equity.
- **Buy 1,000 shares** ($10k, a 10% position). cash → 90k; position +10k. `E = 100k`, `G = 10k`.
  `10k ≤ 100k`? **Yes → admit.**

Now the subtle one — **an over-limit account from drift.** Start short 1,000 @ $100 (cash 200k,
position −1,000). The price drifts to **$150** (the short is losing): `E = 200k − 150k = 50k`,
`G = 150k` → leverage **3×**, over the 1× cap (no fill created this — the *price move* did).

- **Reduce (buy back 500):** post-fill position −500, `G` falls 150k → 75k. It *decreases* gross, so
  it's **admitted** despite the account being over the cap. Good — you can de-risk.
- **Increase (short 500 more):** position −1,500, `G` rises 150k → 225k, and `225k ≤ 1 × 50k` is
  false. **Rejected.** You can't dig the hole deeper.

That pair — reduce-allowed, increase-rejected, from an over-limit state — is the carve-out, and it's
tested in both directions.

## Which file and function did each step

| Step in the story | File | Function / type |
|-------------------|------|-----------------|
| Store the leverage cap on the account | `portfolio/book.py` | `Book.max_leverage` |
| Decide if a fill is admissible (pure) | `portfolio/accounting.py` | `admits_fill` |
| Mark equity / gross exposure for the check | `portfolio/accounting.py` | `admits_fill` (inline), `equity`, `market_value` |
| Reject-or-apply each fill, emit the record | `core/engine.py` | `run` (the fill loop) |
| Write the `reject` record to `reject.parquet` | `runner/recorder.py` | `ParquetRecorder` (generic per-kind) |
| The attack it defends against | `tests/test_audit.py` | `test_leverage_attack_is_rejected_long_and_short` |
| The de-risk carve-out, both directions | `tests/test_accounting.py` | `test_derisk_carveout_from_an_over_limit_state` |

---

## Answer these yourself

Cover the text and try these.

1. **Why is unlimited leverage the audit's most dangerous finding — worse than a fill or accounting
   bug?** (Part 0. It directly inflates returns: 10× exposure → 10× the gain on a move, at no
   financing cost. It's a way to fake performance, not just a bookkeeping slip.)

2. **Why not put the check in the fill model, where "execution" seems to belong?** (Part 1. The
   fill model models the market, not the account — it can't see cash/positions, and giving it that
   would change a fixed seam and conflate microstructure with financing. Affordability is an
   account-level question; the engine asks a pure account predicate instead.)

3. **A cash-non-negative rule sounds sufficient. Construct the trade that beats it.** (Part 3. A
   short generates cash, so shorting $10M raises cash to $10.1M while creating −$10M of exposure — a
   cash floor never fires. Capping *gross exposure* catches it.)

4. **What is the de-risking carve-out, and what disaster does it prevent?** (Part 3. A fill that
   doesn't increase gross exposure is always admitted. Without it, an account pushed over the cap by
   mark-to-market drift could never trade again — it couldn't even close its losing position. A
   locked account is worse than the leverage bug.)

5. **Adding the check changed nothing in the Task 10 grid. Why is that expected, not suspicious?**
   (Part 4 / Part 0. Fixed-fractional sizing targets 10% of the account, so gross exposure ≈ 0.1× —
   nowhere near the 1× cap. Zero rejections, so identical metrics. A row that *changed* would
   be the finding.)

If those come out cleanly in your own words, you've got Task 12 cold.

---

## Mini-glossary

- **Leverage** — the ratio of gross market exposure to equity. 1× means fully invested with no
  borrowing; 10× means $10 of exposure per $1 of equity.
- **Gross exposure** — the sum of the *absolute* market values of all positions (`Σ |qty × mark|`).
  Absolute, so a short counts as exposure just like a long.
- **Equity** — total account value: cash plus the mark-to-market value of positions.
- **Margin / buying-power check** — the rule that refuses a trade which would exceed the leverage
  cap; here, `admits_fill`.
- **Reject record** — seam 6's record kind for an order the engine refused to fill; written to
  `reject.parquet`. Task 12 is its first use.
- **De-risking carve-out** — always allowing a fill that reduces (or doesn't increase) gross
  exposure, so an account pushed over the cap by price drift can still close out and never locks up.
- **Short generates cash** — selling borrowed shares credits cash while creating a negative
  position, which is why a cash floor can't constrain shorting and a gross-exposure cap can.
