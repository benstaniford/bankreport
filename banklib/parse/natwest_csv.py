"""Parse a NatWest "Download transactions" CSV into transactions.

The export (confirmed against a real download) is a standard quoted CSV with the
header::

    Date,Type,Description,Value,Balance,Account Name,Account Number

* ``Date``   — ``DD Mon YYYY`` (year included, so no inference needed).
* ``Value``  — already signed: credits positive, debits negative, matching our
  convention, so it maps straight onto :attr:`Transaction.amount`.
* ``Balance``— running balance after the row; kept as a stable per-row
  discriminator for the cache fingerprint.

Parsing is header-driven (``csv.DictReader``) so a change in column order does
not break it.
"""

from __future__ import annotations

import csv
import datetime
import io

from ..models import Transaction


def _parse_date(value: str | None) -> datetime.date | None:
    if not value:
        return None
    try:
        return datetime.datetime.strptime(value.strip(), "%d %b %Y").date()
    except ValueError:
        return None


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    value = value.strip().replace(",", "")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_natwest_csv(text: str, *, provider: str = "natwest") -> list[Transaction]:
    """Parse the text of a NatWest transactions CSV into transactions."""
    reader = csv.DictReader(io.StringIO(text))
    out: list[Transaction] = []
    for row in reader:
        date = _parse_date(row.get("Date"))
        amount = _to_float(row.get("Value"))
        if date is None or amount is None:
            continue
        out.append(Transaction(
            date=date,
            description=(row.get("Description") or "").strip(),
            amount=amount,
            account=(row.get("Account Name") or "").strip(),
            provider=provider,
            type=(row.get("Type") or "").strip() or None,
            balance=_to_float(row.get("Balance")),
            seq=0,  # balance anchors the fingerprint for NatWest rows
            source="natwest-csv",
            raw=(row.get("Account Number") or "").strip() or None,
        ))
    return out
