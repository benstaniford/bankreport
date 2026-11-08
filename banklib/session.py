"""The one context manager used to drive any provider.

``open_session`` builds the browser, logs in on entry, and logs out + quits on
exit. ``accounts()`` and ``download()`` are the same for every bank; capability
checks live here so a provider that cannot download fails cleanly.
"""

from __future__ import annotations

from pathlib import Path

from .base import Capability
from .browser import Browser, make_driver
from .errors import CredentialsError, UnsupportedCapability
from .models import Account, DownloadResult
from .registry import get_provider


def default_cache_dir(name: str) -> Path:
    return Path.home() / ".cache" / name


class Session:
    """A logged-in browsing session against one provider.

    Use via :func:`open_session`::

        with open_session("natwest", creds) as s:
            for a in s.accounts():
                ...
    """

    def __init__(self, provider, creds: dict, *, headless: bool = True,
                 profile_dir: Path | None = None, download_dir: Path | None = None,
                 on_status=None, driver=None):
        self.provider = provider
        self.creds = creds
        self.headless = headless
        self.on_status = on_status
        cache = default_cache_dir(provider.name)
        self.profile_dir = profile_dir or (cache / "firefox-profile")
        self.download_dir = download_dir or (cache / "downloads")
        self._driver = driver  # injectable for tests
        self.browser: Browser | None = None

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> "Session":
        """Build the browser and log in. Returns self."""
        self._validate_creds()
        driver = self._driver or make_driver(
            headless=self.headless, profile_dir=self.profile_dir,
            download_dir=self.download_dir)
        self.browser = Browser(driver)
        self.provider.login(self.browser, self.creds, on_status=self.on_status)
        return self

    def close(self, logout: bool = True) -> None:
        """Log out (best effort, if requested) and quit the browser."""
        if self.browser is None:
            return
        try:
            if logout:
                self.provider.logout(self.browser)
        finally:
            self.browser.quit()
            self.browser = None

    # -- context manager ---------------------------------------------------
    def __enter__(self) -> "Session":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close(logout=True)

    def _validate_creds(self) -> None:
        missing = [f.name for f in self.provider.credential_fields
                   if not self.creds.get(f.name)]
        if missing:
            raise CredentialsError(
                f"Missing credentials for {self.provider.name}: {', '.join(missing)}")

    # -- operations --------------------------------------------------------
    def accounts(self) -> list[Account]:
        return self.provider.scrape_accounts(self.browser)

    def download(self, accounts: list[str] | None = None, out=None) -> DownloadResult:
        if not self.provider.supports(Capability.DOWNLOAD):
            raise UnsupportedCapability(
                f"{self.provider.display_name or self.provider.name} "
                "does not support transaction downloads")
        result = self.provider.download(self.browser, accounts, self.download_dir)
        if out is not None:
            result = _move_result(result, out)
        return result


def _move_result(result: DownloadResult, out) -> DownloadResult:
    """Move the downloaded CSV to ``out`` (a directory or a full file path)."""
    import os
    import shutil

    saved = result.path
    out_str = str(out)
    dest_dir = Path(out)
    if dest_dir.is_dir() or out_str.endswith(("/", os.sep)):
        dest = dest_dir / saved.name
    elif dest_dir.suffix:  # looks like a filename
        dest = dest_dir
    else:
        dest = dest_dir / saved.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(saved), str(dest))
    return DownloadResult(path=dest, accounts=result.accounts)


def open_session(provider_name: str, creds: dict, *, headless: bool = True,
                 profile_dir: Path | None = None, download_dir: Path | None = None,
                 on_status=None, driver=None) -> Session:
    """Create a :class:`Session` for ``provider_name``.

    The returned object is a context manager; login happens on ``__enter__``.
    """
    provider = get_provider(provider_name)
    return Session(provider, creds, headless=headless, profile_dir=profile_dir,
                   download_dir=download_dir, on_status=on_status, driver=driver)
