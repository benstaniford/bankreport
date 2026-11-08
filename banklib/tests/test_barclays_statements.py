"""Tests for the Barclays statement-list / recent-activity pure helpers.

Uses real row-context strings captured from the live statements page.
"""

from __future__ import annotations

import datetime

from banklib.providers.barclays import (
    parse_recent_rows,
    parse_statement_list,
    resolve_recent_date,
    statements_for_range,
)

D = datetime.date

RAW = [
    {"id": "statement-0-download-btn",
     "ctx": "November 2026 Statement Selection View November 2026 Statement "
            "Transaction Date: From Date and To Date: 03 November 2026 Search"},
    {"id": "statement-1-download-btn",
     "ctx": "October 2026 Statement Selection Unread statement View October 2026 "
            "Statement Transaction Date: From Date and To Date: 02 October 2026 S"},
    {"id": "statement-7-download-btn",
     "ctx": "Download Annual BarclayCard Statement_02-APR-26 03040333"},
    {"id": "statement-12-download-btn",
     "ctx": "December 2025 Statement Selection Unread statement View December 2025 "
            "Statement Transaction Date: From Date and To Date: 02 December 2025"},
]


def test_parse_statement_list_skips_annual():
    got = parse_statement_list(RAW)
    assert got == [
        ("statement-0-download-btn", D(2026, 11, 3)),
        ("statement-1-download-btn", D(2026, 10, 2)),
        ("statement-12-download-btn", D(2025, 12, 2)),
    ]


def test_statements_for_range_selects_covering_statement():
    stmts = parse_statement_list(RAW)
    # October transactions live on the November statement (issued 03 Nov).
    got = statements_for_range(stmts, D(2026, 10, 5), D(2026, 10, 20))
    assert got == ["statement-0-download-btn"]


def test_statements_for_range_includes_boundary_statement():
    stmts = parse_statement_list(RAW)
    got = statements_for_range(stmts, D(2026, 10, 1), D(2026, 10, 31))
    assert "statement-0-download-btn" in got
    assert "statement-1-download-btn" in got  # Oct 2 issue is within start-2..end+35


def test_statements_for_range_excludes_far_statements():
    stmts = parse_statement_list(RAW)
    got = statements_for_range(stmts, D(2026, 10, 1), D(2026, 10, 31))
    assert "statement-12-download-btn" not in got


def test_resolve_recent_date():
    today = D(2026, 11, 8)
    assert resolve_recent_date("Today", today) == today
    assert resolve_recent_date("Yesterday", today) == D(2026, 11, 7)
    assert resolve_recent_date("3 November", today) == D(2026, 11, 3)
    assert resolve_recent_date("28 December", today) == D(2025, 12, 28)  # rolls back a year
    assert resolve_recent_date("05 Nov 2026", today) == D(2026, 11, 5)
    assert resolve_recent_date("Tuesday, 3 Nov", today) == D(2026, 11, 3)  # full-list header
    assert resolve_recent_date("Friday, 6 Nov", today) == D(2026, 11, 6)


def test_parse_recent_rows_signs_and_after_filter():
    today = D(2026, 11, 8)
    rows = [
        {"merchant": "The Local Zone", "date": "Today", "amount": "£5.23", "pending": True},
        {"merchant": "Netflix", "date": "Yesterday", "amount": "£13.99", "pending": True},
        {"merchant": "Old Shop", "date": "01 November", "amount": "£9.00", "pending": False},
    ]
    got = parse_recent_rows(rows, account="Platinum Visa", today=today,
                            after=D(2026, 11, 2))
    # The 01 Nov row is on/before the statement boundary -> dropped.
    assert [t.description for t in got] == ["The Local Zone", "Netflix"]
    assert all(t.amount < 0 for t in got)              # treated as spend
    assert got[0].amount == -5.23
    assert got[0].source == "barclays-online"
    assert got[0].provider == "barclays"
    assert got[0].account == "Platinum Visa"
