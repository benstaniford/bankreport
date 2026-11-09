# Merchant classifications & household facts

Household-specific knowledge for grouping transactions consistently across
months. These are stable facts about *this* household; confirm with the user
if something looks off, and add new merchants as they appear.

## Household facts (affects the numbers)

- **Single breadwinner, family of four**, Wirral / Merseyside area.
- **NatWest** = current account; **Barclays (Barclaycard)** = credit card paid
  off from NatWest. The NatWest→card settlement (`B/CARD PLAT VISA`) is excluded
  from spend and noted separately — `scripts/last-month` already handles this.
- **Salary** payer: **Avecto Limited** (→ Income / Salary).
- **Nationwide (`NATIONWIDE B S`)** is the **mortgage** → Housing (not Savings).
- **Tembo Savings** credits are a **savings top-up to stay liquid** → "Savings
  Drawdown", excluded from real income.
- **HMRC Child Benefit** is pass-through money repaid later → Benefits, excluded
  from real income.
- Preferred PDF destination: `ben@moria:/data/Downloads/` (via scp).

## Eating Out → venue sub-types

Group the "Eating Out" category into these four for the deep dive:

- **Takeaways / delivery**: Just Eat, L1 Takeaway, Deliveroo, Uber Eats, any
  named takeaway.
- **Pubs**: Stanley's Cask (the local), Ship & Mitre, The Magazine, Philharmonic
  Dining Rooms, The White Hart, The Pilgrim.
- **Restaurants / sit-down**: Cappadocia, Marino Lounge (café-bar, counted here).
- **Cafés / coffee / bakery**: Elsie's Bakehouse, Loaf Café, Coffee on the Lake,
  Starbucks, Costa, Greggs.

## Groceries → corner shop vs supermarket

The point of this split is that the corner shop is pricier per item, so a high
corner-shop share is a saving opportunity.

- **Supermarkets (cheaper per item)**: Sainsbury's, Tesco, Asda, Morrisons,
  Aldi, Lidl, Marks & Spencer (premium but still a supermarket).
- **Corner shop (pricier)**: **The Local Zone** (the household's corner shop).
- **Doorstep**: Milkman.

## Shopping → new vs second-hand

- **Amazon** = new goods (the spend to question).
- **Vinted** = second-hand (already the thrifty option; many small buys add up).
- **Home Bargains**, other retail = general.

## Notes

- Sub-type membership is judgement, not in the data — a new pub or takeaway won't
  be auto-classified. When unsure, search the merchant name + town, or ask.
- Keep this list in sync with the category caches
  (`~/.cache/banklib/categories.json`, `categories-income.json`). The category
  itself (Groceries, Eating Out, …) comes from those caches; this file only adds
  the finer venue/type grouping on top.
