# Understanding Task 4: portfolio accounting

A from-scratch explanation, no code required. By the end you should be able to answer
the interview questions yourself, in your own words, and know *why* each answer is
right.

Read it once top to bottom, then try the "Answer these yourself" section.

---

## Part 0 — The problem we are actually solving

A strategy (Task 3) emits **orders** — intent. Later, a fill model (Task 5) will turn
those into **fills** — "you actually bought 100 shares at $10." But once a fill
happens, somebody has to keep the books: *how much cash do I have, what do I own, and
how much money have I made or lost?*

That bookkeeping is Task 4. It sounds mundane, but it's where subtle errors hide, and
there's one iron rule it must never break:

> **At every instant, your cash plus the current value of what you own equals your
> total account value (equity).**

If that identity ever drifts, your equity curve is lying, and every performance number
computed from it is wrong. This is one of the three load-bearing tests for a reason.

Two files split the job:
- **`book.py`** — the *state*: cash, positions, and how a fill changes them.
- **`accounting.py`** — the *derived views*: given current market prices, what's my
  equity and my unrealized profit? (Pure functions; they never change the state.)

---

## Part 1 — What "a position" needs to remember

For each symbol you hold, you track two numbers:
- **quantity** — how many units, *signed*: positive means long (you own it), negative
  means short (you owe it).
- **average price** — what you paid, on average, per unit of the position you currently
  hold. (0 when you hold nothing.)

Plus, at the account level: your **cash**, and a running total of **realized PnL**
(profit/loss you've actually locked in by closing trades).

That's the whole state. Everything else is computed from it.

---

## Part 2 — Realized vs unrealized: the key distinction

There are two kinds of profit, and confusing them is a classic mistake.

- **Unrealized PnL** — profit *on paper*. You're long 100 shares at an average cost of
  $10, and the market is now $11. You're "up" $100 — but only on paper, because you
  haven't sold. If the price drops back to $10 tomorrow, that $100 evaporates. Nothing
  is locked in.
- **Realized PnL** — profit *locked in*. The moment you sell those shares at $11, the
  $100 is real. It moves out of "on paper" and into your actual cash.

So: **you own things → unrealized.** **You close things → realized.** Marking to market
(Part 5) is how we measure the unrealized part.

> Recite: *unrealized is paper profit on what you still hold; realized is locked-in
> profit from what you've closed.*

---

## Part 3 — Average cost vs FIFO (the design decision)

When you close part of a position, *which* shares did you sell, and what did they cost?
Two conventions:

- **Average cost:** blend everything into one average price. Bought 100 @ $10 and
  another 100 @ $20? Your position is 200 @ $15 average. Sell some, and the profit is
  measured against that $15. One number, simple.
- **FIFO (first-in-first-out):** track each purchase as its own "lot" with its own
  price. When you sell, you sell the *oldest* lot first. This matters for taxes (lots
  have holding periods) and for matching a broker's official statement.

**The crucial insight:** over the *entire life* of a position — from first buy to
final close — the **total** profit is *exactly the same* under both methods. The only
difference is the *timing*: which specific dollars get called "realized" at which
moment, and how per-trade attribution looks. A tax office cares. A strategy researcher
measuring an equity curve does not.

So we chose **average cost**: simpler, one number per position, deterministic, and it
gives the same bottom line. FIFO can be added later behind the same interface if we
ever need tax-lot fidelity. (This is the recurring theme: pick the simple correct thing
now, leave the door open for the fancy thing later.)

> Recite: *average cost vs FIFO changes the timing and attribution of realized PnL, not
> the lifetime total. We use average cost because a research backtest cares about the
> total and the equity curve, not tax lots.*

---

## Part 4 — Three awkward cases, and how we handle them

### Partial fills — nothing special needed
An order for 100 shares might fill in pieces: 40, then 60. We handle this by never
thinking in "orders" at the accounting layer — we apply **one fill at a time**, and each
fill just adds to the position and adjusts cash. Because every fill keeps the books
balanced on its own, partial fills are handled *for free*. (40 @ $10 then 60 @ $10.10 →
100 shares at an average of $10.06.)

### The long-to-short flip — split at zero
This is the tricky one. You're **long 100** at average $10, and a single **sell of 150**
comes in at $12. That one fill does two separate things:
1. It **closes your entire long 100** → you realize `(12 − 10) × 100 = +$200`.
2. It **opens a brand-new short 50** at $12 → new average price is $12.

The rule: when a fill pushes you *through zero*, split it. Realize profit only on the
part that **closed** (the 100). The leftover (the 50) starts a fresh position at the
fill price. You do **not** compute profit on the newly-opened short — it hasn't gone
anywhere yet. Getting this split wrong is a classic accounting bug.

### Fees — a realized cost, right away
Commissions drain your cash. We also count them as a *realized loss* immediately (rather
than folding them into the cost basis of the position). Why? Because it keeps a clean
identity true: `realized + unrealized = equity − starting cash`. A fee is money gone, so
it belongs in realized PnL the moment it's paid.

---

## Part 5 — Marking to market: where and at what price

"Mark to market" = update the paper value of your holdings to the current price.

- **At what price?** The **most recent observed market price** — the last bar's close.
  Not some average, not a future price. Just "what's it worth right now."
- **Where does it happen?** In `accounting.py`, using a `prices` map that the engine
  keeps updated as events arrive. Because the engine only ever puts the *latest seen*
  price in that map, a mark can never accidentally use a future price — the no-look-ahead
  rule from Task 1 is preserved even here.

From that, two derived numbers:
- **market value** = for each position, `quantity × current price`, summed up.
- **equity** = `cash + market value`. Your total account worth.

And the iron identity the test checks at every step:

```
equity  ==  cash  +  market_value
```

plus the cross-check that ties realized and unrealized together:

```
realized_pnl  +  unrealized_pnl  ==  equity  −  starting_cash
```

> Recite: *mark at the latest observed price (last close); equity = cash + market value;
> unrealized = quantity × (mark − average cost).*

---

## Part 6 — What actually got built in Task 4

Two files, plus the second of the three load-bearing tests.

- **`tessera/portfolio/book.py`** — `Position` (qty + average price) and `Book` (cash,
  positions, realized PnL). Its one real method, `apply_fill(symbol, qty, price, cost)`,
  updates everything: cash flow, the average-cost blend, the zero-crossing split, and
  fees.
- **`tessera/portfolio/accounting.py`** — pure functions `market_value`, `equity`, and
  `unrealized_pnl` that read the book and a prices map. They never change the book.
- **`tests/test_accounting.py`** — drives the book through a partial fill, a long-to-
  short flip, and a full close, checking the equity identity after every mark.

---

## Worked example with synthetic data

One symbol (AAPL). Start with **$100,000** cash. We'll apply a scripted sequence of
fills and mark after each. `apply_fill(symbol, qty, price, cost)` — `qty` is signed
(+buy, −sell). Watch the identity `equity = cash + market value` hold throughout.

```
START: cash = 100,000, no positions, realized = 0

FILL  buy 40 @ 10.00     cash -= 400   -> cash 99,600;  pos 40 @ 10.00
FILL  buy 60 @ 10.10     cash -= 606   -> cash 98,994;  pos 100 @ 10.06   (avg blended)
   MARK @ 10.50:
     market value = 100 * 10.50 = 1,050
     equity = 98,994 + 1,050 = 100,044
     unrealized = 100 * (10.50 - 10.06) = 44 ;  realized = 0
     check: realized + unrealized = 44 = equity - 100,000   OK

FILL  sell 150 @ 12.00   cash += 1,800 -> cash 100,794
     FLIP: closes long 100 -> realized (12.00 - 10.06)*100 = +194
           opens short 50 @ 12.00 (new basis)
     pos = -50 @ 12.00 ;  realized total = 194
   MARK @ 11.00:
     market value = -50 * 11.00 = -550
     equity = 100,794 - 550 = 100,244
     unrealized = -50 * (11.00 - 12.00) = +50 ;  realized = 194
     check: 194 + 50 = 244 = equity - 100,000   OK

FILL  buy 50 @ 9.00      cash -= 450   -> cash 100,344
     closes short 50 -> realized (9.00 - 12.00)*50*(-1) = +150
     pos = flat (dropped) ;  realized total = 344
   MARK (flat):
     market value = 0 ; equity = 100,344 = cash
     check: realized 344 + unrealized 0 = 344 = equity - 100,000   OK
```

Every single mark satisfies `equity = cash + market value`. That's exactly what the
load-bearing test asserts — through a partial fill, a flip, and a close.

### Which file and function did each step

| Step above | File | Function / type |
|---|---|---|
| holding qty + average cost | `tessera/portfolio/book.py` | `Position` |
| cash, realized PnL, applying a fill | `tessera/portfolio/book.py` | `Book`, `Book.apply_fill()` |
| the zero-crossing flip split | `tessera/portfolio/book.py` | inside `apply_fill` (opposing-direction branch) |
| market value of positions | `tessera/portfolio/accounting.py` | `market_value()` |
| cash + market value | `tessera/portfolio/accounting.py` | `equity()` |
| paper profit on open positions | `tessera/portfolio/accounting.py` | `unrealized_pnl()` |
| the equity-identity checks | `tests/test_accounting.py` | `_check_identities`, `test_equity_identity_through_partial_fill_flip_and_close` |

---

## Answer these yourself

Cover the text and try these.

1. **Average cost or FIFO — which did you pick, and what does the choice actually
   change?** (Part 3. It changes the *timing* and attribution of realized PnL, never the
   lifetime total; we picked average cost because a research backtest cares about the
   total and the equity curve.)

2. **A fill takes you from long 100 to short 50 in one trade. What happens to realized
   PnL?** (Part 4. It closes the long 100 — realizing profit on exactly those 100 versus
   the old average — and opens a fresh short 50 at the fill price. No PnL on the new 50.)

3. **Where does unrealized PnL get marked, and at what price?** (Part 5. In the
   accounting layer, using the latest observed market price — the last bar's close — fed
   by the engine so it's never a future price. Unrealized = qty × (mark − average cost).)

4. **Why do fees reduce realized PnL immediately instead of being added to cost basis?**
   (Part 4. A fee is cash gone, so it belongs in realized right away; this keeps
   `realized + unrealized = equity − starting cash` exactly true.)

If those come out cleanly in your own words, you've got Task 4 cold.

---

## Mini-glossary

- **Position** — what you hold in one symbol: signed quantity + average cost.
- **Long / short** — you own it (positive qty) / you owe it (negative qty).
- **Cash** — actual money in the account, changed by every fill and fee.
- **Realized PnL** — profit locked in by closing a position.
- **Unrealized PnL** — paper profit on positions you still hold.
- **Average cost** — one blended entry price per position.
- **FIFO** — first-in-first-out lot accounting (oldest shares sold first).
- **Mark to market** — revaluing holdings at the current market price.
- **Market value** — quantity × current price, summed over positions.
- **Equity** — total account value = cash + market value.
