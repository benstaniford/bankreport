import pytest

from banklib.errors import ScrapeError
from banklib.providers.barclays import BarclaysProvider, parse_scrape_rows


# -- parse_scrape_rows ------------------------------------------------------

def test_parse_scrape_rows_basic():
    rows = [
        {"name": "Current Account", "balance": "£2,500.00"},
        {"name": "Savings", "balance": "-£10.00"},
    ]
    accounts = parse_scrape_rows(rows)
    assert [a.name for a in accounts] == ["Current Account", "Savings"]
    assert accounts[0].balance == 2500.00
    assert accounts[1].balance == -10.00


def test_parse_scrape_rows_dedupes_by_name():
    rows = [
        {"name": "Current Account", "balance": "£2,500.00"},   # tile
        {"name": "Current Account", "balance": "£2,500.00"},   # repeated elsewhere
    ]
    accounts = parse_scrape_rows(rows)
    assert len(accounts) == 1


# -- scrape_accounts through a fake browser ---------------------------------

def test_scrape_accounts(make_browser):
    rows = [{"name": "Current Account", "balance": "£2,500.00"}]
    browser = make_browser(scripts=[("moneyRe", rows)])
    accounts = BarclaysProvider().scrape_accounts(browser)
    assert accounts[0].name == "Current Account"
    assert accounts[0].balance == 2500.00


def test_scrape_accounts_errors_when_empty(make_browser):
    browser = make_browser(scripts=[("moneyRe", [])])
    browser.wait_for = lambda fn, timeout, interval=0.5: fn()
    with pytest.raises(ScrapeError):
        BarclaysProvider().scrape_accounts(browser)
