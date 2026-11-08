"""Firefox/Selenium plumbing shared by every provider.

:func:`find_firefox` and :func:`make_driver` are merged, verbatim in behaviour,
from the two original scripts. :class:`Browser` wraps a driver and exposes the
DOM helpers that both scripts previously copy-pasted (cookie dismissal, deep
clicks across shadow roots, positional-secret entry, error/heading scraping).

Providers are handed a :class:`Browser`, never a raw driver, which is what lets
the tests drive them with a fake.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Callable

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By

from .errors import BrowserError

# -- position parsers (shared by PIN-digit and memorable-word entry) --------


def leading_number(label: str) -> int | None:
    """``"3rd number"`` / ``"2"`` -> the leading integer (NatWest labels)."""
    m = re.match(r"\s*(\d+)", label or "")
    return int(m.group(1)) if m else None


_ORDINAL_RE = re.compile(r"(\d+)\s*(?:st|nd|rd|th)", re.I)


def ordinal_number(label: str) -> int | None:
    """``"2nd character"`` -> ``2`` (Barclays memorable-word labels)."""
    m = _ORDINAL_RE.search(label or "")
    return int(m.group(1)) if m else None


# -- Firefox discovery / driver construction --------------------------------


def find_firefox() -> str | None:
    """Locate the Firefox binary across platforms.

    Selenium Manager finds it on PATH, but Firefox is rarely on PATH on
    Windows/macOS, so the usual install locations are checked too.
    """
    names = ["firefox", "firefox-esr"]
    paths = [shutil.which(n) for n in names]
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles"),
                     os.environ.get("ProgramFiles(x86)")):
            if base:
                paths.append(os.path.join(base, "Mozilla Firefox", "firefox.exe"))
    elif sys.platform == "darwin":
        paths.append("/Applications/Firefox.app/Contents/MacOS/firefox")
    else:  # Linux
        paths += ["/usr/bin/firefox", "/snap/bin/firefox",
                  "/usr/lib/firefox/firefox"]
    for p in paths:
        if p and Path(p).exists():
            return p
    return None


def make_driver(headless: bool = True, profile_dir: Path | None = None,
                download_dir: Path | None = None):
    """Build a Firefox WebDriver with an optional persistent profile.

    A persistent profile keeps the bank's device-recognition cookies, which
    makes extra verification steps less likely on later runs. Download prefs are
    only applied when ``download_dir`` is given (providers without a download
    capability never need them).
    """
    # Stop Selenium Manager sending anonymous usage stats to plausible.io, which
    # otherwise logs a noisy warning when the network blocks the request.
    os.environ.setdefault("SE_AVOID_STATS", "true")
    opts = webdriver.FirefoxOptions()
    if headless:
        opts.add_argument("-headless")
    firefox = find_firefox()
    if firefox:
        opts.binary_location = firefox
    if profile_dir is not None:
        profile_dir = Path(profile_dir)
        profile_dir.mkdir(parents=True, exist_ok=True)
        opts.add_argument("-profile")
        opts.add_argument(str(profile_dir))
    if download_dir is not None:
        download_dir = Path(download_dir)
        download_dir.mkdir(parents=True, exist_ok=True)
        opts.set_preference("browser.download.folderList", 2)
        opts.set_preference("browser.download.dir", str(download_dir))
        opts.set_preference("browser.download.useDownloadDir", True)
        opts.set_preference(
            "browser.helperApps.neverAsk.saveToDisk",
            "text/csv,application/csv,application/vnd.ms-excel,"
            "application/octet-stream,text/plain,application/download")
    try:
        driver = webdriver.Firefox(options=opts)
    except WebDriverException as e:
        raise BrowserError(f"Could not start Firefox: {e}") from e
    driver.set_window_size(1280, 1000)
    return driver


# -- generic DOM helpers (shared JS) ----------------------------------------

# Click a button-like element (incl. custom elements and those in shadow roots)
# whose visible text matches exactly.
_CLICK_TEXT_DEEP_JS = r"""
const want = arguments[0].trim().toLowerCase();
const clickable = el => {
  const tag = el.tagName.toLowerCase();
  return tag === 'button' || tag === 'a' || el.getAttribute('role') === 'button' || tag.includes('button');
};
function walk(root) {
  for (const el of root.querySelectorAll('*')) {
    if (clickable(el) && (el.innerText || el.textContent || '').trim().toLowerCase() === want) {
      el.scrollIntoView({block: 'center'}); el.click(); return true;
    }
  }
  for (const el of root.querySelectorAll('*')) { if (el.shadowRoot && walk(el.shadowRoot)) return true; }
  return false;
}
return walk(document);
"""

# Click an element by id, descending into shadow roots if the light DOM lacks it.
_CLICK_ID_DEEP_JS = r"""
const id = arguments[0];
function find(root) {
  const hit = root.getElementById ? root.getElementById(id) : root.querySelector('#' + CSS.escape(id));
  if (hit) return hit;
  for (const el of root.querySelectorAll('*')) {
    if (el.shadowRoot) { const h = find(el.shadowRoot); if (h) return h; }
  }
  return null;
}
const el = find(document);
if (!el) return false;
el.scrollIntoView({block: 'center'}); el.click(); return true;
"""

_EXISTS_DEEP_JS = r"""
const id=arguments[0];
const f=r=>{let h=r.getElementById?r.getElementById(id):r.querySelector('#'+CSS.escape(id));
if(h)return h;for(const e of r.querySelectorAll('*')){if(e.shadowRoot){const x=f(e.shadowRoot);if(x)return x;}}return null;};
return !!f(document);
"""

# Dismiss the cookie banner. Handles NatWest's OneTrust buttons (light DOM) and
# Barclays' <tealium-consent> web component (shadow DOM), preferring to reject.
_DISMISS_COOKIES_JS = r"""
const onetrust = ['#onetrust-reject-all-handler', '#onetrust-accept-btn-handler'];
for (const sel of onetrust) {
  const el = document.querySelector(sel);
  if (el && el.offsetParent !== null) { el.click(); return true; }
}
const wanted = ['reject optional cookies', 'reject all cookies', 'reject all',
                'accept optional cookies', 'accept all cookies', 'accept all'];
function walk(root) {
  for (const el of root.querySelectorAll('button, a, [role=button], tealium-button')) {
    const t = (el.innerText || el.textContent || '').trim().toLowerCase();
    if (wanted.includes(t)) { el.click(); return true; }
  }
  for (const el of root.querySelectorAll('*')) { if (el.shadowRoot && walk(el.shadowRoot)) return true; }
  return false;
}
return walk(document);
"""


class Browser:
    """Thin convenience wrapper over a Selenium WebDriver.

    Providers reach for ``self.driver`` for raw calls (``get``, ``find_elements``,
    ``execute_script``, ``current_url``) and use the methods here for the logic
    that was duplicated across the original scripts.
    """

    def __init__(self, driver):
        self.driver = driver

    # -- lifecycle ---------------------------------------------------------
    @property
    def current_url(self) -> str:
        return self.driver.current_url

    def get(self, url: str) -> None:
        self.driver.get(url)

    def quit(self) -> None:
        try:
            self.driver.quit()
        except WebDriverException:
            pass

    def script(self, js: str, *args):
        return self.driver.execute_script(js, *args)

    def screenshot(self, path) -> bool:
        try:
            return bool(self.driver.save_screenshot(str(path)))
        except WebDriverException:
            return False

    # -- waiting -----------------------------------------------------------
    def wait_for(self, fn: Callable, timeout: float, interval: float = 0.5):
        """Poll ``fn`` until it returns truthy or ``timeout`` elapses."""
        end = time.time() + timeout
        while time.time() < end:
            try:
                result = fn()
                if result:
                    return result
            except WebDriverException:
                pass
            time.sleep(interval)
        return None

    # -- clicking ----------------------------------------------------------
    def click_button(self, text: str, css: str = "ibank-button") -> bool:
        """Click the first visible element matching ``css`` whose text equals ``text``."""
        for el in self.driver.find_elements(By.CSS_SELECTOR, css):
            if el.text.strip() == text and el.is_displayed():
                self.driver.execute_script("arguments[0].click()", el)
                return True
        return False

    def click_text_deep(self, text: str) -> bool:
        try:
            return bool(self.driver.execute_script(_CLICK_TEXT_DEEP_JS, text))
        except WebDriverException:
            return False

    def click_id_deep(self, el_id: str) -> bool:
        for el in self.driver.find_elements(By.ID, el_id):
            if el.is_displayed():
                self.driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});arguments[0].click();", el)
                return True
        try:
            return bool(self.driver.execute_script(_CLICK_ID_DEEP_JS, el_id))
        except WebDriverException:
            return False

    def exists_deep(self, el_id: str) -> bool:
        if self.driver.find_elements(By.ID, el_id):
            return True
        try:
            return bool(self.driver.execute_script(_EXISTS_DEEP_JS, el_id))
        except WebDriverException:
            return False

    def dismiss_cookie_banner(self) -> bool:
        try:
            return bool(self.driver.execute_script(_DISMISS_COOKIES_JS))
        except WebDriverException:
            return False

    # -- form entry --------------------------------------------------------
    def fill(self, el, value: str, fire_events: bool = False) -> None:
        """Type ``value`` into ``el``; optionally fire the input/change events
        Angular needs to bind the value."""
        el.clear()
        el.send_keys(value)
        if fire_events:
            self.driver.execute_script(
                "arguments[0].dispatchEvent(new Event('input', {bubbles:true}));"
                "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));"
                "arguments[0].blur();", el)

    def label_text(self, input_id: str) -> str:
        for lab in self.driver.find_elements(By.CSS_SELECTOR, f"label[for='{input_id}']"):
            if lab.text:
                return lab.text
        return ""

    def fill_positional(self, input_css: str, parse_pos: Callable[[str], int | None],
                        secret: str, what: str, *, fire_events: bool = False,
                        visible_only: bool = False) -> int:
        """Fill the "enter character N of your <secret>" boxes.

        Reads each box's requested 1-based position from its ``<label>`` via
        ``parse_pos`` and types the matching character of ``secret``. Returns the
        number of boxes filled. Raises :class:`LoginError` if a label cannot be
        parsed or the bank asks for a position beyond the stored secret.
        """
        from .errors import LoginError

        filled = 0
        for inp in self.driver.find_elements(By.CSS_SELECTOR, input_css):
            if visible_only and not inp.is_displayed():
                continue
            iid = inp.get_attribute("id")
            label = self.label_text(iid)
            pos = parse_pos(label)
            if pos is None:
                raise LoginError(f"Can't parse {what} position from label {label!r}")
            if pos > len(secret):
                raise LoginError(
                    f"Bank asked for {what} position {pos}, but the stored {what} is shorter")
            self.fill(inp, secret[pos - 1], fire_events)
            filled += 1
        return filled

    # -- page state --------------------------------------------------------
    def page_heading(self) -> str:
        try:
            el = self.driver.find_element(By.CSS_SELECTOR, "main h1, main h2, h1")
            return el.text.strip()
        except WebDriverException:
            return ""

    def page_errors(self, selectors: str | None = None) -> list[str]:
        css = selectors or (
            "[role='alert'], .error, .form-field__error, .field-error, "
            "ibank-notification-message")
        msgs = []
        for el in self.driver.find_elements(By.CSS_SELECTOR, css):
            try:
                t = el.text.strip()
                if t and el.is_displayed():
                    msgs.append(t)
            except WebDriverException:
                pass
        return msgs
