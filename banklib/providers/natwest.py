"""NatWest provider: balances and transaction-CSV download.

Ported from ``~/dot-files/scripts/natwest``. The pure parsing cores
(:func:`parse_tiles`, :func:`resolve_wanted`) are separated from the
browser-driving so they can be unit-tested directly.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from selenium.webdriver.common.by import By

from ..base import Capability, Provider
from ..browser import leading_number
from ..credentials import CredentialField
from ..errors import DownloadError, LoginError, ScrapeError
from ..models import Account, DownloadResult
from ..money import parse_money
from ..registry import register

LOGIN_URL = "https://www.onlinebanking.natwest.com/"
LOGIN_TIMEOUT = 180
PIN_PREFIX = "input_enterthefollowingnumbersfromyourpin-"
PWD_PREFIX = "input_enterthefollowingcharactersfromyourpassword-"

# -- scraping JS ------------------------------------------------------------

TILES_JS = r"""
return [...document.querySelectorAll('ibank-account-tile-card')].map(card => {
  const q = s => { const e = card.querySelector(s); return e ? e.innerText.trim() : ''; };
  const values = [...card.querySelectorAll('.account-tile__value')].map(e => e.innerText.replace(/\s+/g, ' ').trim());
  return {
    name: q('.account-tile__display-name'),
    balance: q('.account-tile__balance'),
    values: values,
  };
});
"""

# -- download JS (account checkboxes live in a shadow root) -----------------

LIST_ACCOUNTS_JS = r"""
const tl = document.querySelector('ibank-table-list');
if (!tl || !tl.shadowRoot) return null;
return [...tl.shadowRoot.querySelectorAll("input[type=checkbox]")].map(cb => {
  const aria = cb.getAttribute('aria-label') || '';
  const m = aria.match(/Account name:\s*([^,]+)/i);
  return {id: cb.id, checked: cb.checked, name: (m ? m[1] : '').trim()};
});
"""

SELECT_ACCOUNTS_JS = r"""
const want = new Set(arguments[0]);
const tl = document.querySelector('ibank-table-list');
tl.shadowRoot.querySelectorAll("input[type=checkbox]").forEach(cb => {
  if (cb.checked !== want.has(cb.id)) {
    (tl.shadowRoot.querySelector(`label[for='${cb.id}']`) || cb).click();
  }
});
return [...tl.shadowRoot.querySelectorAll("input[type=checkbox]")]
  .filter(cb => cb.checked).map(cb => cb.id);
"""

CLICK_DOWNLOAD_JS = r"""
const btn = document.querySelector('ibank-button.download-form__submit-button');
if (!btn) return false;
(btn.querySelector('button') || btn).click();
return true;
"""

CLICK_STATEMENTS_JS = r"""
const a = [...document.querySelectorAll('a[href]')]
  .find(x => (x.getAttribute('href') || '') === '/statements-and-transactions');
if (!a) return false;
a.click();
return true;
"""

CLICK_DOWNLOADS_JS = r"""
const txt = e => (e.innerText || e.textContent || '').trim();
const links = [...document.querySelectorAll('ibank-link, a, button, ibank-button')]
  .filter(x => /download transactions/i.test(txt(x)));
const el = links.find(x => /multiple/i.test(txt(x))) || links[0];
if (!el) return false;
(el.querySelector('a, button') || el).click();
return true;
"""


# -- pure parsing cores -----------------------------------------------------

def parse_tiles(tiles: list[dict]) -> list[Account]:
    """Turn raw tile dicts (from :data:`TILES_JS`) into :class:`Account`s.

    Only tiles whose balance parses as money are kept.
    """
    accounts: list[Account] = []
    for t in tiles:
        if parse_money(t.get("balance")) is None:
            continue
        acc = Account(name=t.get("name", ""), balance=parse_money(t["balance"]))
        for v in t.get("values", []):
            m = re.search(r"Account Number\s*(\d+).*?Sort Code\s*([\d-]+)", v)
            if m:
                acc.account_number, acc.sort_code = m.groups()
            elif v.startswith(("Overdraft limit", "Overdraft remaining", "Available")):
                key = v.split(":")[0].strip().lower().replace(" ", "_")
                amount = parse_money(v)
                if key == "available":
                    acc.available = amount
                elif key == "overdraft_limit":
                    acc.overdraft_limit = amount
                else:
                    acc.extras[key] = amount
            elif "£" not in v and v not in ("Select Account",):
                if acc.type is None:
                    acc.type = v
        accounts.append(acc)
    return accounts


def resolve_wanted(accounts: list[dict], account_filter: list[str] | None) -> list[dict]:
    """Pick the checkbox entries to download. No filter -> all accounts."""
    if not account_filter:
        return accounts
    wanted = [a for a in accounts
              if any(f.lower() in a["name"].lower() for f in account_filter)]
    if not wanted:
        names = ", ".join(a["name"] for a in accounts)
        raise DownloadError(f"No account matched {account_filter}. Available: {names}")
    return wanted


# -- provider ---------------------------------------------------------------

@register
class NatWestProvider(Provider):
    name = "natwest"
    display_name = "NatWest"
    login_url = LOGIN_URL
    capabilities = frozenset({Capability.BALANCES, Capability.DOWNLOAD})
    credential_fields = [
        CredentialField("customer_number", "Customer number"),
        CredentialField("pin", "PIN (4 digits)", secret=True, pattern=r"\d{4}"),
        CredentialField("password", "Password", secret=True),
    ]

    # -- login ------------------------------------------------------------
    def login(self, browser, creds: dict, on_status=None) -> None:
        browser.get(self.login_url)

        state = browser.wait_for(
            lambda: ("cust" if browser.driver.find_elements(By.ID, "input_customerNumber")
                     else "pin" if browser.driver.find_elements(
                         By.CSS_SELECTOR, f"input[id^='{PIN_PREFIX}']")
                     else None),
            60,
        )
        if not state:
            raise LoginError(
                f"Login page did not load (at {browser.current_url}, "
                f"heading {browser.page_heading()!r})")
        browser.dismiss_cookie_banner()

        if state == "cust":
            field = browser.driver.find_element(By.ID, "input_customerNumber")
            field.clear()
            field.send_keys(creds["customer_number"])
            if not browser.click_button("Continue"):
                raise LoginError("Couldn't find Continue button on login step 1")

        if not browser.wait_for(
                lambda: browser.driver.find_elements(
                    By.CSS_SELECTOR, f"input[id^='{PIN_PREFIX}']"), 60):
            errs = browser.page_errors()
            raise LoginError("Login step 2 did not appear"
                             + (f": {errs}" if errs else ""))
        self._submit_security_step(browser, creds)

        announced = None
        end = time.time() + LOGIN_TIMEOUT
        while time.time() < end:
            if "account-summary" in browser.current_url:
                return
            if browser.driver.find_elements(By.CSS_SELECTOR, f"input[id^='{PIN_PREFIX}']"):
                errs = browser.page_errors()
                if errs:
                    raise LoginError("Login rejected: " + " | ".join(errs))
            heading = browser.page_heading()
            if heading and heading != announced and "Login step 2" not in heading:
                if on_status:
                    on_status(f"Waiting at: {heading} (approve in the NatWest app if prompted)")
                announced = heading
            time.sleep(1)
        raise LoginError(
            f"Timed out waiting for account summary (at {browser.current_url}, "
            f"heading {browser.page_heading()!r})")

    def _submit_security_step(self, browser, creds: dict) -> None:
        browser.dismiss_cookie_banner()
        time.sleep(0.5)  # let Angular finish wiring the inputs
        browser.fill_positional(f"input[id^='{PIN_PREFIX}']", leading_number,
                                creds["pin"], "PIN")
        browser.fill_positional(f"input[id^='{PWD_PREFIX}']", leading_number,
                                creds["password"], "password")
        if not (browser.click_button("Continue", "ibank-button.navigation__next-button")
                or browser.click_button("Continue")):
            raise LoginError("Couldn't find Continue button on the security step")

    # -- scraping ---------------------------------------------------------
    def scrape_accounts(self, browser) -> list[Account]:
        tiles = browser.wait_for(
            lambda: [t for t in browser.script(TILES_JS)
                     if parse_money(t["balance"]) is not None], 60)
        if not tiles:
            raise ScrapeError("No account balances found on the account summary page")
        time.sleep(1.5)  # balances can arrive slightly after the first tiles render
        tiles = browser.script(TILES_JS)
        return parse_tiles(tiles)

    # -- download ---------------------------------------------------------
    def download(self, browser, accounts: list[str] | None,
                 download_dir) -> DownloadResult:
        download_dir = Path(download_dir)
        download_dir.mkdir(parents=True, exist_ok=True)
        self._goto_downloads(browser)
        listing = browser.script(LIST_ACCOUNTS_JS)
        wanted = resolve_wanted(listing, accounts)

        browser.script(SELECT_ACCOUNTS_JS, [a["id"] for a in wanted])
        # Set these explicitly: on a fresh load the timeframe has no default,
        # which otherwise yields "no transactions to download for this period".
        self._set_mat_select(browser, "select_timeframe", "Last 12 months")
        self._set_mat_select(browser, "select_documentFormat", "CSV")

        before = set(download_dir.glob("*.csv"))
        if not browser.script(CLICK_DOWNLOAD_JS):
            raise DownloadError("Couldn't find the Download button")
        saved = self._wait_for_download(download_dir, before)
        if not saved:
            errs = browser.page_errors()
            raise DownloadError("Download did not produce a CSV"
                                + (f": {errs}" if errs else ""))
        return DownloadResult(path=saved, accounts=[a["name"] for a in wanted])

    def _goto_downloads(self, browser) -> None:
        # SPA navigation; a full load of the deep link triggers an endless
        # step-up challenge, but in-app navigation sails straight through.
        if not browser.wait_for(lambda: browser.script(CLICK_STATEMENTS_JS), 30):
            raise DownloadError("Couldn't find the 'Statements & transactions' link")
        if not browser.wait_for(lambda: browser.script(CLICK_DOWNLOADS_JS), 30):
            raise DownloadError("Couldn't find the 'Download transactions' link")
        if not browser.wait_for(lambda: browser.script(LIST_ACCOUNTS_JS), 60):
            raise DownloadError(
                f"Download page did not load (at {browser.current_url}, "
                f"heading {browser.page_heading()!r})")

    def _set_mat_select(self, browser, select_id: str, want: str) -> None:
        cur = browser.script(
            f"const v=document.querySelector('#{select_id} .mat-mdc-select-value-text');"
            "return v?v.innerText.trim():'';")
        if want.lower() in (cur or "").lower():
            return
        browser.script(f"document.getElementById('{select_id}').click();")
        if not browser.wait_for(
                lambda: browser.script("return document.querySelectorAll('mat-option').length>0"), 10):
            raise DownloadError(f"{select_id} dropdown did not open")
        clicked = browser.script(
            "const want=arguments[0].toLowerCase();"
            "const o=[...document.querySelectorAll('mat-option')]"
            ".find(x=>(x.innerText||'').toLowerCase().includes(want));"
            "if(!o) return false; o.click(); return true;", want)
        if not clicked:
            opts = browser.script(
                "return [...document.querySelectorAll('mat-option')].map(o=>o.innerText.trim());")
            raise DownloadError(f"Option {want!r} not found for {select_id}; options: {opts}")
        time.sleep(0.3)

    def _wait_for_download(self, download_dir: Path, before: set,
                           timeout: float = 90) -> Path | None:
        end = time.time() + timeout
        while time.time() < end:
            csvs = set(download_dir.glob("*.csv")) - before
            partials = list(download_dir.glob("*.part"))
            if csvs and not partials:
                newest = max(csvs, key=lambda p: p.stat().st_mtime)
                size = newest.stat().st_size
                time.sleep(0.5)
                if newest.stat().st_size == size:  # size settled
                    return newest
            time.sleep(0.5)
        return None
