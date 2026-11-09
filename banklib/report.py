"""Pure logic for the monthly spending report (``scripts/last-month``).

Everything here is side-effect-free and unit-tested: month-boundary maths,
credit-card-payment detection, the spending/income split, the Claude
categorisation prompt + response parsing, and per-category aggregation. The
orchestration (fetching from the banks, shelling out to ``claude``, rendering the
PDF) lives in the script and in :mod:`banklib.report_pdf`.

Modelling: Barclays is a credit card and NatWest is the current account that pays
it off. The monthly card settlement leaving NatWest is **not** spending — the real
spending is the Barclays purchases it covers — so it is pulled out of the NatWest
outgoings and reported on its own. Transaction ``amount`` is signed (money out is
negative), so "spend" is the magnitude of the negative rows.
"""

from __future__ import annotations

import datetime
import json
import re
from dataclasses import dataclass, field

from .models import Transaction

# -- taxonomy ---------------------------------------------------------------

#: The fixed category vocabulary Claude must choose from.
CATEGORIES = [
    "Groceries",
    "Eating Out",
    "Transport",
    "Fuel",
    "Shopping",
    "Utilities",
    "Housing",
    "Subscriptions",
    "Health",
    "Entertainment",
    "Travel",
    "Cash",
    "Fees",
    "Income",
    "Transfers",
    "Savings",
    "Other",
]

#: Used when nothing is known about a description (no Claude, or a gap).
UNCATEGORISED = "Uncategorised"

#: Categories that are not "spending": money coming in (Income) or moved into the
#: user's own savings. Excluded from the headline spend total but shown separately
#: so the report still balances. Transfers (e.g. payments to other people) DO
#: count as spend.
NON_SPEND_CATEGORIES = frozenset({"Income", "Savings"})

_VALID_CATEGORIES = frozenset(CATEGORIES)

# -- month boundaries -------------------------------------------------------


def last_calendar_month(today: datetime.date) -> tuple[datetime.date, datetime.date]:
    """Inclusive ``(first, last)`` day of the calendar month before ``today``."""
    first_of_this = today.replace(day=1)
    last_of_prev = first_of_this - datetime.timedelta(days=1)
    first_of_prev = last_of_prev.replace(day=1)
    return first_of_prev, last_of_prev


def parse_month(value: str) -> tuple[datetime.date, datetime.date]:
    """Inclusive ``(first, last)`` day of a ``YYYY-MM`` string."""
    year, month = (int(p) for p in value.split("-", 1))
    first = datetime.date(year, month, 1)
    if month == 12:
        last = datetime.date(year, 12, 31)
    else:
        last = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)
    return first, last


# -- credit-card-payment detection -----------------------------------------

#: NatWest rows that settle the Barclaycard. Heuristic on the payee text. The
#: real statement payee is ``B/CARD PLAT VISA`` (Barclaycard Platinum Visa), so
#: ``b/card`` is matched as well as the fuller spellings.
CARD_PAYMENT_RE = re.compile(
    r"barclaycard|barclays\s*card|\bb\s*/?\s*card\b|credit\s*card\s*(payment|bill)",
    re.IGNORECASE)


def is_card_payment(txn: Transaction) -> bool:
    """True if ``txn`` is a current-account payment settling the credit card.

    Only outgoing (negative) rows qualify — an incoming refund that happens to
    mention the card is not a settlement.
    """
    return txn.amount < 0 and bool(CARD_PAYMENT_RE.search(txn.description or ""))


# -- spending / income split ------------------------------------------------


@dataclass
class Breakdown:
    """The result of splitting a month's transactions into reporting buckets.

    ``spending`` holds the signed rows that count towards spend (Barclays
    purchases + NatWest non-card outgoings); refunds (positive rows) stay in so a
    category's net is honest. ``card_payments`` is the NatWest→Barclaycard
    settlement(s), reported separately. ``income`` is the NatWest credits.

    The card settlement also appears on the **Barclays** statement as a payment
    credit; it is dropped (not counted as a negative "spend") so it isn't
    double-represented — the NatWest side is the one we report.
    """

    spending: list[Transaction] = field(default_factory=list)
    card_payments: list[Transaction] = field(default_factory=list)
    income: list[Transaction] = field(default_factory=list)

    @property
    def card_payment_total(self) -> float:
        """Magnitude of the card settlement(s) (a positive number)."""
        return sum(-t.amount for t in self.card_payments)

    @property
    def income_total(self) -> float:
        return sum(t.amount for t in self.income)


def classify(natwest: list[Transaction],
             barclays: list[Transaction]) -> Breakdown:
    """Split NatWest + Barclays transactions into reporting buckets.

    * Barclays purchases/refunds are spending; the statement's payment credit
      (``type == "payment"``, the card being settled) is dropped — it mirrors the
      NatWest card payment and would otherwise cancel out real purchases.
    * NatWest **outgoings** are spending, except the card settlement, which goes
      to ``card_payments``.
    * NatWest **credits** are income.
    """
    b = Breakdown()
    b.spending.extend(t for t in barclays if t.type != "payment")
    for t in natwest:
        if is_card_payment(t):
            b.card_payments.append(t)
        elif t.amount < 0:
            b.spending.append(t)
        else:
            b.income.append(t)
    return b


# -- categorisation prompt / parsing ----------------------------------------


def normalise(description: str) -> str:
    """Stable key for caching/prompting: collapse whitespace, upper-case."""
    return " ".join((description or "").split()).upper()


def unique_descriptions(txns: list[Transaction]) -> list[str]:
    """The distinct normalised descriptions across ``txns``, in first-seen order."""
    seen: dict[str, None] = {}
    for t in txns:
        key = normalise(t.description)
        if key and key not in seen:
            seen[key] = None
    return list(seen)


def build_category_prompt(descriptions: list[str]) -> str:
    """Prompt asking Claude to map each description to ``{category, merchant}``."""
    cats = ", ".join(CATEGORIES)
    listing = "\n".join(f"- {d}" for d in descriptions)
    return (
        "You are categorising UK bank and credit-card transactions.\n"
        "For each transaction description below, identify the merchant and assign "
        "exactly one category.\n\n"
        f"Allowed categories (use these spellings only): {cats}.\n"
        "Guidance: supermarkets -> Groceries; restaurants/cafes/takeaways -> "
        "Eating Out; trains/buses/taxis/parking -> Transport; petrol stations -> "
        "Fuel; streaming/software/memberships -> Subscriptions; salary/refunds/"
        "interest -> Income; moving money to your own savings -> Savings; moving "
        "money between your own accounts or to people -> Transfers; anything you "
        "cannot place -> Other.\n\n"
        "Respond with ONLY a JSON object mapping each exact description (verbatim, "
        'as given) to an object {\"category\": \"...\", \"merchant\": \"...\"}. '
        "No prose, no code fences.\n\n"
        "Descriptions:\n" + listing
    )


def parse_category_response(text: str) -> dict[str, dict]:
    """Parse Claude's JSON reply into ``{NORMALISED_DESC: {category, merchant}}``.

    Tolerant of `````json`` fences and surrounding prose; unknown
    categories are coerced to ``Other``. Returns ``{}`` if nothing parses.
    """
    blob = _extract_json_object(text)
    if blob is None:
        return {}
    try:
        data = json.loads(blob)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict] = {}
    for desc, info in data.items():
        if isinstance(info, dict):
            category = str(info.get("category", "")).strip()
            merchant = str(info.get("merchant", "")).strip()
        else:  # a bare category string
            category = str(info).strip()
            merchant = ""
        if category not in _VALID_CATEGORIES:
            category = "Other"
        out[normalise(desc)] = {"category": category, "merchant": merchant}
    return out


def _extract_json_object(text: str) -> str | None:
    """Pull the outermost ``{...}`` object out of a possibly-fenced reply."""
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    return text[start:end + 1]


# -- aggregation ------------------------------------------------------------


@dataclass
class CategoryTotal:
    category: str
    total: float  # magnitude of spend (positive), net of refunds
    count: int


def category_of(txn: Transaction, categories: dict[str, dict]) -> str:
    """The category assigned to ``txn``'s description, or ``UNCATEGORISED``."""
    info = categories.get(normalise(txn.description))
    if info and info.get("category"):
        return info["category"]
    return UNCATEGORISED


def aggregate_by_category(
    spending: list[Transaction], categories: dict[str, dict]
) -> tuple[list[CategoryTotal], list[CategoryTotal]]:
    """Totals per category, split into ``(spend, non_spend)``.

    Each total is the magnitude of the signed amounts in that category (so a
    refund reduces it), sorted largest first. Non-spend categories
    (:data:`NON_SPEND_CATEGORIES`) are returned separately so the headline spend
    excludes them.
    """
    buckets: dict[str, list[float]] = {}
    for t in spending:
        cat = category_of(t, categories)
        buckets.setdefault(cat, []).append(t.amount)

    spend: list[CategoryTotal] = []
    non_spend: list[CategoryTotal] = []
    for cat, amounts in buckets.items():
        total = sum(-a for a in amounts)  # out is negative -> positive spend
        ct = CategoryTotal(category=cat, total=total, count=len(amounts))
        (non_spend if cat in NON_SPEND_CATEGORIES else spend).append(ct)
    spend.sort(key=lambda c: c.total, reverse=True)
    non_spend.sort(key=lambda c: c.total, reverse=True)
    return spend, non_spend
