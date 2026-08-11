# Understanding Task 5: the naive fill model and costs

A from-scratch explanation, no code required. By the end you should be able to answer
the interview questions yourself, in your own words, and know *why* each answer is
right.

Read it once top to bottom, then try the "Answer these yourself" section.

---

## Part 0 — The problem we are actually solving

A strategy emits an **order** — "I want to buy 100 AAPL." Between that wish and an
actual trade sits reality: *at what price does it fill, and when?* That's the job of a
**fill model**. Get it slightly wrong — especially in the optimistic direction — and
your backtest prints beautiful, fake profits.

Task 5 builds the simplest honest fill model (`NaiveFillModel`), a simple cost model
(`BpsCostModel`), and — importantly — the *plumbing* for order latency, even though we
set the latency to zero for now.

The two questions a fill model answers: **when** does an order fill, and **at what
price**. Getting "when" wrong is how look-ahead sneaks back in.

---

## Part 1 — Next open, not current close (the heart of it)

Picture a strategy watching daily bars. On **Monday**, after the market closes, it
looks at Monday's bar and decides "buy." When can it actually buy?

The tempting (and wrong) answer: "fill it at Monday's close." But think about *when*
the strategy knew Monday's close — only *after Monday's session ended*. By then that
price is history; you can't place an order into the past. Filling at Monday's close
means trading at a price you only learned once it was already gone. **That is
look-ahead.** It quietly hands the strategy a price it could never have gotten.

The honest answer: the first price a Monday-close decision can actually transact at is
**Tuesday's open**. So market orders fill at the **next bar's open**.

```
Monday close: strategy decides "buy"   (it now knows Monday's close)
Tuesday open: the order fills          (first price actually reachable)
```

### Why this matters so much

Filling at the close doesn't just add a small error — it adds a *systematic, always-
favorable* one, and it's worst for strategies that react to the close (momentum,
reversal). That leads to the sharp interview point:

> If a strategy is profitable **only** when you fill at the close, that's not a
> discovery — it's a symptom. The "edge" is the look-ahead bias itself, not a real
> signal. Switch to next-open fills and the profit usually vanishes.

> Recite: *decide at the close, fill at the next open. Filling at the close trades at a
> price you only knew once it was unreachable — look-ahead — and it flatters
> close-reactive strategies most.*

---

## Part 2 — The five lies of NaiveFillModel

"Naive" is honest branding: this model is deliberately wrong about reality in five
specific ways. Naming them is the point — each is a knob we can make realistic later
without touching any strategy.

1. **Infinite liquidity.** Your entire order fills, no matter how huge. Reality: a big
   order can't all trade at one price; you run out of willing sellers.
2. **No slippage.** You get *exactly* the open price. Reality: the act of buying pushes
   the price up against you; your average fill is worse than the quote.
3. **No spread.** You trade at the open as if it were free. Reality: you buy at the
   (higher) ask and sell at the (lower) bid, paying that gap every single trade.
4. **Certain execution.** Every order fills, fully, right away. Reality: partial fills,
   rejections, gaps, halts, and opening auctions all happen.
5. **Trivial flat costs.** Our cost is a fixed number of basis points regardless of size
   or conditions. Reality: costs grow nonlinearly with order size and swing with
   volatility and liquidity.

Why ship something with five known lies? Because they're **explicit and isolated**.
Each lives behind an interface (`FillModel`, `CostModel`) we can swap. A model that's
naive-but-honest-about-it beats one that hides its assumptions.

> Recite: *infinite liquidity, no slippage, no spread, certain execution, flat costs.
> All acceptable because each is an explicit, swappable seam — not a hidden assumption.*

---

## Part 3 — Latency plumbing we build now and don't use yet

Here's a move that looks like over-engineering but isn't. Every order, when submitted,
gets an **arrival time**:

```
arrival_ts = submit_ts + latency_ns
```

and it sits in a **pending queue** until the clock reaches that arrival time. Real
orders take time to travel to the exchange; `latency_ns` models that delay.

For week 1 we set `latency_ns = 0`, so `arrival_ts = submit_ts` and nothing visibly
changes — orders still fill at the next open. So why build the queue at all?

Because adding it *later* would be painful. Latency touches three components at once —
the engine loop, the fill model, and portfolio timing. Retrofitting a pending queue
through all three is invasive surgery. Building the ~20 lines of plumbing **now**, while
it's trivial, means that turning on realistic latency later is a **one-line config
change** (`latency_ns = 500_000`), not a rewrite. Build the pipe now; turn on the water
later.

> Recite: *every order gets arrival_ts = submit_ts + latency_ns and waits in a pending
> queue. At 0 it's a no-op, but building the plumbing now makes real latency a config
> value instead of surgery across three components.*

---

## Part 4 — The two smaller design calls

### Costs: a narrowed signature (a flagged seam decision)
The architecture sketched the cost function as taking a `MarketCtx` (market context) —
useful for future spread/impact models. But that type doesn't exist yet, and inventing
its contents now would be guessing. So we narrowed the cost function to what a
basis-points charge actually needs — the order, the fill price, and the quantity — and
**flagged** that we're deferring `MarketCtx` until a cost model genuinely needs it. This
is deliberate and documented, not accidental drift; widening it later touches only our
own cost models.

### Limit orders: one shot at the next open
Week-1 strategies only send *market* orders, so we didn't build a full resting-limit-
order book. Instead: a limit order gets **one attempt** at the next open. If the open
crosses its limit price (a buy limit at 11.50 when the open is 11.00 — good, that's at
or below your limit), it fills; otherwise it's cancelled rather than left lingering
forever. Enough to complete the interface, without building machinery nothing uses yet.

---

## Part 5 — What actually got built in Task 5

Three files, plus a test.

- **`tessera/execution/base.py`** — the `Fill` record (what actually traded + its cost)
  and the two interfaces: `FillModel` (`submit` an order, `on_event` produces fills) and
  `CostModel` (`cost` of a fill).
- **`tessera/execution/naive.py`** — `NaiveFillModel`: holds orders in a pending queue
  with arrival times, fills market orders at the next bar's open, gives limit orders one
  shot.
- **`tessera/execution/costs.py`** — `BpsCostModel`: a flat basis-points charge on
  notional.
- **`tests/test_fills.py`** — proves next-open (not close) fills, that latency delays a
  fill, symbol matching, the bps cost, and the limit-crossing rule.

---

## Worked example with synthetic data

Daily AAPL bars. Note each bar's **open differs from its close** so we can *prove* we
never fill at the close. `submit(order, ts)` queues an order; `on_event(bar)` fills
eligible ones at that bar's open.

```
Bar day0:  open 10.0, close 10.5
Bar day1:  open 11.0, close 11.5
Bar day2:  open 12.0, close 12.5
```

### (a) A market order fills at the NEXT open

```
on_event(day0)                       -> []          (nothing pending)
submit(BUY 100 AAPL market, ts=day0) -> queued      (decided using day0's close 10.5)
on_event(day0)                       -> []          (must NOT fill on the same bar)
on_event(day1)                       -> FILL 100 @ 11.0
```

The fill price is **11.0 — day1's open — not 10.5, day0's close.** That single fact is
the whole no-look-ahead point of the fill model.

### (b) Latency pushes the fill further out

With `latency_ns = 1.5 days`, an order submitted at day0 has `arrival = day0 + 1.5d`:

```
submit(BUY market, ts=day0)   arrival_ts = day0 + 1.5 days
on_event(day1)  -> []    (day1 is only 1 day out; arrival not reached)
on_event(day2)  -> FILL @ 12.0   (day2 is 2 days out; arrival reached -> fills at day2 open)
```

Same order, later fill, purely because of the arrival gate. At `latency = 0` this gate
is invisible; here you can see it working.

### (c) Cost is a basis-points drag

With `BpsCostModel(10)` (10 basis points = 0.001) on the 100-share fill at 11.0:

```
cost = 10 * 0.0001 * 11.0 * 100 = 1.1
```

That 1.1 rides on the Fill and later becomes a realized drag in the book (Task 4).

### Which file and function did each step

| Step above | File | Function / type |
|---|---|---|
| the synthetic bars | `tessera/core/events.py` | `Bar` |
| queuing an order with an arrival time | `tessera/execution/naive.py` | `NaiveFillModel.submit`, `_Pending` |
| filling at the next open / latency gate / limit crossing | `tessera/execution/naive.py` | `NaiveFillModel.on_event` |
| the fill record | `tessera/execution/base.py` | `Fill` |
| the basis-points cost | `tessera/execution/costs.py` | `BpsCostModel.cost` |
| next-open / latency / cost / limit tests | `tests/test_fills.py` | `test_market_order_fills_at_next_open_not_current_close`, etc. |

---

## Answer these yourself

Cover the text and try these.

1. **What are the five things NaiveFillModel gets wrong about reality?** (Part 2.
   Infinite liquidity, no slippage, no spread, certain execution, flat costs.)

2. **Why build the pending-order queue now, when latency is zero?** (Part 3. Retrofitting
   it later means invasive changes across the engine loop, fill model, and portfolio;
   building it now makes real latency a one-line config change.)

3. **A strategy is profitable only when filling at the close. What does that tell you?**
   (Part 1. The "edge" is look-ahead bias, not a real signal — it trades at a price only
   knowable after it was reachable. Next-open fills usually erase the profit.)

4. **Why fill at the next open instead of the current close, precisely?** (Part 1. The
   close is only known once the bar ends; the first actually-reachable price for a
   close-time decision is the next open.)

If those come out cleanly in your own words, you've got Task 5 cold.

---

## Mini-glossary

- **Fill** — an order that actually traded: price, quantity, side, and cost.
- **Fill model** — decides when and at what price an order fills.
- **Cost model** — prices the friction of a fill (commission/spread/impact).
- **Slippage** — getting a worse price than quoted because your order moves the market.
- **Spread** — the gap between the bid (sell here) and ask (buy here) you cross each trade.
- **Basis point (bp)** — one hundredth of a percent (0.01%); 10 bp = 0.1%.
- **Notional** — the cash value of a trade: price × quantity.
- **Latency** — the delay between placing an order and it reaching the market.
- **arrival_ts** — submit time + latency; an order can't fill before this.
- **Pending queue** — where submitted orders wait until they're eligible to fill.
