"""Barclays provider: balances only (no transaction download).

Ported from ``~/dot-files/scripts/barclays``. :func:`parse_scrape_rows` is the
pure core separated out for testing.
"""

from __future__ import annotations

import time

from selenium.webdriver.common.by import By

from ..base import Capability, Provider
from ..browser import ordinal_number
from ..credentials import CredentialField
from ..errors import LoginError, ScrapeError
from ..models import Account
from ..money import parse_money
from ..registry import register

LOGIN_URL = "https://bank.barclays.co.uk/olb/authlogin/loginAppContainer.do"
LOGIN_TIMEOUT = 180
MEM_INPUT_CSS = "input[id^='memorableCharacters-input-']"

# Find each account's name + balance on the home page. Balances render inside
# web components, so walk shadow roots; the name is the nearest heading above
# the £ amount (climbing across shadow boundaries).
SCRAPE_ACCOUNTS_JS = r"""
const moneyRe = /^-?£[\d,]+\.\d{2}$/;
const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
const deep = (sel, root = document, acc = []) => {
  root.querySelectorAll(sel).forEach(e => acc.push(e));
  root.querySelectorAll('*').forEach(e => { if (e.shadowRoot) deep(sel, e.shadowRoot, acc); });
  return acc;
};
const out = [];
deep('*').forEach(e => {
  const own = Array.from(e.childNodes).filter(n => n.nodeType === 3)
    .map(n => n.textContent.trim()).join('').trim();
  if (!moneyRe.test(own) || !vis(e)) return;
  let node = e, name = '';
  for (let i = 0; i < 10 && node && !name; i++) {
    const h = node.querySelector && node.querySelector('h1,h2,h3,h4');
    if (h) name = h.textContent.trim();
    node = node.parentElement || (node.getRootNode() && node.getRootNode().host) || null;
  }
  if (name) out.push({name: name, balance: own});
});
return out;
"""


def parse_scrape_rows(rows: list[dict]) -> list[Account]:
    """De-duplicate by name (keeping the first, the account tile) and parse."""
    seen: set[str] = set()
    accounts: list[Account] = []
    for r in rows:
        name = r.get("name", "")
        if name in seen:
            continue
        seen.add(name)
        accounts.append(Account(name=name, balance=parse_money(r.get("balance"))))
    return accounts


@register
class BarclaysProvider(Provider):
    name = "barclays"
    display_name = "Barclays"
    login_url = LOGIN_URL
    capabilities = frozenset({Capability.BALANCES})
    credential_fields = [
        CredentialField("surname", "Surname"),
        CredentialField("membership_number", "Membership number"),
        CredentialField("passcode", "Passcode (5 digits)", secret=True, pattern=r"\d{5}"),
        CredentialField("memorable_word", "Memorable word", secret=True),
    ]

    # -- login ------------------------------------------------------------
    def login(self, browser, creds: dict, on_status=None) -> None:
        self._login_step1(browser, creds)
        # Wait for step 1 to give way to the auth step (or surface an error).
        browser.wait_for(
            lambda: "identification" not in browser.current_url
            or browser.driver.find_elements(By.CSS_SELECTOR, "input[type='password']")
            or browser.page_errors(), 60)
        browser.wait_for(
            lambda: browser.driver.find_elements(By.ID, "passcode")
            or browser.page_errors(), 45)
        time.sleep(1)
        self._login_step2(browser, creds)

    def _login_step1(self, browser, creds: dict) -> None:
        browser.get(self.login_url)
        field = browser.wait_for(
            lambda: browser.driver.find_elements(By.ID, "surnameMem"), 60)
        if not field:
            raise LoginError(f"Login page did not load (at {browser.current_url})")
        browser.wait_for(lambda: browser.dismiss_cookie_banner(), 10)
        # Ensure the 'Membership number' tab is selected.
        for b in browser.driver.find_elements(By.ID, "loginTypeSelection_tab_button_0"):
            if b.is_displayed():
                browser.driver.execute_script("arguments[0].click()", b)
                break
        browser.fill(browser.driver.find_element(By.ID, "surnameMem"),
                     creds["surname"], fire_events=True)
        browser.fill(browser.driver.find_element(By.ID, "membership0"),
                     creds["membership_number"], fire_events=True)
        time.sleep(1)  # let async field validation settle before submitting
        # Click Continue once and wait patiently: re-clicking cancels the
        # in-flight submit. Retry only if still on the identification step.
        for _ in range(3):
            browser.click_id_deep("continue")
            if browser.wait_for(
                    lambda: "identification" not in browser.current_url
                    or browser.page_errors(), 25):
                return

    def _login_step2(self, browser, creds: dict) -> None:
        if not browser.driver.find_elements(By.ID, "passcode"):
            browser.click_text_deep("Passcode and memorable word")
        if not browser.wait_for(
                lambda: browser.driver.find_elements(By.ID, "passcode"), 30):
            raise LoginError("Passcode field did not appear on the authentication step")
        browser.fill(browser.driver.find_element(By.ID, "passcode"),
                     creds["passcode"], fire_events=True)
        filled = browser.fill_positional(
            MEM_INPUT_CSS, ordinal_number, creds["memorable_word"],
            "memorable-word character", fire_events=True, visible_only=True)
        if not filled:
            raise LoginError("No memorable-word character boxes found")
        if not browser.wait_for(lambda: browser.exists_deep("submitAuthentication"), 15):
            raise LoginError("Submit button never appeared on the authentication step")
        for _ in range(3):
            browser.click_id_deep("submitAuthentication")
            if browser.wait_for(
                    lambda: "authlogin" not in browser.current_url
                    or self._login_error(browser), 25):
                break
        end = time.time() + LOGIN_TIMEOUT
        while time.time() < end:
            if "authlogin" not in browser.current_url:
                return
            if self._login_error(browser):
                raise LoginError("Login rejected: " + " | ".join(browser.page_errors()))
            time.sleep(1)
        raise LoginError(
            f"Timed out after the authentication step (at {browser.current_url})")

    @staticmethod
    def _login_error(browser) -> bool:
        return any("memorable" in e.lower() or "passcode" in e.lower()
                   or "incorrect" in e.lower() or "do not match" in e.lower()
                   for e in browser.page_errors())

    # -- scraping ---------------------------------------------------------
    def scrape_accounts(self, browser) -> list[Account]:
        rows = browser.wait_for(lambda: browser.script(SCRAPE_ACCOUNTS_JS), 60)
        if not rows:
            raise ScrapeError("No account balances found on the home page")
        return parse_scrape_rows(rows)
