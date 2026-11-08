"""Tests for the SQLite transaction cache and its interval math."""

from __future__ import annotations

import datetime

import pytest

from banklib.models import Transaction
from banklib.store import (
    TransactionStore,
    merge_intervals,
    subtract_intervals,
)

D = datetime.date


def txn(date, amount, desc="Shop", account="Card", seq=0, source="s"):
    return Transaction(date=date, description=desc, amount=amount, account=account,
                       provider="test", seq=seq, source=source)


# -- pure interval helpers --------------------------------------------------

def test_merge_adjacent_and_overlapping():
    got = merge_intervals([(D(2026, 1, 1), D(2026, 1, 10)),
                           (D(2026, 1, 11), D(2026, 1, 20)),   # adjacent (next day)
                           (D(2026, 1, 5), D(2026, 1, 7))])    # overlapping
    assert got == [(D(2026, 1, 1), D(2026, 1, 20))]


def test_merge_keeps_gaps_separate():
    got = merge_intervals([(D(2026, 1, 1), D(2026, 1, 5)),
                           (D(2026, 1, 10), D(2026, 1, 15))])
    assert got == [(D(2026, 1, 1), D(2026, 1, 5)),
                   (D(2026, 1, 10), D(2026, 1, 15))]


def test_subtract_nothing_covered():
    req = (D(2026, 1, 1), D(2026, 1, 31))
    assert subtract_intervals(req, []) == [req]


def test_subtract_fully_covered():
    req = (D(2026, 1, 10), D(2026, 1, 20))
    assert subtract_intervals(req, [(D(2026, 1, 1), D(2026, 1, 31))]) == []


def test_subtract_leaves_both_ends():
    req = (D(2026, 1, 1), D(2026, 1, 31))
    gaps = subtract_intervals(req, [(D(2026, 1, 10), D(2026, 1, 20))])
    assert gaps == [(D(2026, 1, 1), D(2026, 1, 9)),
                    (D(2026, 1, 21), D(2026, 1, 31))]


def test_subtract_tail_only():
    # The open-period case: history covered, the recent tail is not.
    req = (D(2026, 1, 1), D(2026, 1, 31))
    gaps = subtract_intervals(req, [(D(2026, 1, 1), D(2026, 1, 25))])
    assert gaps == [(D(2026, 1, 26), D(2026, 1, 31))]


# -- store round-trips ------------------------------------------------------

@pytest.fixture
def store():
    s = TransactionStore("test", db_path=":memory:")
    yield s
    s.close()


def test_add_and_query(store):
    store.add([txn(D(2026, 1, 5), -10.0, "Alpha"),
               txn(D(2026, 1, 6), -20.0, "Beta", seq=1)])
    got = store.query(D(2026, 1, 1), D(2026, 1, 31))
    assert [t.description for t in got] == ["Alpha", "Beta"]


def test_add_is_idempotent_by_fingerprint(store):
    rows = [txn(D(2026, 1, 5), -10.0, "Alpha")]
    assert store.add(rows) == 1
    assert store.add(rows) == 0  # same fingerprint ignored
    assert len(store.query(D(2026, 1, 1), D(2026, 1, 31))) == 1


def test_genuine_duplicates_kept_via_seq(store):
    # Same day/amount/merchant twice is real; distinct seq keeps both.
    store.add([txn(D(2026, 1, 5), -7.0, "Pub", seq=0),
               txn(D(2026, 1, 5), -7.0, "Pub", seq=1)])
    assert len(store.query(D(2026, 1, 1), D(2026, 1, 31))) == 2


def test_query_filters(store):
    store.add([
        txn(D(2026, 1, 5), -10.0, "Groceries Shop"),
        txn(D(2026, 1, 6), -200.0, "Big Spend", seq=1),
        txn(D(2026, 1, 7), 15.0, "Refund", seq=2),
    ])
    assert [t.description for t in store.query(D(2026, 1, 1), D(2026, 1, 31),
                                               search="Shop")] == ["Groceries Shop"]
    assert [t.amount for t in store.query(D(2026, 1, 1), D(2026, 1, 31),
                                          min_amount=0)] == [15.0]
    assert [t.amount for t in store.query(D(2026, 1, 1), D(2026, 1, 31),
                                          max_amount=-100)] == [-200.0]


def test_query_respects_date_window(store):
    store.add([txn(D(2026, 1, 5), -10.0, "In"),
               txn(D(2026, 2, 5), -10.0, "Out", seq=1)])
    got = store.query(D(2026, 1, 1), D(2026, 1, 31))
    assert [t.description for t in got] == ["In"]


def test_query_account_filter(store):
    store.add([txn(D(2026, 1, 5), -10.0, "A", account="Platinum Visa"),
               txn(D(2026, 1, 6), -10.0, "B", account="Current Account", seq=1)])
    got = store.query(D(2026, 1, 1), D(2026, 1, 31), accounts=["Visa"])
    assert [t.description for t in got] == ["A"]


def test_coverage_merges_and_missing_ranges(store):
    store.record_coverage(D(2026, 1, 1), D(2026, 1, 10))
    store.record_coverage(D(2026, 1, 11), D(2026, 1, 20))  # adjacent -> merges
    assert store.coverage() == [(D(2026, 1, 1), D(2026, 1, 20))]
    # A query inside the covered span needs no fetch.
    assert store.missing_ranges(D(2026, 1, 5), D(2026, 1, 15)) == []
    # Extending past it leaves only the tail gap.
    assert store.missing_ranges(D(2026, 1, 5), D(2026, 1, 25)) == [
        (D(2026, 1, 21), D(2026, 1, 25))]


def test_persistence_across_reopen(tmp_path):
    db = tmp_path / "t.db"
    s1 = TransactionStore("test", db_path=db)
    s1.add([txn(D(2026, 1, 5), -10.0, "Alpha")])
    s1.record_coverage(D(2026, 1, 1), D(2026, 1, 31))
    s1.close()
    s2 = TransactionStore("test", db_path=db)
    assert len(s2.query(D(2026, 1, 1), D(2026, 1, 31))) == 1
    assert s2.missing_ranges(D(2026, 1, 1), D(2026, 1, 31)) == []
    s2.close()
