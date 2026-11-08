"""Barclays provider: balances, plus transactions from PDF statements and the
online recent-activity list.

Ported from ``~/dot-files/scripts/barclays``. The pure cores
(:func:`parse_scrape_rows`, :func:`parse_statement_list`,
:func:`statements_for_range`, :func:`parse_recent_rows`) are separated out for
testing; the statement PDFs themselves are parsed by
:mod:`banklib.parse.barclays_pdf`.
"""

from __future__ import annotations

import datetime
import re
import time
from pathlib import Path

from selenium.webdriver.common.by import By

from ..base import Capability, Provider
from ..browser import ordinal_number
from ..credentials import CredentialField
from ..errors import DownloadError, LoginError, ScrapeError
from ..models import Account, FetchResult, Transaction
from ..money import parse_money
from ..parse.barclays_pdf import extract_text, parse_barclays_statement
from ..registry import register

LOGIN_URL = "https://bank.barclays.co.uk/olb/authlogin/loginAppContainer.do"
LOGIN_TIMEOUT = 180
MEM_INPUT_CSS = "input[id^='memorableCharacters-input-']"

# Statements/documents live behind this in-app link; a full URL load triggers a
# step-up re-auth, so we navigate by clicking (like NatWest's SPA nav).
STATEMENTS_HREF = "clddecoup/cloudit"
HOME_HREF = "/olb/balances/digital/btr/home"
RECENT_HREF = "rntransactions"

# Click the first visible anchor whose href contains arguments[0], crossing
# shadow roots. Returns true if one was clicked.
CLICK_HREF_JS = r"""
const sub = arguments[0];
const deep = (root, acc=[]) => { root.querySelectorAll('a').forEach(e=>acc.push(e));
  root.querySelectorAll('*').forEach(e=>{if(e.shadowRoot)deep(e.shadowRoot,acc);}); return acc; };
const a = deep(document).find(x => (x.getAttribute('href')||'').includes(sub) && x.offsetParent !== null);
if (!a) return false; a.scrollIntoView({block:'center'}); a.click(); return true;
"""

# Enumerate the statement list: each `statement-N-download-btn` with the text of
# its surrounding row (which carries "...To Date: DD Month YYYY").
LIST_STATEMENTS_JS = r"""
const deep = (root, acc=[]) => { root.querySelectorAll('*').forEach(e => { acc.push(e); if (e.shadowRoot) deep(e.shadowRoot, acc); }); return acc; };
const out = [];
deep(document).forEach(e => {
  if (!(e.id && /^statement-\d+-download-btn$/.test(e.id))) return;
  let node = e, ctx = '';
  for (let i=0;i<6 && node;i++){ node = node.parentElement;
    if(node){ const t=(node.innerText||'').replace(/\s+/g,' ').trim(); if(t.length>ctx.length) ctx=t; if(ctx.length>60) break; } }
  out.push({id:e.id, ctx:ctx.slice(0,200)});
});
return out;
"""

# Click a statement download button by id.
CLICK_ID_JS = r"""
const el=document.getElementById(arguments[0]);
if(!el) return false; el.scrollIntoView({block:'center'}); el.click(); return true;
"""

# Scrape recent-activity rows. Each transaction is a clickable
# `button._c-panel__pane--interactive` holding "merchant | category | £amount |
# [Pending]". On the "View all transactions" list the date is a group *header*
# above each day's rows; on the overview it sits inside the row. We track the
# header date in document order and fall back to any in-row date. Balance tiles
# and the bill-summary are not interactive buttons, so they are excluded.
RECENT_ROWS_JS = r"""
const MONEY = /^-?£[\d,]+\.\d{2}$/;
const INROW_DATE = /^(Today|Yesterday|\d{1,2}\s+[A-Za-z]{3,}(\s+\d{4})?)$/;
const HEADER = /^(Today|Yesterday|(Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,\s+\d{1,2}\s+[A-Za-z]{3,})$/;
const ownText = e => Array.from(e.childNodes).filter(n=>n.nodeType===3).map(n=>n.textContent.trim()).join('').trim();
const rows = [], seen = new Set();
let curDate = null;
Array.from(document.querySelectorAll('*')).forEach(e => {
  const t = ownText(e);
  const inButton = e.closest && e.closest('button');
  if (t && HEADER.test(t) && !inButton) { curDate = t; return; }
  const cls = (typeof e.className === 'string') ? e.className : '';
  if (e.tagName.toLowerCase() !== 'button' || !/_c-panel__pane--interactive/.test(cls)) return;
  const texts = Array.from(e.querySelectorAll('*')).map(ownText).filter(Boolean);
  const amount = texts.find(x => MONEY.test(x));
  if (!amount) return;
  const inRowDate = texts.find(x => INROW_DATE.test(x));
  const date = inRowDate || curDate;
  if (!date) return;
  const merchant = texts.find(x => !MONEY.test(x) && !INROW_DATE.test(x) && !/^pending$/i.test(x));
  const rec = {merchant: merchant||'', date, amount, pending: texts.some(x=>/pending/i.test(x))};
  const k = JSON.stringify(rec);
  if (!seen.has(k)) { seen.add(k); rows.push(rec); }
});
return rows;
"""

_ISSUE_DATE_RE = re.compile(r"To Date:\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4})")

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


# -- statement list / selection (pure, tested) ------------------------------

def parse_statement_list(raw: list[dict]) -> list[tuple[str, datetime.date]]:
    """Map raw ``{id, ctx}`` rows to ``(button_id, issue_date)``.

    Rows without a parseable "To Date:" issue date (e.g. the annual summary
    statement) are dropped, so only the monthly statements are kept.
    """
    out: list[tuple[str, datetime.date]] = []
    for row in raw:
        m = _ISSUE_DATE_RE.search(row.get("ctx", ""))
        if not m:
            continue
        try:
            issue = datetime.datetime.strptime(m.group(1), "%d %B %Y").date()
        except ValueError:
            continue
        out.append((row["id"], issue))
    return out


def statements_for_range(statements: list[tuple[str, datetime.date]],
                         start: datetime.date, end: datetime.date) -> list[str]:
    """Button ids of statements whose billing period may intersect ``[start, end]``.

    A monthly statement issued on ``I`` covers roughly ``[I - ~31d, I - 1]`` (the
    issue day itself lands on the *next* statement), so it is relevant when
    ``start < I <= end + 35d``. Excluding ``I == start`` avoids re-downloading the
    newest statement on open-period queries, whose gap begins the day after it.
    """
    hi = end + datetime.timedelta(days=35)
    return [sid for sid, issue in statements if start < issue <= hi]


# -- recent-activity parsing (pure, tested) ---------------------------------

_MONTHS_ABBR = {m.lower(): i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}
_RECENT_DATE_RE = re.compile(r"(\d{1,2})\s+([A-Za-z]{3,})(?:\s+(\d{4}))?")


def resolve_recent_date(label: str, today: datetime.date) -> datetime.date | None:
    """Resolve a recent-activity date label to a real date.

    Handles ``Today``/``Yesterday``, ``"3 November"``/``"05 Nov 2026"``, and the
    weekday-prefixed group headers of the full list (``"Tuesday, 3 Nov"``). When
    no year is given, the most recent matching year on or before today is used.
    """
    label = (label or "").strip()
    low = label.lower()
    if low == "today":
        return today
    if low == "yesterday":
        return today - datetime.timedelta(days=1)
    m = _RECENT_DATE_RE.search(label)
    if not m:
        return None
    day = int(m.group(1))
    month = _MONTHS_ABBR.get(m.group(2)[:3].lower())
    if not month:
        return None
    year = int(m.group(3)) if m.group(3) else today.year
    try:
        d = datetime.date(year, month, day)
    except ValueError:
        return None
    if not m.group(3) and d > today:
        d = datetime.date(year - 1, month, day)
    return d


def parse_recent_rows(rows: list[dict], *, account: str, today: datetime.date,
                      after: datetime.date | None = None,
                      provider: str = "barclays") -> list[Transaction]:
    """Turn scraped recent-activity rows into (open-period) transactions.

    Amounts show no sign online, so they are treated as spend (negative); the
    authoritative signs arrive later with the statement. Rows on or before
    ``after`` (already covered by a statement) are skipped to avoid double counting.
    """
    out: list[Transaction] = []
    for i, r in enumerate(rows):
        date = resolve_recent_date(r.get("date", ""), today)
        amount = parse_money(r.get("amount"))
        if date is None or amount is None:
            continue
        if date > today:  # guard against stray future dates (e.g. a due date)
            continue
        if after is not None and date <= after:
            continue
        out.append(Transaction(
            date=date, description=(r.get("merchant") or "").strip(),
            amount=-abs(amount), account=account, provider=provider,
            type="pending" if r.get("pending") else "purchase",
            seq=i, source="barclays-online"))
    return out


@register
class BarclaysProvider(Provider):
    name = "barclays"
    display_name = "Barclays"
    login_url = LOGIN_URL
    capabilities = frozenset({Capability.BALANCES, Capability.TRANSACTIONS})
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

    # -- transactions -----------------------------------------------------
    def fetch_transactions(self, browser, ranges, accounts: list[str] | None,
                           download_dir) -> FetchResult:
        """Download the PDF statements covering the requested ranges, parse them,
        and supplement the open period with the online recent-activity list."""
        if not ranges:
            return FetchResult()
        download_dir = Path(download_dir)
        download_dir.mkdir(parents=True, exist_ok=True)
        span_start = min(r[0] for r in ranges)
        span_end = max(r[1] for r in ranges)
        today = datetime.date.today()

        self._goto_statements(browser)
        statements = parse_statement_list(browser.script(LIST_STATEMENTS_JS) or [])
        if not statements:
            raise DownloadError("Could not read the statement list")
        wanted = statements_for_range(statements, span_start, span_end)

        txns: list[Transaction] = []
        periods: list[tuple[datetime.date, datetime.date]] = []
        account = "Barclaycard"
        for btn_id in wanted:
            path = self._download_statement(browser, btn_id, download_dir)
            if not path:
                continue
            parsed = parse_barclays_statement(extract_text(path))
            txns.extend(parsed.transactions)
            account = parsed.account or account
            if parsed.period_start and parsed.period_end:
                periods.append((parsed.period_start, parsed.period_end))

        covered: list[tuple[datetime.date, datetime.date]] = []
        finalised_end = max((e for _, e in periods), default=None)
        if finalised_end is not None:
            covered = [(span_start, finalised_end)]

        # Open period: anything after the last finalised statement, scraped live
        # and never cached as covered. The boundary is the newest statement's
        # period end (~ its issue date minus a day), known from the list even when
        # this fetch downloaded no statement (an open-period-only query).
        newest_issue = max((d for _, d in statements), default=None)
        boundary = newest_issue - datetime.timedelta(days=1) if newest_issue else None
        if boundary is not None and span_end > boundary:
            recent = self._scrape_recent(browser, account, today, boundary)
            txns.extend(recent)

        return FetchResult(transactions=txns, covered=covered)

    def _goto_statements(self, browser) -> None:
        if not browser.wait_for(lambda: browser.script(CLICK_HREF_JS, STATEMENTS_HREF), 30):
            raise DownloadError("Couldn't find the 'Statements and documents' link")
        if not browser.wait_for(lambda: browser.script(LIST_STATEMENTS_JS), 60):
            raise DownloadError(
                f"Statements page did not load (at {browser.current_url})")

    def _download_statement(self, browser, btn_id: str, download_dir: Path,
                            timeout: float = 60):
        before = set(download_dir.glob("*.pdf"))
        if not browser.script(CLICK_ID_JS, btn_id):
            return None
        end = time.time() + timeout
        while time.time() < end:
            new = set(download_dir.glob("*.pdf")) - before
            partials = list(download_dir.glob("*.part"))
            if new and not partials:
                newest = max(new, key=lambda p: p.stat().st_mtime)
                size = newest.stat().st_size
                time.sleep(0.5)
                if newest.stat().st_size == size:
                    return newest
            time.sleep(0.5)
        return None

    def _scrape_recent(self, browser, account: str, today: datetime.date,
                       after: datetime.date) -> list[Transaction]:
        # Navigate home, then into the card's recent transactions (in-app).
        browser.script(CLICK_HREF_JS, HOME_HREF)
        time.sleep(3)
        if not browser.wait_for(lambda: browser.script(CLICK_HREF_JS, RECENT_HREF), 20):
            return []
        # Open the full "Transaction list by date" so open-period days older than
        # the overview's Today/Yesterday are included (the overview shows only the
        # latest ~10). Already-statemented rows are dropped by the ``after`` filter.
        browser.wait_for(lambda: browser.script(RECENT_ROWS_JS), 30)
        try:
            browser.click_text_deep("View all transactions")
        except Exception:
            pass
        time.sleep(4)
        rows = browser.wait_for(lambda: browser.script(RECENT_ROWS_JS), 30) or []
        return parse_recent_rows(rows, account=account, today=today, after=after)
