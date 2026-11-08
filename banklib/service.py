"""The transaction API: one symmetric entry point for every provider.

:func:`get_transactions` answers a date-range query from the local cache, only
logging in to the bank for the ranges the cache is missing. It is the same call
for NatWest and Barclays — the differences live entirely in each provider's
``fetch_transactions``.
"""

from __future__ import annotations

import datetime

from .base import Capability
from .errors import UnsupportedCapability
from .models import Transaction
from .registry import get_provider
from .session import open_session
from .store import TransactionStore


def _d(value) -> datetime.date:
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value))


def get_transactions(provider_name: str, creds: dict, start, end, *,
                     search: str | None = None, min_amount: float | None = None,
                     max_amount: float | None = None,
                     accounts: list[str] | None = None, headless: bool = True,
                     on_status=None, store: TransactionStore | None = None,
                     session_factory=open_session) -> list[Transaction]:
    """Return transactions in ``[start, end]`` (inclusive) for ``provider_name``.

    The cache is consulted first; the bank is contacted only for uncovered date
    ranges, and never for a range already fetched. ``session_factory`` is
    injectable for tests.
    """
    start, end = _d(start), _d(end)
    provider = get_provider(provider_name)
    if not provider.supports(Capability.TRANSACTIONS):
        raise UnsupportedCapability(
            f"{provider.display_name or provider.name} does not support transactions")

    own_store = store is None
    if store is None:
        store = TransactionStore(provider_name)
    try:
        gaps = store.missing_ranges(start, end)
        if gaps:
            _fetch_into_store(provider_name, creds, gaps, accounts, store,
                              headless=headless, on_status=on_status,
                              session_factory=session_factory)
        return store.query(start, end, search=search, min_amount=min_amount,
                           max_amount=max_amount, accounts=accounts)
    finally:
        if own_store:
            store.close()


def _fetch_into_store(provider_name, creds, gaps, accounts, store, *,
                      headless, on_status, session_factory) -> None:
    with session_factory(provider_name, creds, headless=headless,
                         on_status=on_status) as session:
        result = session.provider.fetch_transactions(
            session.browser, gaps, accounts, session.download_dir)
    store.add(result.transactions)
    for cov_start, cov_end in result.covered:
        store.record_coverage(cov_start, cov_end)
