import json

import pytest
from keyring.errors import NoKeyringError

import banklib.credentials as credmod
from banklib.credentials import CredentialField, CredentialStore

FIELDS = [
    CredentialField("customer_number", "Customer number"),
    CredentialField("pin", "PIN", secret=True, pattern=r"\d{4}"),
    CredentialField("password", "Password", secret=True),
]


class FakeKeyring:
    """In-memory keyring stand-in; optionally raises NoKeyringError."""

    def __init__(self, available=True):
        self.available = available
        self.store: dict = {}

    def get_password(self, service, field):
        if not self.available:
            raise NoKeyringError()
        return self.store.get((service, field))

    def set_password(self, service, field, value):
        if not self.available:
            raise NoKeyringError()
        self.store[(service, field)] = value

    def delete_password(self, service, field):
        if not self.available:
            raise NoKeyringError()
        self.store.pop((service, field), None)


# -- field validation -------------------------------------------------------

def test_field_validate_pattern():
    f = CredentialField("pin", "PIN", pattern=r"\d{4}")
    assert f.validate("1234")
    assert not f.validate("123")
    assert not f.validate("12ab")


def test_field_validate_no_pattern_rejects_empty():
    f = CredentialField("surname", "Surname")
    assert f.validate("Smith")
    assert not f.validate("")


# -- keyring backend --------------------------------------------------------

def test_save_and_load_via_keyring(monkeypatch, tmp_path):
    fake = FakeKeyring(available=True)
    monkeypatch.setattr(credmod, "keyring", fake)
    store = CredentialStore("natwest", FIELDS,
                            fallback_file=tmp_path / "creds.json")

    where = store.save({"customer_number": "123", "pin": "4321", "password": "s3cret"})
    assert where == "system keyring"
    assert store.load() == {"customer_number": "123", "pin": "4321", "password": "s3cret"}
    # Nothing leaked to the fallback file.
    assert not (tmp_path / "creds.json").exists()


def test_load_returns_none_when_incomplete(monkeypatch, tmp_path):
    fake = FakeKeyring(available=True)
    fake.store[("natwest", "customer_number")] = "123"  # pin/password missing
    monkeypatch.setattr(credmod, "keyring", fake)
    store = CredentialStore("natwest", FIELDS, fallback_file=tmp_path / "creds.json")
    assert store.load() is None


def test_forget_via_keyring(monkeypatch, tmp_path):
    fake = FakeKeyring(available=True)
    monkeypatch.setattr(credmod, "keyring", fake)
    store = CredentialStore("natwest", FIELDS, fallback_file=tmp_path / "creds.json")
    store.save({"customer_number": "1", "pin": "0000", "password": "p"})
    store.forget()
    assert store.load() is None


# -- fallback file ----------------------------------------------------------

def test_save_and_load_via_fallback(monkeypatch, tmp_path):
    fake = FakeKeyring(available=False)  # no keyring backend
    monkeypatch.setattr(credmod, "keyring", fake)
    path = tmp_path / "sub" / "creds.json"
    store = CredentialStore("natwest", FIELDS, fallback_file=path)

    where = store.save({"customer_number": "9", "pin": "1111", "password": "pw"})
    assert where == str(path)
    assert path.exists()
    assert json.loads(path.read_text())["pin"] == "1111"
    assert store.load()["password"] == "pw"


def test_forget_removes_fallback(monkeypatch, tmp_path):
    fake = FakeKeyring(available=False)
    monkeypatch.setattr(credmod, "keyring", fake)
    path = tmp_path / "creds.json"
    store = CredentialStore("natwest", FIELDS, fallback_file=path)
    store.save({"customer_number": "9", "pin": "1111", "password": "pw"})
    assert path.exists()
    store.forget()
    assert not path.exists()
