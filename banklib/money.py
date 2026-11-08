"""Parsing and formatting of UK-style currency strings (e.g. ``-£1,234.56``).

Unified from the duplicated ``MONEY``/``to_float``/``fmt`` helpers in the two
original scripts.
"""

from __future__ import annotations

import re

MONEY_RE = re.compile(r"-?£[\d,]+\.\d{2}")


def parse_money(text: str | None) -> float | None:
    """Extract the first currency amount from ``text`` as a float.

    Returns ``None`` when there is no amount to find, so callers can filter out
    tiles/rows that have not finished rendering their balance.
    """
    if not text:
        return None
    m = MONEY_RE.search(text)
    if not m:
        return None
    return float(m.group(0).replace("£", "").replace(",", ""))


def format_money(value: float | None) -> str:
    """Render a float back as ``£1,234.56`` / ``-£1,234.56``.

    ``None`` renders as an empty string so tables can show a blank cell.
    """
    if value is None:
        return ""
    if value < 0:
        return f"-£{abs(value):,.2f}"
    return f"£{value:,.2f}"
