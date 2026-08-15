"""Cash plus mark-to-market position value must equal total equity at every timestamp.

This is one of the three load-bearing tests. It drives the book through an awkward
sequence — a partial fill, a long-to-short flip in one fill, and a full close — and
after each mark asserts two identities:

    equity                     == cash + market_value
    realized + unrealized      == equity - initial_cash
"""

from __future__ import annotations

import pytest

from tessera.portfolio import accounting
from tessera.portfolio.book import Book, Position

INITIAL = 100_000.0


def _check_identities(book: Book, prices: dict[str, float]) -> float:
    eq = accounting.equity(book, prices)
    assert eq == pytest.approx(book.cash + accounting.market_value(book, prices))
    assert book.realized_pnl + accounting.unrealized_pnl(book, prices) == pytest.approx(
        eq - INITIAL
    )
    return eq


def test_equity_identity_through_partial_fill_flip_and_close() -> None:
    book = Book(cash=INITIAL)

    # --- partial fills: an order for 100 arrives as 40 then 60 ---
    assert book.apply_fill("AAPL", 40, 10.0) == pytest.approx(0.0)
    assert book.apply_fill("AAPL", 60, 10.1) == pytest.approx(0.0)
    pos = book.position("AAPL")
    assert pos.qty == pytest.approx(100.0)
    assert pos.avg_price == pytest.approx(10.06)  # (40*10 + 60*10.1) / 100
    _check_identities(book, {"AAPL": 10.5})

    # --- long -> short flip in one fill: sell 150 while long 100 ---
    realized = book.apply_fill("AAPL", -150, 12.0)
    assert realized == pytest.approx(194.0)  # closes 100 @ (12 - 10.06)
    pos = book.position("AAPL")
    assert pos.qty == pytest.approx(-50.0)  # now short 50
    assert pos.avg_price == pytest.approx(12.0)  # new basis at the fill price
    _check_identities(book, {"AAPL": 11.0})

    # --- close the short: buy 50 back ---
    realized = book.apply_fill("AAPL", 50, 9.0)
    assert realized == pytest.approx(150.0)  # closes short 50 @ (9 - 12) * -1
    assert book.position("AAPL").qty == pytest.approx(0.0)
    assert "AAPL" not in book.positions  # flat positions are dropped
    assert book.realized_pnl == pytest.approx(344.0)

    eq = _check_identities(book, {})  # flat: no marks needed
    assert eq == pytest.approx(book.cash)


def test_costs_are_a_realized_drag() -> None:
    book = Book(cash=INITIAL)
    realized = book.apply_fill("AAPL", 100, 10.0, cost=5.0)
    assert realized == pytest.approx(-5.0)  # nothing closed, but the fee is realized
    assert book.cash == pytest.approx(INITIAL - 1000.0 - 5.0)
    # Marked at the same price, equity is down exactly the fee.
    eq = _check_identities(book, {"AAPL": 10.0})
    assert eq == pytest.approx(INITIAL - 5.0)


def test_short_then_cover_at_lower_price_profits() -> None:
    book = Book(cash=INITIAL)
    book.apply_fill("AAPL", -10, 100.0)  # open short 10 @ 100
    assert book.position("AAPL").qty == pytest.approx(-10.0)
    _check_identities(book, {"AAPL": 100.0})
    realized = book.apply_fill("AAPL", 10, 90.0)  # cover @ 90 -> +10 each
    assert realized == pytest.approx(100.0)
    assert book.realized_pnl == pytest.approx(100.0)
    eq = _check_identities(book, {})
    assert eq == pytest.approx(INITIAL + 100.0)


def test_adding_to_a_long_blends_average_cost() -> None:
    book = Book(cash=INITIAL)
    book.apply_fill("AAPL", 100, 10.0)
    book.apply_fill("AAPL", 100, 20.0)
    pos = book.position("AAPL")
    assert pos.qty == pytest.approx(200.0)
    assert pos.avg_price == pytest.approx(15.0)  # blended, no PnL realized yet
    assert book.realized_pnl == pytest.approx(0.0)
    _check_identities(book, {"AAPL": 20.0})


# --- margin / leverage admissibility (D43) -------------------------------------

def test_admits_within_cap_and_rejects_beyond_on_both_sides() -> None:
    book = Book(cash=INITIAL)  # flat, max_leverage 1.0
    prices = {"AAPL": 10.0}
    # 10% notional (1,000 @ 10 = 10k) is well within 1x of 100k equity.
    assert accounting.admits_fill(book, "AAPL", +1_000.0, 10.0, prices) is True
    # The audit's exploit: 1,000,000 @ 10 = 10M gross on 100k equity -> rejected, long...
    assert accounting.admits_fill(book, "AAPL", +1_000_000.0, 10.0, prices) is False
    # ...and short (a short generates cash but still creates 10M of gross exposure).
    assert accounting.admits_fill(book, "AAPL", -1_000_000.0, 10.0, prices) is False
    # Exactly at the cap (gross 100k == 1x * 100k equity) is admitted, not tripped by rounding.
    assert accounting.admits_fill(book, "AAPL", +10_000.0, 10.0, prices) is True


def test_derisk_carveout_from_an_over_limit_state() -> None:
    """An account can only exceed the cap via mark-to-market drift (no fill creates it),
    e.g. a short moving against it. From there it must be able to REDUCE exposure but not
    INCREASE it — otherwise the account locks up, a worse failure than the leverage bug.
    """
    # Short 1,000 @ 100 leaves cash 200k and a -1,000 position; then the price drifts up to
    # 150 (the short loses): equity 200k - 150k = 50k, gross 150k -> leverage 3x, over the cap.
    book = Book(cash=200_000.0, positions={"AAPL": Position(qty=-1_000.0, avg_price=100.0)})
    prices = {"AAPL": 150.0}
    assert accounting.equity(book, prices) == pytest.approx(50_000.0)
    assert abs(accounting.market_value(book, prices)) == pytest.approx(150_000.0)  # 3x of 50k

    # REDUCE: buy back 500 of the short -> gross falls 150k -> 75k. Allowed despite being over.
    assert accounting.admits_fill(book, "AAPL", +500.0, 150.0, prices) is True
    # INCREASE: short 500 more -> gross rises 150k -> 225k. Rejected.
    assert accounting.admits_fill(book, "AAPL", -500.0, 150.0, prices) is False
    # Fully closing (buy 1,000) is a reduction to flat -> always allowed.
    assert accounting.admits_fill(book, "AAPL", +1_000.0, 150.0, prices) is True
