import pytest

from banklib.errors import DownloadError
from banklib.providers import natwest
from banklib.providers.natwest import NatWestProvider, parse_tiles, resolve_wanted


# -- parse_tiles ------------------------------------------------------------

def test_parse_tiles_basic():
    tiles = [{
        "name": "Everyday",
        "balance": "£1,234.56",
        "values": [
            "Current Account",
            "Account Number 12345678 Sort Code 01-02-03",
            "Available: £1,200.00",
            "Overdraft limit: £500.00",
        ],
    }]
    (acc,) = parse_tiles(tiles)
    assert acc.name == "Everyday"
    assert acc.balance == 1234.56
    assert acc.type == "Current Account"
    assert acc.account_number == "12345678"
    assert acc.sort_code == "01-02-03"
    assert acc.available == 1200.00
    assert acc.overdraft_limit == 500.00


def test_parse_tiles_skips_tiles_without_balance():
    tiles = [
        {"name": "Loading", "balance": "", "values": []},
        {"name": "Savings", "balance": "£10.00", "values": []},
    ]
    accounts = parse_tiles(tiles)
    assert [a.name for a in accounts] == ["Savings"]


def test_parse_tiles_to_dict_drops_none():
    (acc,) = parse_tiles([{"name": "Pot", "balance": "£5.00", "values": []}])
    assert acc.to_dict() == {"name": "Pot", "balance": 5.00}


# -- resolve_wanted ---------------------------------------------------------

ACCOUNTS = [
    {"id": "cb1", "name": "BILLS", "checked": False},
    {"id": "cb2", "name": "SPENDING", "checked": False},
]


def test_resolve_wanted_all_when_no_filter():
    assert resolve_wanted(ACCOUNTS, None) == ACCOUNTS


def test_resolve_wanted_filters_case_insensitive():
    assert resolve_wanted(ACCOUNTS, ["bill"]) == [ACCOUNTS[0]]


def test_resolve_wanted_no_match_raises():
    with pytest.raises(DownloadError, match="No account matched"):
        resolve_wanted(ACCOUNTS, ["mortgage"])


# -- scrape_accounts through a fake browser ---------------------------------

def test_scrape_accounts(make_browser, monkeypatch):
    monkeypatch.setattr(natwest.time, "sleep", lambda *_: None)
    tiles = [{"name": "Everyday", "balance": "£42.00", "values": ["Current Account"]}]
    browser = make_browser(scripts=[("ibank-account-tile-card", tiles)])
    accounts = NatWestProvider().scrape_accounts(browser)
    assert len(accounts) == 1
    assert accounts[0].balance == 42.00
    assert accounts[0].type == "Current Account"


def test_scrape_accounts_errors_when_empty(make_browser, monkeypatch):
    from banklib.errors import ScrapeError
    monkeypatch.setattr(natwest.time, "sleep", lambda *_: None)
    browser = make_browser(scripts=[("ibank-account-tile-card", [])])
    # Short-circuit the 60s poll: run the predicate once.
    browser.wait_for = lambda fn, timeout, interval=0.5: fn()
    with pytest.raises(ScrapeError):
        NatWestProvider().scrape_accounts(browser)
