"""Tests for the Barclaycard PDF-statement parser.

Run against a synthetic fixture modelled on the real statement layout (no real
personal data), plus inline cases for year inference across a year boundary.
"""

from __future__ import annotations

import datetime
from pathlib import Path

from banklib.parse.barclays_pdf import _infer_year, parse_barclays_statement

FIXTURE = Path(__file__).parent / "fixtures" / "barclays_statement.txt"


def _parse_fixture():
    return parse_barclays_statement(FIXTURE.read_text(encoding="utf-8"))


def test_metadata():
    ps = _parse_fixture()
    assert ps.issue_date == datetime.date(2026, 11, 5)
    assert ps.account == "Gold Visa"
    assert ps.period_start == datetime.date(2026, 10, 1)
    assert ps.period_end == datetime.date(2026, 10, 27)


def test_transaction_count_excludes_marketing_and_summaries():
    # 1 payment + 4 card transactions; the summary £-lines and the "Ways to pay"
    # £999.99 must not be captured.
    ps = _parse_fixture()
    assert len(ps.transactions) == 5
    assert all(abs(t.amount) != 999.99 for t in ps.transactions)


def test_signs_and_types():
    ps = _parse_fixture()
    by_desc = {t.description: t for t in ps.transactions}
    assert by_desc["Payment By Direct Debit"].amount == 50.00   # payment -> positive
    assert by_desc["Payment By Direct Debit"].type == "payment"
    assert by_desc["Coffee Shop, Testville"].amount == -3.50    # purchase -> negative
    assert by_desc["Coffee Shop, Testville"].type == "purchase"
    assert by_desc["Refund Co, London"].amount == 10.00         # CR -> positive
    assert by_desc["Refund Co, London"].type == "refund"


def test_contactless_marker_dropped_and_wrapped_description_joined():
    ps = _parse_fixture()
    descs = [t.description for t in ps.transactions]
    assert "Coffee Shop, Testville" in descs          # lone "e" line dropped
    assert "Big Store*Order12345, Ref-98765" in descs  # wrapped onto two lines


def test_digits_in_description_not_mistaken_for_amount():
    ps = _parse_fixture()
    late = next(t for t in ps.transactions if t.description.startswith("Late Shop"))
    assert late.description == "Late Shop, Testville 0800 328 1700"
    assert late.amount == -7.25


def test_year_inference_within_statement():
    ps = _parse_fixture()
    assert all(t.date.year == 2026 for t in ps.transactions)


def test_year_inference_across_year_boundary():
    issue = datetime.date(2027, 1, 5)
    assert _infer_year(28, 12, issue) == datetime.date(2026, 12, 28)  # Dec -> prev year
    assert _infer_year(2, 1, issue) == datetime.date(2027, 1, 2)      # Jan -> same year


def test_rows_carry_provider_and_source():
    ps = _parse_fixture()
    assert all(t.provider == "barclays" for t in ps.transactions)
    assert all(t.source == "barclays-statement:2026-11-05" for t in ps.transactions)
    # Distinct seq keeps genuine duplicates apart.
    assert len({t.seq for t in ps.transactions}) == len(ps.transactions)


def test_missing_issue_date_returns_no_transactions():
    ps = parse_barclays_statement("Your Gold Visa statement\n01 Oct\nShop\n£1.00")
    assert ps.issue_date is None
    assert ps.transactions == []
