"""Owns mark-to-market accounting: equity, market value, and unrealized PnL.

These are pure functions over a `Book` plus a `prices` map of the latest observed
market price per symbol (supplied by the engine, which updates it as events arrive —
so marks never use a price beyond the current clock). Nothing here mutates the book.

The core identity, which the load-bearing accounting test checks at every timestamp:

    equity(book, prices) == book.cash + market_value(book, prices)

and, as an internal cross-check:

    realized_pnl + unrealized_pnl(book, prices) == equity(book, prices) - initial_cash
"""

from __future__ import annotations

from collections.abc import Mapping

from tessera.portfolio.book import Book

# Float tolerance so an exactly-at-the-cap fill (gross == max_leverage * equity) is admitted
# rather than tripped by rounding. Tiny relative to any realistic notional.
_EPS = 1e-6


def admits_fill(
    book: Book,
    symbol: str,
    signed_qty: float,
    price: float,
    prices: Mapping[str, float],
    cost: float = 0.0,
) -> bool:
    """Whether applying this fill keeps gross exposure within the book's leverage cap.

    Pure — never mutates the book. Marks the traded `symbol` at its fill `price` (known now)
    and every other open position at its last observed price in `prices`; because `prices`
    never holds a price beyond the current clock, the check cannot look ahead. A fill is
    admitted iff the resulting gross exposure is within `book.max_leverage x equity`, **or**
    it does not increase gross exposure. That second clause is the de-risking carve-out: an
    account pushed over the cap by mark-to-market drift (a short moving against it, say) can
    always reduce exposure and never locks up — the failure mode that would be worse than the
    unlimited-leverage bug this guards against (decision D43).
    """

    def mark(sym: str) -> float:
        return price if sym == symbol else prices.get(sym, book.position(sym).avg_price)

    qtys: dict[str, float] = {s: p.qty for s, p in book.positions.items()}
    qtys[symbol] = qtys.get(symbol, 0.0) + signed_qty

    cash_after = book.cash - signed_qty * price - cost
    equity_after = cash_after + sum(q * mark(s) for s, q in qtys.items())
    gross_after = sum(abs(q * mark(s)) for s, q in qtys.items())
    gross_before = sum(abs(p.qty * mark(s)) for s, p in book.positions.items())

    if gross_after <= gross_before + _EPS:
        return True  # exposure-reducing (or unchanged): always allowed, even if over the cap
    return equity_after > 0.0 and gross_after <= book.max_leverage * equity_after + _EPS


def market_value(book: Book, prices: Mapping[str, float]) -> float:
    """Total mark-to-market value of all open positions at the given prices."""
    return sum(pos.qty * prices[symbol] for symbol, pos in book.positions.items())


def equity(book: Book, prices: Mapping[str, float]) -> float:
    """Total account value: cash plus the market value of open positions."""
    return book.cash + market_value(book, prices)


def unrealized_pnl(book: Book, prices: Mapping[str, float]) -> float:
    """Open-position PnL not yet realized: sum of qty x (mark_price - avg_price)."""
    return sum(
        pos.qty * (prices[symbol] - pos.avg_price) for symbol, pos in book.positions.items()
    )
