"""Parse a Barclaycard monthly PDF statement into transactions.

Barclaycard statements have no machine-readable transaction export, so we parse
the text layer (extracted with :mod:`pypdf`). The layout, confirmed against real
statements, lists each transaction as three logical lines::

    01 Oct                         <- day + abbreviated month, no year
    Just Eat.co.uk Ltd, London     <- merchant (may wrap onto extra lines)
    £42.49                         <- amount, 'CR' suffix for credits/refunds

Transaction runs are interleaved across pages with marketing and summary text;
the anchored date regex (``DD Mon`` exactly) and the requirement that a run end
in an amount line keep that prose out. A lone ``e`` contactless marker between
the merchant and the amount is dropped.

The statement carries only ``DD Mon`` dates, so the year is inferred from the
``issued on <date>`` line: each transaction gets the latest year that keeps it on
or before the issue date (so an October row on a 3 November statement is the same
year, while a December row on a January statement is the previous year).
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..models import Transaction

_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}
_MONTHS_FULL = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], start=1)}

# A transaction date line: exactly "DD Mon" (e.g. "01 Oct"). Anchored so longer
# lines like "03 November 2026" or "Please pay ... 30 November" never match.
_DATE_RE = re.compile(r"^(\d{2}) ([A-Z][a-z]{2})$")
# An amount line: £ then digits, optional 'CR' credit marker, nothing else.
_AMOUNT_RE = re.compile(r"^£([\d,]+\.\d{2})(CR)?$")
_ISSUED_RE = re.compile(r"issued on (\d{1,2}) ([A-Za-z]+) (\d{4})")
_CARD_RE = re.compile(r"Your (.+?) statement")


@dataclass
class ParsedStatement:
    """Everything parsed from one statement PDF."""

    transactions: list[Transaction] = field(default_factory=list)
    issue_date: datetime.date | None = None
    period_start: datetime.date | None = None
    period_end: datetime.date | None = None
    account: str = "Barclaycard"


def extract_text(path: str | Path) -> str:
    """Return the full text layer of a statement PDF, page by page."""
    from pypdf import PdfReader  # imported lazily so the module loads without it

    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _infer_year(day: int, month: int, issue: datetime.date) -> datetime.date:
    """Latest year that keeps ``day/month`` on or before the issue date."""
    candidate = datetime.date(issue.year, month, day)
    if candidate > issue:
        candidate = datetime.date(issue.year - 1, month, day)
    return candidate


def _find_issue_date(text: str) -> datetime.date | None:
    m = _ISSUED_RE.search(text)
    if not m:
        return None
    day, name, year = m.groups()
    month = _MONTHS_FULL.get(name) or _MONTHS.get(name[:3])
    if not month:
        return None
    return datetime.date(int(year), month, int(day))


def _find_account(text: str) -> str:
    m = _CARD_RE.search(text)
    return m.group(1).strip() if m else "Barclaycard"


def parse_barclays_statement(text: str, *, provider: str = "barclays") -> ParsedStatement:
    """Parse the extracted text of one Barclaycard statement."""
    issue = _find_issue_date(text)
    account = _find_account(text)
    source = f"barclays-statement:{issue.isoformat()}" if issue else "barclays-statement"

    result = ParsedStatement(issue_date=issue, account=account)
    if issue is None:
        return result  # can't date the rows without a year anchor

    lines = [ln.strip() for ln in text.split("\n")]
    seq = 0
    i = 0
    n = len(lines)
    while i < n:
        m = _DATE_RE.match(lines[i])
        if not m:
            i += 1
            continue
        day, mon = int(m.group(1)), _MONTHS.get(m.group(2))
        if not mon:
            i += 1
            continue
        # Collect description lines until the amount line (or give up at the
        # next date line / end — a run with no amount is not a transaction).
        desc_parts: list[str] = []
        amount_match = None
        j = i + 1
        while j < n:
            ln = lines[j]
            if not ln or len(ln) <= 1:   # blank or a lone marker like "e"/"o"
                j += 1
                continue
            amount_match = _AMOUNT_RE.match(ln)
            if amount_match:
                break
            if _DATE_RE.match(ln):       # next date with no amount seen -> abort
                break
            desc_parts.append(ln)
            j += 1
        if not amount_match:
            i = j
            continue

        date = _infer_year(day, mon, issue)
        description = " ".join(" ".join(desc_parts).split())
        magnitude = float(amount_match.group(1).replace(",", ""))
        is_credit = bool(amount_match.group(2)) or description.lower().startswith("payment")
        amount = magnitude if is_credit else -magnitude
        kind = ("refund" if amount_match.group(2)
                else "payment" if description.lower().startswith("payment")
                else "purchase")
        result.transactions.append(Transaction(
            date=date, description=description, amount=amount, account=account,
            provider=provider, type=kind,
            raw=f"{lines[i]} | {description} | {lines[j]}", seq=seq, source=source))
        seq += 1
        i = j + 1

    if result.transactions:
        dates = [t.date for t in result.transactions]
        result.period_start, result.period_end = min(dates), max(dates)
    return result
