"""Tests for the transaction service: cache-first, fetch only missing ranges."""

from __future__ import annotations

import datetime

import pytest

from banklib import registry
from banklib.base import Capability, Provider
from banklib.errors import UnsupportedCapability
from banklib.models import FetchResult, Transaction
from banklib.registry import get_provider
from banklib.service import get_transactions
from banklib.store import TransactionStore

D = datetime.date


class _FakeTxnProvider(Provider):
    name = "faketxn"
    display_name = "FakeTxn"
    capabilities = frozenset({Capability.TRANSACTIONS})
    credential_fields = []
    calls: list = []

    def login(self, *a, **k):
        pass

    def scrape_accounts(self, *a, **k):
        return []

    def fetch_transactions(self, browser, ranges, accounts, download_dir):
        type(self).calls.append(list(ranges))
        txns = [Transaction(date=D(2026, 1, 5), description="Shop", amount=-10.0,
                            account="Card", provider=self.name, seq=0, source="x")]
        return FetchResult(transactions=txns, covered=[(s, e) for s, e in ranges])


class _FakeNoTxnProvider(Provider):
    name = "fakenotxn"
    display_name = "FakeNoTxn"
    capabilities = frozenset({Capability.BALANCES})
    credential_fields = []

    def login(self, *a, **k):
        pass

    def scrape_accounts(self, *a, **k):
        return []


@pytest.fixture(autouse=True)
def _register_fakes():
    # Register in the global registry only for these tests, then clean up so
    # other suites (e.g. test_registry) see the real providers only.
    registry._ensure_loaded()
    for cls in (_FakeTxnProvider, _FakeNoTxnProvider):
        registry._REGISTRY[cls.name] = cls()
    yield
    for cls in (_FakeTxnProvider, _FakeNoTxnProvider):
        registry._REGISTRY.pop(cls.name, None)


class _FakeSession:
    def __init__(self, provider_name):
        self.provider = get_provider(provider_name)
        self.browser = None
        self.download_dir = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _factory(name, creds, headless=True, on_status=None):
    return _FakeSession(name)


@pytest.fixture
def store(tmp_path):
    s = TransactionStore("faketxn", db_path=tmp_path / "t.db")
    yield s
    s.close()


def test_unsupported_provider_raises():
    with pytest.raises(UnsupportedCapability):
        get_transactions("fakenotxn", {}, D(2026, 1, 1), D(2026, 1, 31))


def test_first_query_fetches(store):
    _FakeTxnProvider.calls.clear()
    out = get_transactions("faketxn", {}, D(2026, 1, 1), D(2026, 1, 31),
                           store=store, session_factory=_factory)
    assert [t.description for t in out] == ["Shop"]
    assert len(_FakeTxnProvider.calls) == 1
    assert _FakeTxnProvider.calls[0] == [(D(2026, 1, 1), D(2026, 1, 31))]


def test_covered_range_does_not_refetch(store):
    _FakeTxnProvider.calls.clear()
    get_transactions("faketxn", {}, D(2026, 1, 1), D(2026, 1, 31),
                     store=store, session_factory=_factory)
    # Same range again: fully cached, so no second fetch.
    get_transactions("faketxn", {}, D(2026, 1, 10), D(2026, 1, 20),
                     store=store, session_factory=_factory)
    assert len(_FakeTxnProvider.calls) == 1


def test_only_the_gap_is_fetched(store):
    _FakeTxnProvider.calls.clear()
    get_transactions("faketxn", {}, D(2026, 1, 1), D(2026, 1, 20),
                     store=store, session_factory=_factory)
    get_transactions("faketxn", {}, D(2026, 1, 1), D(2026, 1, 31),
                     store=store, session_factory=_factory)
    # The second call fetches only the uncovered tail.
    assert _FakeTxnProvider.calls[-1] == [(D(2026, 1, 21), D(2026, 1, 31))]


def test_search_filter_passes_through(store):
    _FakeTxnProvider.calls.clear()
    out = get_transactions("faketxn", {}, D(2026, 1, 1), D(2026, 1, 31),
                           search="Nothing", store=store, session_factory=_factory)
    assert out == []
