"""Plain data returned by the library: accounts and download results.

These are provider-agnostic. A provider fills in whatever fields it can scrape
and leaves the rest as ``None``/empty, so a web app can render them uniformly.
"""

from __future__ import annotations

import datetime
import hashlib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Account:
    """A single account tile scraped from a bank's summary page."""

    name: str
    balance: float | None = None
    type: str | None = None
    account_number: str | None = None
    sort_code: str | None = None
    available: float | None = None
    overdraft_limit: float | None = None
    # Anything provider-specific that does not fit the common fields above.
    extras: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """JSON-friendly dict with ``None``/empty fields dropped."""
        out: dict = {"name": self.name}
        for key in ("balance", "type", "account_number", "sort_code",
                    "available", "overdraft_limit"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        out.update(self.extras)
        return out


@dataclass
class DownloadResult:
    """The outcome of a transaction download."""

    path: Path
    accounts: list[str]

    def to_dict(self) -> dict:
        return {"file": str(self.path), "accounts": self.accounts}


@dataclass
class Transaction:
    """A single posted transaction, provider-agnostic.

    ``amount`` is signed from the account holder's perspective: money **out**
    (card purchases, current-account debits) is negative; money **in** (refunds,
    credits, incoming payments) is positive. So "spend" over a period is the
    magnitude of the negative amounts.

    ``seq`` is the transaction's position within the source it came from (a
    statement or CSV). It keeps genuinely distinct same-day/same-amount/
    same-merchant transactions apart in the cache, where they would otherwise
    collapse to one row.
    """

    date: datetime.date
    description: str
    amount: float
    account: str
    provider: str
    type: str | None = None
    raw: str | None = None
    seq: int = 0
    #: Identifier of the source document (e.g. a statement period), folded into
    #: the fingerprint so the same row re-parsed from the same source dedupes,
    #: but the same row seen in two different sources does not silently vanish.
    source: str | None = None
    #: Running account balance after this transaction, when the source provides
    #: one (NatWest CSV). It uniquely and *stably* distinguishes otherwise
    #: identical same-day rows across re-downloads, so it anchors the fingerprint
    #: when present; sources without it (Barclays statements) fall back to ``seq``.
    balance: float | None = None

    def fingerprint(self) -> str:
        """Stable dedupe key for the cache (``INSERT OR IGNORE`` primary key)."""
        discriminator = f"bal={self.balance:.2f}" if self.balance is not None else f"seq={self.seq}"
        parts = (self.provider, self.account, self.date.isoformat(),
                 f"{self.amount:.2f}", " ".join(self.description.split()).lower(),
                 self.source or "", discriminator)
        return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        """JSON-friendly dict with ``None``/empty fields dropped."""
        out: dict = {
            "date": self.date.isoformat(),
            "description": self.description,
            "amount": self.amount,
            "account": self.account,
            "provider": self.provider,
        }
        if self.type is not None:
            out["type"] = self.type
        if self.balance is not None:
            out["balance"] = self.balance
        return out


@dataclass
class FetchResult:
    """What a provider returns from :meth:`Provider.fetch_transactions`.

    ``transactions`` is everything parsed (the provider may return more than was
    asked for — e.g. NatWest only exports a whole 12-month CSV). ``covered`` lists
    the inclusive date intervals that were fetched from a **finalised** source and
    are safe to cache permanently; the current open period (today's not-yet-settled
    days, or Barclays' online "recent" rows) is deliberately left out so it keeps
    refreshing.
    """

    transactions: list["Transaction"] = field(default_factory=list)
    covered: list[tuple[datetime.date, datetime.date]] = field(default_factory=list)
