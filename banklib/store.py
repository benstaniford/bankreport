"""Local SQLite cache of parsed transactions, so a date range is fetched from
the bank at most once.

Two tables per provider:

* ``transactions`` — every parsed row, keyed by :meth:`Transaction.fingerprint`
  and inserted with ``INSERT OR IGNORE`` so re-fetching the same source never
  duplicates.
* ``coverage`` — merged, inclusive date intervals that have been fully fetched
  from *finalised* sources. :meth:`missing_ranges` subtracts these from a
  request so only the gaps are fetched. The current open period (today's
  not-yet-final days) is deliberately never recorded, so it always refreshes.

Coverage is tracked provider-wide, not per-account: NatWest downloads every
account in one CSV and Barclays is a single card, so a fetch covers the whole
provider for that range. Account filtering is a query-time concern only.

The interval helpers (:func:`merge_intervals`, :func:`subtract_intervals`) are
pure and unit-tested directly.
"""

from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path

from .models import Transaction

ONE_DAY = datetime.timedelta(days=1)

Interval = tuple[datetime.date, datetime.date]


def _d(value) -> datetime.date:
    """Coerce an ISO string or date/datetime to a ``date``."""
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value))


def merge_intervals(intervals: list[Interval]) -> list[Interval]:
    """Merge overlapping or adjacent inclusive date intervals, sorted."""
    ordered = sorted(intervals)
    merged: list[Interval] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1] + ONE_DAY:
            prev_start, prev_end = merged[-1]
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def subtract_intervals(request: Interval, covered: list[Interval]) -> list[Interval]:
    """Return the sub-intervals of ``request`` not inside any ``covered`` one."""
    start, end = request
    if start > end:
        return []
    gaps: list[Interval] = []
    cursor = start
    for c_start, c_end in merge_intervals(covered):
        if c_end < cursor or c_start > end:
            continue  # no overlap with the remaining request
        if c_start > cursor:
            gaps.append((cursor, c_start - ONE_DAY))
        if c_end + ONE_DAY > cursor:
            cursor = c_end + ONE_DAY
        if cursor > end:
            return gaps
    if cursor <= end:
        gaps.append((cursor, end))
    return gaps


class TransactionStore:
    """SQLite-backed cache for one provider's transactions and coverage."""

    def __init__(self, provider_name: str, db_path: Path | None = None):
        self.provider_name = provider_name
        if db_path is None:
            db_path = Path.home() / ".cache" / provider_name / "transactions.db"
        self.db_path = Path(db_path)
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                fingerprint TEXT PRIMARY KEY,
                provider    TEXT NOT NULL,
                account     TEXT NOT NULL,
                date        TEXT NOT NULL,
                amount      REAL NOT NULL,
                description TEXT NOT NULL,
                type        TEXT,
                raw         TEXT,
                seq         INTEGER NOT NULL DEFAULT 0,
                source      TEXT,
                balance     REAL
            );
            CREATE INDEX IF NOT EXISTS ix_txn_date ON transactions(date);
            CREATE TABLE IF NOT EXISTS coverage (
                start TEXT NOT NULL,
                end   TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "TransactionStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- writes ------------------------------------------------------------
    def add(self, transactions: list[Transaction]) -> int:
        """Insert transactions, ignoring any already present. Returns rows added."""
        before = self.conn.total_changes
        self.conn.executemany(
            """
            INSERT OR IGNORE INTO transactions
                (fingerprint, provider, account, date, amount, description,
                 type, raw, seq, source, balance)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (t.fingerprint(), t.provider, t.account, t.date.isoformat(),
                 t.amount, t.description, t.type, t.raw, t.seq, t.source, t.balance)
                for t in transactions
            ],
        )
        self.conn.commit()
        return self.conn.total_changes - before

    def record_coverage(self, start, end) -> None:
        """Mark ``[start, end]`` (inclusive) as fetched, merging with existing."""
        start, end = _d(start), _d(end)
        if start > end:
            return
        existing = self.coverage()
        merged = merge_intervals(existing + [(start, end)])
        self.conn.execute("DELETE FROM coverage")
        self.conn.executemany(
            "INSERT INTO coverage (start, end) VALUES (?, ?)",
            [(s.isoformat(), e.isoformat()) for s, e in merged],
        )
        self.conn.commit()

    # -- reads -------------------------------------------------------------
    def coverage(self) -> list[Interval]:
        rows = self.conn.execute("SELECT start, end FROM coverage").fetchall()
        return merge_intervals([(_d(r["start"]), _d(r["end"])) for r in rows])

    def missing_ranges(self, start, end) -> list[Interval]:
        """Sub-ranges of ``[start, end]`` not yet covered — the gaps to fetch."""
        return subtract_intervals((_d(start), _d(end)), self.coverage())

    def query(self, start, end, *, search: str | None = None,
              min_amount: float | None = None, max_amount: float | None = None,
              accounts: list[str] | None = None) -> list[Transaction]:
        """Return cached transactions in ``[start, end]`` matching the filters."""
        sql = ["SELECT * FROM transactions WHERE date BETWEEN ? AND ?"]
        params: list = [_d(start).isoformat(), _d(end).isoformat()]
        if search:
            sql.append("AND description LIKE ?")
            params.append(f"%{search}%")
        if min_amount is not None:
            sql.append("AND amount >= ?")
            params.append(min_amount)
        if max_amount is not None:
            sql.append("AND amount <= ?")
            params.append(max_amount)
        if accounts:
            placeholders = " OR ".join(["account LIKE ?"] * len(accounts))
            sql.append(f"AND ({placeholders})")
            params.extend(f"%{a}%" for a in accounts)
        sql.append("ORDER BY date, seq, rowid")
        rows = self.conn.execute(" ".join(sql), params).fetchall()
        return [self._row_to_txn(r) for r in rows]

    @staticmethod
    def _row_to_txn(r: sqlite3.Row) -> Transaction:
        return Transaction(
            date=_d(r["date"]), description=r["description"], amount=r["amount"],
            account=r["account"], provider=r["provider"], type=r["type"],
            raw=r["raw"], seq=r["seq"], source=r["source"], balance=r["balance"],
        )
