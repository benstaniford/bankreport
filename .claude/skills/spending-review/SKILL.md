---
name: spending-review
description: >-
  Produce a monthly personal spending-review PDF for Ben's family (NatWest
  current account + Barclaycard) using the banklib caches: an accurate
  categorised report, category deep-dives (eating out by venue, groceries
  corner-shop vs supermarket, shopping new vs second-hand), a comparison against
  current UK cost-of-living benchmarks, and a savings strategy — then delivered
  to moria:/data/Downloads. Use this whenever Ben asks for a spending review,
  a monthly spending report/analysis, "how typical is our spending", help
  cutting spending or building savings, a category breakdown (eating out,
  groceries, shopping), or to compare the household's spending to UK averages —
  even if he doesn't say the word "skill" or name a month.
---

# Spending review

Generate a two-part monthly deliverable for a single-breadwinner family of four:

1. **`last-month-YYYY-MM.pdf`** — the accurate, categorised spending report
   (produced by the repo's `scripts/last-month`).
2. **`spending-review-YYYY-MM.pdf`** — the analysis: how the household's spending
   compares to UK benchmarks, category deep-dives, and a prioritised plan to cut
   spending and build savings.

Both are copied to `ben@moria:/data/Downloads/` (Ben's preferred destination) and
a local copy is kept in `%USERPROFILE%`.

## Why it's built this way

The money data already lives in local caches that `banklib` fills from the banks
(`~/.cache/{natwest,barclays}/transactions.db`), with per-description categories
in `~/.cache/banklib/categories.json` (spending) and `categories-income.json`
(income). So a review needs **no bank login** for an already-cached month — it's
fast and offline. The modelling rules (card settlement excluded and noted
separately, transfers count as spend, savings and benefits excluded from income)
are baked into `scripts/last-month` and `banklib.report`; reuse them rather than
re-deriving. Household-specific facts (who the salary payer is, which merchant is
the mortgage, venue types) live in `references/merchant-types.md`.

## Workflow

### 1. Pick the month
Default to the **previous calendar month**. Accept an explicit `YYYY-MM` if Ben
names one. All scripts below take `--month YYYY-MM`.

### 2. Produce the accurate categorised report
From the repo root, run the existing wrapper (it bootstraps its own venv, uses
the caches, and only logs into the banks if the month isn't cached):

```
python scripts/last-month --month YYYY-MM --out "%USERPROFILE%"
```

Read its stderr summary — it prints **total spend**, **real income**, the
**credit-card payment (excluded)** and writes `last-month-YYYY-MM.pdf`. These are
your headline numbers. If it needs a bank login (uncached month) it may prompt
for 2FA; let Ben know.

### 3. Get the category + merchant breakdown
```
python .claude/skills/spending-review/scripts/category_breakdown.py --month YYYY-MM
```
This prints every category's total and the merchants inside it (money-in and
money-out), straight from the caches. Add `--json` if you want to consume it
programmatically, or `--category "Eating Out"` to focus one.

### 4. Build the deep-dives
For the big discretionary categories, group merchants into sub-types using
`references/merchant-types.md` (read it — it has the household's known venues and
facts). The three that matter most:
- **Eating out** → Takeaways/delivery · Pubs · Restaurants · Cafés.
- **Groceries** → Supermarkets (cheaper) vs **The Local Zone** corner shop
  (pricier) vs doorstep. The corner-shop share is the saving lever.
- **Shopping** → Amazon (new) vs Vinted (second-hand) vs other.

New merchants won't be pre-classified — search the name + town, or ask Ben.

### 5. Research current UK benchmarks
Read `references/benchmarks.md`, then **refresh the figures with `WebSearch`** —
price caps and the ONS/JRF numbers go stale. You need: ONS average household
spend, the Ofgem price cap for the review month's quarter, family-of-four
grocery and food-out norms, and the JRF Minimum Income Standard. Keep the
single-earner caveat on JRF (its figure assumes two earners + childcare).

### 6. Assemble the review PDF
Write a spec JSON (see **Spec & structure** below) and render it:

```
~/.cache/banklib/venv/Scripts/python.exe \
  .claude/skills/spending-review/scripts/make_review_pdf.py \
  --spec /path/to/spec.json --out "%USERPROFILE%\spending-review-YYYY-MM.pdf"
```

Use the banklib venv's python — it has `reportlab`. (`category_breakdown.py` has
no such dependency and runs on any python.)

### 7. Deliver
```
scp -o BatchMode=yes "%USERPROFILE%\spending-review-YYYY-MM.pdf" ben@moria:/data/Downloads/
scp -o BatchMode=yes "%USERPROFILE%\last-month-YYYY-MM.pdf"      ben@moria:/data/Downloads/
```
Verify with `ssh ben@moria "ls -l /data/Downloads/*.pdf"`. If `moria` doesn't
resolve or scp fails, tell Ben and leave the PDFs in `%USERPROFILE%`.

## Spec & structure

`make_review_pdf.py` renders a JSON spec of content *blocks* (full schema in that
script's docstring). Keep the rendering deterministic and put all the thinking in
the content. Use this section order, which matches the report Ben has approved:

1. **Caveat** (`note`) — built from one month; may be skewed by periodic bills.
2. **How your spending compares** (`h2` + `table`) — category vs typical UK
   family-of-four, with a `read` column, and a bold `total_row`.
3. **Overall** (`p`) — above average total is expected for 4 people; the *shape*
   (high discretionary) is the story.
4. **Deep dives** (`h2` + three `deepdive` blocks) — eating out, groceries,
   shopping, each with a one-line `note` tying back to the plan.
5. **Income adequacy** (`h2` + `p`) — JRF framing; it's allocation, not income.
6. **Why savings are zero** (`h2` + `p`) — the paper surplus vs the savings
   cycling that eats it.
7. **Strategy** (`h2` + `p`/`bullets`) — priority order: discretionary outliers
   first (biggest, least painful), then fixed costs, then build-and-protect a
   buffer by standing order on payday. Quantify the monthly saving and the
   emergency-fund timeline.
8. **Headline** (`hr` accent + `headline`) — the single biggest lever in one line.
9. **Sources** (`h2` + `sources`) — the live benchmark URLs you used.

Minimal spec example (one deepdive + a table) — expand to the full structure:

```json
{
  "title": "Spending Review & Savings Strategy",
  "subtitle": "Family of four, single earner · UK, 2026",
  "dateline": "Based on your October 2026 bank report",
  "blocks": [
    {"note": "Caveat: built from a single month; periodic bills can skew it."},
    {"h2": "How your spending compares"},
    {"table": {"columns": ["Category","You","Typical family of 4","Read"],
               "rows": [["Groceries","£1,013","~£550–£900/mo","High side"],
                        ["TOTAL SPEND","£4,460","~£2,930–£3,110/mo avg hh","Above avg"]],
               "widths_mm": [52,20,55,45], "total_row": true}},
    {"deepdive": {"heading": "Eating out — £790.78 (38 transactions)",
      "groups": [["Takeaways / delivery", 282.63, 9, 36,
                   [["Just Eat", 278.64, 8], ["L1 Takeaway", 3.99, 1]]]],
      "note": "Takeaways are the low-pain target."}},
    {"hr": "accent"},
    {"headline": "<b>Eating out alone runs ~£540/mo above typical — that one category funds the whole emergency fund.</b>"},
    {"h2": "Sources"},
    {"sources": [["ONS Family spending", "https://www.ons.gov.uk/..."]]}
  ]
}
```

## Notes
- **Don't invent numbers.** Every figure in the PDF must come from
  `scripts/last-month`, `category_breakdown.py`, or a cited benchmark source.
- Amounts in `deepdive` groups are numbers (the renderer formats them); amounts
  in plain `table` rows are strings you format (e.g. `"£1,013"`).
- Keep the tone practical and encouraging — it's Ben's own money, not a lecture.
  End with "informational, not regulated financial advice".
- If Ben only wants part of this (e.g. "just break down groceries"), you can run
  steps 1–4 and answer in chat without building the PDF.
