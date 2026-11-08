from pathlib import Path

import pytest

import banklib
from banklib import cli
from banklib.credentials import CredentialStore
from banklib.models import Account, DownloadResult

NAT_CREDS = {"customer_number": "123", "pin": "4321", "password": "pw"}


class _StubBrowser:
    def screenshot(self, path):
        return False


class FakeSession:
    def __init__(self):
        self.browser = _StubBrowser()
        self.closed_with_logout = None
        self.downloaded = None

    def start(self):
        return self

    def accounts(self):
        return [Account("Everyday", 42.0, sort_code="01-02-03",
                        account_number="12345678", overdraft_limit=500.0)]

    def download(self, accounts=None, out=None):
        self.downloaded = (accounts, out)
        return DownloadResult(path=Path("/tmp/statement.csv"), accounts=["BILLS"])

    def close(self, logout=True):
        self.closed_with_logout = logout


@pytest.fixture
def fake_session(monkeypatch):
    session = FakeSession()
    monkeypatch.setattr(cli, "open_session", lambda *a, **k: session)
    monkeypatch.setattr(CredentialStore, "load", lambda self: dict(NAT_CREDS))
    return session


# -- argument parsing / capability gating -----------------------------------

def test_download_flags_present_for_natwest():
    parser = cli.build_parser(banklib.get_provider("natwest"))
    args = parser.parse_args(["--download", "--account", "BILLS"])
    assert args.download is True
    assert args.account == ["BILLS"]


def test_download_flags_absent_for_barclays():
    parser = cli.build_parser(banklib.get_provider("barclays"))
    with pytest.raises(SystemExit):
        parser.parse_args(["--download"])


# -- main() flows -----------------------------------------------------------

def test_main_balances_table(fake_session, capsys):
    rc = cli.main("natwest", [])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Everyday" in out
    assert "£42.00" in out
    assert "01-02-03 12345678" in out
    assert "overdraft £500.00" in out
    assert fake_session.closed_with_logout is True


def test_main_balances_json(fake_session, capsys):
    rc = cli.main("natwest", ["--json"])
    assert rc == 0
    import json
    data = json.loads(capsys.readouterr().out)
    assert data[0]["name"] == "Everyday"
    assert data[0]["balance"] == 42.0


def test_main_download(fake_session, capsys):
    rc = cli.main("natwest", ["--download", "--account", "BILLS"])
    assert rc == 0
    assert fake_session.downloaded == (["BILLS"], None)
    assert "Saved BILLS to" in capsys.readouterr().out


def test_main_error_returns_1(fake_session, monkeypatch, capsys):
    def boom(*a, **k):
        raise banklib.LoginError("nope")
    monkeypatch.setattr(fake_session, "accounts", boom)
    rc = cli.main("natwest", [])
    assert rc == 1
    assert "natwest: nope" in capsys.readouterr().err
    assert fake_session.closed_with_logout is False  # no logout on error


def test_main_forget(monkeypatch, capsys):
    called = {}
    monkeypatch.setattr(CredentialStore, "forget", lambda self: called.setdefault("yes", True))
    rc = cli.main("barclays", ["--forget"])
    assert rc == 0
    assert called["yes"]
    assert "Barclays credentials removed" in capsys.readouterr().out


# -- credential prompting ---------------------------------------------------

def test_prompt_credentials_validates(monkeypatch):
    provider = banklib.get_provider("natwest")
    store = CredentialStore("natwest", provider.credential_fields)
    monkeypatch.setattr(CredentialStore, "save", lambda self, creds: "test-store")
    inputs = iter(["123"])            # customer_number (not secret)
    secrets = iter(["4321", "pw"])    # pin, password
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    monkeypatch.setattr(cli.getpass, "getpass", lambda *a: next(secrets))
    creds = cli.prompt_credentials(provider, store)
    assert creds == NAT_CREDS


def test_prompt_credentials_rejects_bad_pin(monkeypatch):
    provider = banklib.get_provider("natwest")
    store = CredentialStore("natwest", provider.credential_fields)
    monkeypatch.setattr("builtins.input", lambda *a: "123")
    monkeypatch.setattr(cli.getpass, "getpass", lambda *a: "12")  # too short
    with pytest.raises(SystemExit):
        cli.prompt_credentials(provider, store)
