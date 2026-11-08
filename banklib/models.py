"""Plain data returned by the library: accounts and download results.

These are provider-agnostic. A provider fills in whatever fields it can scrape
and leaves the rest as ``None``/empty, so a web app can render them uniformly.
"""

from __future__ import annotations

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
