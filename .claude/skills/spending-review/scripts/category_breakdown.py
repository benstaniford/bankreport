#!/usr/bin/env python3
"""Per-category, per-merchant spending/income breakdown for one month.

Reads the local banklib caches (no bank login, no network): the transaction
SQLite DBs under ``~/.cache/{natwest,barclays}/transactions.db`` and the category
caches under ``~/.cache/banklib/categories.json`` (spending) and
``categories-income.json`` (income). Joins transactions to their cached category
by normalised description and prints totals grouped by category, then by merchant.

This is the raw material for a spending review: it gives you every category's
total, transaction count, and the merchants inside it, so you can assemble the
deep-dive sections. It does NOT guess venue sub-types (pub vs takeaway, corner
shop vs supermarket) — that judgement lives with the model, informed by
``references/merchant-types.md``.

Usage:
    python category_breakdown.py --month 2026-10
    python category_breakdown.py --month 2026-10 --category "Eating Out"
    python category_breakdown.py --month 2026-10 --json
"""
from __future__ import annotations

import argparse
import calendar
import datetime
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

CACHE = Path.home() / ".cache"
SPEND_CACHE = CACHE / "banklib" / "categories.json"
INCOME_CACHE = CACHE / "banklib" / "categories-income.json"
PROVIDERS = ("natwest", "barclays")


def norm(s: str) -> str:
    return " ".join((s or "").split()).upper()


def load_cache(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def month_bounds(month: str) -> tuple[str, str]:
    year, mon = (int(p) for p in month.split("-", 1))
    last = calendar.monthrange(year, mon)[1]
    return f"{year:04d}-{mon:02d}-01", f"{year:04d}-{mon:02d}-{last:02d}"


def collect(start: str, end: str) -> list[dict]:
    """All transactions in [start, end] tagged with their cached category."""
    spend = load_cache(SPEND_CACHE)
    income = load_cache(INCOME_CACHE)
    out: list[dict] = []
    for prov in PROVIDERS:
        db = CACHE / prov / "transactions.db"
        if not db.exists():
            continue
        con = sqlite3.connect(str(db))
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT date, amount, description, type FROM transactions "
            "WHERE date BETWEEN ? AND ?", (start, end)).fetchall()
        con.close()
        for r in rows:
            key = norm(r["description"])
            if r["amount"] > 0:  # money in -> income vocabulary
                info = income.get(key)
            else:
                info = spend.get(key)
            # Barclays statement "payment" credits settle the card; not spend/income.
            if r["amount"] > 0 and r["type"] == "payment" and prov == "barclays":
                continue
            out.append({
                "date": r["date"], "amount": r["amount"],
                "description": r["description"], "provider": prov,
                "category": (info or {}).get("category") or "Uncategorised",
                "merchant": ((info or {}).get("merchant") or r["description"]).strip(),
                "is_income": r["amount"] > 0,
            })
    return out


def group(txns: list[dict]) -> dict:
    """{category: {"total","count","merchants":{m:{"total","count"}}}}."""
    cats: dict = defaultdict(lambda: {"total": 0.0, "count": 0,
                                      "merchants": defaultdict(lambda: {"total": 0.0, "count": 0})})
    for t in txns:
        c = cats[t["category"]]
        c["total"] += t["amount"]
        c["count"] += 1
        m = c["merchants"][t["merchant"]]
        m["total"] += t["amount"]
        m["count"] += 1
    return cats


def to_plain(cats: dict) -> dict:
    return {cat: {"total": round(d["total"], 2), "count": d["count"],
                  "merchants": {m: {"total": round(v["total"], 2), "count": v["count"]}
                                for m, v in d["merchants"].items()}}
            for cat, d in cats.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    today = datetime.date.today()
    prev_end = today.replace(day=1) - datetime.timedelta(days=1)
    ap.add_argument("--month", default=prev_end.strftime("%Y-%m"),
                    metavar="YYYY-MM", help="month to report (default: last month)")
    ap.add_argument("--category", help="limit to one category (case-insensitive)")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args()

    start, end = month_bounds(args.month)
    txns = collect(start, end)
    cats = to_plain(group(txns))

    if args.category:
        want = args.category.lower()
        cats = {c: d for c, d in cats.items() if c.lower() == want}

    if args.json:
        print(json.dumps({"month": args.month, "categories": cats}, indent=2))
        return 0

    # Spend categories are negative totals; income positive. Show magnitudes.
    order = sorted(cats.items(), key=lambda kv: abs(kv[1]["total"]), reverse=True)
    print(f"Breakdown for {args.month}\n")
    for cat, d in order:
        tag = "in" if d["total"] > 0 else "out"
        print(f"{cat}  £{abs(d['total']):,.2f} {tag}  ({d['count']} txns)")
        for m, v in sorted(d["merchants"].items(), key=lambda kv: abs(kv[1]["total"]),
                           reverse=True):
            print(f"    {m[:36]:36} £{abs(v['total']):>9,.2f}  {v['count']:>3}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
