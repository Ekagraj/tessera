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
