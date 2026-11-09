"""Tests for the pure monthly-report logic in :mod:`banklib.report`."""

from __future__ import annotations

import datetime

from banklib.models import Transaction
from banklib import report

D = datetime.date


def _txn(provider, desc, amount, day=15, account="Acct", type=None):
    return Transaction(date=D(2026, 10, day), description=desc, amount=amount,
                       account=account, provider=provider, type=type)


# -- month boundaries -------------------------------------------------------

def test_last_calendar_month_midyear():
    assert report.last_calendar_month(D(2026, 11, 9)) == (D(2026, 10, 1), D(2026, 10, 31))


def test_last_calendar_month_year_boundary():
    assert report.last_calendar_month(D(2026, 1, 3)) == (D(2025, 12, 1), D(2025, 12, 31))


def test_last_calendar_month_handles_february():
    # March -> February (28 days in 2026, not a leap year).
    assert report.last_calendar_month(D(2026, 3, 1)) == (D(2026, 2, 1), D(2026, 2, 28))


def test_parse_month():
    assert report.parse_month("2026-10") == (D(2026, 10, 1), D(2026, 10, 31))
    assert report.parse_month("2026-12") == (D(2026, 12, 1), D(2026, 12, 31))
    assert report.parse_month("2024-02") == (D(2024, 2, 1), D(2024, 2, 29))


# -- card-payment detection -------------------------------------------------

def test_is_card_payment_true_for_barclaycard_debit():
    assert report.is_card_payment(_txn("natwest", "BARCLAYCARD PAYMENT", -500.0))
    assert report.is_card_payment(_txn("natwest", "Credit Card Payment", -200.0))
    # The real NatWest statement payee for the Barclaycard settlement.
    assert report.is_card_payment(_txn("natwest", "B/CARD PLAT VISA", -2893.82))


def test_is_card_payment_false_for_ordinary_debit():
    assert not report.is_card_payment(_txn("natwest", "TESCO STORES", -42.0))
    # "CLUBCARD"/"CLUB CARD" must not be mistaken for the Barclaycard settlement.
    assert not report.is_card_payment(_txn("natwest", "TESCO CLUBCARD", -42.0))
    assert not report.is_card_payment(_txn("natwest", "TESCO CLUB CARD", -42.0))


def test_is_card_payment_false_for_barclays_purchase():
    # A Barclays purchase mentioning the card brand must not be mistaken for the
    # settlement — and an incoming (positive) row never counts.
    assert not report.is_card_payment(_txn("natwest", "BARCLAYCARD REFUND", 50.0))


# -- classify ---------------------------------------------------------------

def test_classify_splits_buckets():
    natwest = [
        _txn("natwest", "BARCLAYCARD PAYMENT", -500.0),
        _txn("natwest", "TESCO", -40.0),
        _txn("natwest", "SALARY", 2000.0),
    ]
    barclays = [
        _txn("barclays", "AMAZON", -30.0),
        _txn("barclays", "REFUND", 5.0),
    ]
    b = report.classify(natwest, barclays)

    assert len(b.card_payments) == 1
    assert b.card_payment_total == 500.0
    assert [t.description for t in b.income] == ["SALARY"]
    assert b.income_total == 2000.0
    # Spending = both Barclays rows + the NatWest non-card debit, not the payment.
    descs = sorted(t.description for t in b.spending)
    assert descs == ["AMAZON", "REFUND", "TESCO"]


def test_classify_drops_barclays_payment_credit():
    # The card settlement shows on the Barclays statement as a positive "payment"
    # credit; it must not land in spending (where it would cancel purchases).
    barclays = [
        _txn("barclays", "AMAZON", -30.0, type="purchase"),
        _txn("barclays", "Payment By Direct Debit", 500.0, type="payment"),
        _txn("barclays", "VINTED REFUND", 5.0, type="refund"),
    ]
    b = report.classify([], barclays)
    descs = sorted(t.description for t in b.spending)
    assert descs == ["AMAZON", "VINTED REFUND"]
    # Net Barclays spend is the purchase less the refund, not flipped negative.
    spend, _ = report.aggregate_by_category(b.spending, {})
    assert sum(c.total for c in spend) == 25.0


# -- category response parsing ---------------------------------------------

def test_parse_category_response_plain():
    text = '{"TESCO": {"category": "Groceries", "merchant": "Tesco"}}'
    out = report.parse_category_response(text)
    assert out["TESCO"] == {"category": "Groceries", "merchant": "Tesco"}


def test_parse_category_response_fenced_and_prose():
    text = ("Here you go:\n```json\n"
            '{"NETFLIX": {"category": "Subscriptions", "merchant": "Netflix"}}\n'
            "```\nHope that helps.")
    out = report.parse_category_response(text)
    assert out["NETFLIX"]["category"] == "Subscriptions"


def test_parse_category_response_coerces_unknown_category():
    text = '{"WIDGET CO": {"category": "Nonsense", "merchant": "Widget"}}'
    out = report.parse_category_response(text)
    assert out["WIDGET CO"]["category"] == "Other"


def test_parse_category_response_bad_input():
    assert report.parse_category_response("not json at all") == {}
    assert report.parse_category_response("") == {}


# -- aggregation ------------------------------------------------------------

def test_aggregate_by_category_totals_and_splits():
    spending = [
        _txn("barclays", "TESCO", -40.0),
        _txn("natwest", "SAINSBURYS", -60.0),
        _txn("barclays", "AMAZON", -30.0),
        _txn("barclays", "AMAZON REFUND", 10.0),
        _txn("natwest", "MOVE TO SAVINGS", -200.0),
    ]
    categories = {
        "TESCO": {"category": "Groceries", "merchant": "Tesco"},
        "SAINSBURYS": {"category": "Groceries", "merchant": "Sainsbury's"},
        "AMAZON": {"category": "Shopping", "merchant": "Amazon"},
        "AMAZON REFUND": {"category": "Shopping", "merchant": "Amazon"},
        "MOVE TO SAVINGS": {"category": "Savings", "merchant": "Savings"},
    }
    spend, non_spend = report.aggregate_by_category(spending, categories)

    # Groceries 100 (largest) before Shopping 20 (net of the 10 refund).
    assert [(c.category, c.total, c.count) for c in spend] == [
        ("Groceries", 100.0, 2), ("Shopping", 20.0, 2)]
    # Savings is split out as non-spend.
    assert [(c.category, c.total) for c in non_spend] == [("Savings", 200.0)]


def test_aggregate_transfers_count_as_spend():
    spending = [
        _txn("natwest", "FP TO SAM", -80.0),
        _txn("natwest", "MOVE TO SAVINGS", -200.0),
    ]
    categories = {
        "FP TO SAM": {"category": "Transfers", "merchant": "Sam"},
        "MOVE TO SAVINGS": {"category": "Savings", "merchant": "Savings"},
    }
    spend, non_spend = report.aggregate_by_category(spending, categories)
    assert [(c.category, c.total) for c in spend] == [("Transfers", 80.0)]
    assert [(c.category, c.total) for c in non_spend] == [("Savings", 200.0)]


def test_aggregate_uncategorised_fallback():
    spending = [_txn("barclays", "MYSTERY SHOP", -15.0)]
    spend, non_spend = report.aggregate_by_category(spending, {})
    assert spend[0].category == report.UNCATEGORISED
    assert spend[0].total == 15.0
