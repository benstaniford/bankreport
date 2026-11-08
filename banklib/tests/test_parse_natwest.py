"""Tests for the NatWest transactions-CSV parser."""

from __future__ import annotations

import datetime
from pathlib import Path

from banklib.parse.natwest_csv import parse_natwest_csv
from banklib.store import TransactionStore

FIXTURE = Path(__file__).parent / "fixtures" / "natwest_download.csv"


def _parse():
    return parse_natwest_csv(FIXTURE.read_text(encoding="utf-8"))


def test_count_and_dates():
    txns = _parse()
    assert len(txns) == 6
    assert txns[0].date == datetime.date(2025, 11, 18)


def test_signed_values_map_directly():
    txns = _parse()
    salary = next(t for t in txns if t.description == "ACME SALARY")
    assert salary.amount == 2000.00          # credit stays positive
    xfer = next(t for t in txns if t.description.startswith("To A/C"))
    assert xfer.amount == -500.00            # debit stays negative


def test_quoted_description_with_commas_preserved():
    txns = _parse()
    xfer = next(t for t in txns if t.description.startswith("To A/C"))
    assert xfer.description == "To A/C 22222222 , SAVINGS , Via Mobile Xfer"


def test_fields_populated():
    txns = _parse()
    t = txns[0]
    assert t.provider == "natwest"
    assert t.account == "MAIN ACCOUNT"
    assert t.type == "BAC"
    assert t.balance == 2500.00
    assert t.source == "natwest-csv"


def test_multiple_accounts():
    txns = _parse()
    assert {t.account for t in txns} == {"MAIN ACCOUNT", "SAVINGS"}


def test_identical_same_day_rows_are_distinct_via_balance():
    # Two identical COFFEE SHOP -3.50 on the same day differ only by balance;
    # both must survive into the cache.
    txns = _parse()
    coffees = [t for t in txns if "COFFEE SHOP" in t.description]
    assert len(coffees) == 2
    assert coffees[0].fingerprint() != coffees[1].fingerprint()
    store = TransactionStore("natwest", db_path=":memory:")
    added = store.add(txns)
    assert added == 6
    assert store.add(txns) == 0  # re-parse of the same export dedupes cleanly
    got = store.query(datetime.date(2025, 11, 1), datetime.date(2025, 11, 30))
    assert len([t for t in got if "COFFEE SHOP" in t.description]) == 2
    store.close()
