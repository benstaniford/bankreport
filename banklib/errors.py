"""Exception hierarchy for banklib.

The library raises these instead of printing or calling sys.exit, so that both
the CLI and a future web backend can decide how to surface failures.
"""

from __future__ import annotations


class BankError(Exception):
    """Base class for every error banklib raises."""


class UnknownProviderError(BankError):
    """Asked for a provider name that is not registered."""


class CredentialsError(BankError):
    """Credentials are missing, incomplete, or fail a provider's validation."""


class LoginError(BankError):
    """The provider could not complete login (bad details, timeout, rejected)."""


class ScrapeError(BankError):
    """The expected data (balances, account list) was not found on the page."""


class DownloadError(BankError):
    """A transaction download was requested but did not produce a file."""


class UnsupportedCapability(BankError):
    """A capability (e.g. transaction download) the provider does not offer."""


class BrowserError(BankError):
    """Firefox could not be located or started."""
