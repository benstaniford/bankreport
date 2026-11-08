"""The provider abstraction: one interface every bank implements.

The public API (:mod:`banklib.session`) only ever talks to a :class:`Provider`
through these methods, so adding a bank means writing one subclass — there are
no bank-specific entry points anywhere else.
"""

from __future__ import annotations

import abc
import enum

from .credentials import CredentialField
from .errors import UnsupportedCapability
from .models import Account, DownloadResult


class Capability(enum.Enum):
    """Optional features a provider may support."""

    BALANCES = "balances"
    DOWNLOAD = "download"


class Provider(abc.ABC):
    """Base class for a bank provider.

    Subclasses set the class attributes and implement :meth:`login` and
    :meth:`scrape_accounts`. :meth:`download` is only overridden by providers
    whose ``capabilities`` include :attr:`Capability.DOWNLOAD`.
    """

    #: Short machine name, e.g. ``"natwest"``. Used as the keyring service too.
    name: str = ""
    #: Human-facing name, e.g. ``"NatWest"``.
    display_name: str = ""
    #: Page the login flow starts on.
    login_url: str = ""
    #: Credentials this provider needs (drives CLI prompts and web forms).
    credential_fields: list[CredentialField] = []
    #: Features this provider supports.
    capabilities: frozenset[Capability] = frozenset({Capability.BALANCES})

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities

    # -- flow (implemented per provider) -----------------------------------
    @abc.abstractmethod
    def login(self, browser, creds: dict, on_status=None) -> None:
        """Drive the browser from ``login_url`` to a logged-in state.

        ``on_status`` is an optional ``callable(str)`` used to report
        intermediate steps (e.g. waiting for app approval).
        """

    @abc.abstractmethod
    def scrape_accounts(self, browser) -> list[Account]:
        """Return the account tiles from the post-login summary page."""

    def download(self, browser, accounts: list[str] | None,
                 download_dir) -> DownloadResult:
        """Download transactions. Default: declare the capability unsupported."""
        raise UnsupportedCapability(
            f"{self.display_name or self.name} does not support transaction downloads")

    # -- shared default ----------------------------------------------------
    def logout(self, browser) -> None:
        """Best-effort logout by clicking a Log out / Logout control."""
        import time

        try:
            if browser.click_text_deep("Logout") or browser.click_text_deep("Log out"):
                time.sleep(2)
        except Exception:
            pass
