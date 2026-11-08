"""banklib — drive UK online banking through a headless browser.

A provider-abstracted library: every bank is a :class:`~banklib.base.Provider`,
reached through one symmetric API, so new banks are added by writing a provider
rather than new top-level functions.

Typical use::

    import banklib

    name = "natwest"
    provider = banklib.get_provider(name)
    store = banklib.CredentialStore(name, provider.credential_fields)
    creds = store.load()

    with banklib.open_session(name, creds, headless=True) as session:
        for account in session.accounts():
            print(account.name, account.balance)
"""

from __future__ import annotations

from .base import Capability, Provider
from .credentials import CredentialField, CredentialStore
from .errors import (
    BankError,
    BrowserError,
    CredentialsError,
    DownloadError,
    LoginError,
    ScrapeError,
    UnknownProviderError,
    UnsupportedCapability,
)
from .models import Account, DownloadResult
from .money import format_money, parse_money
from .registry import get_provider, list_providers, register
from .session import Session, open_session

__all__ = [
    "Account",
    "BankError",
    "BrowserError",
    "Capability",
    "CredentialField",
    "CredentialStore",
    "CredentialsError",
    "DownloadError",
    "DownloadResult",
    "LoginError",
    "Provider",
    "ScrapeError",
    "Session",
    "UnknownProviderError",
    "UnsupportedCapability",
    "format_money",
    "get_provider",
    "list_providers",
    "open_session",
    "parse_money",
    "register",
]

__version__ = "0.1.0"
