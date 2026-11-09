"""Render the monthly spending report to a PDF with reportlab.

Kept out of :mod:`banklib`'s import graph (nothing in ``__init__`` imports it) so
the core library never hard-depends on reportlab; only ``scripts/last-month``
pulls it in, lazily. All the number-crunching lives in :mod:`banklib.report`;
this module is presentation only.
"""

from __future__ import annotations

import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.barcharts import HorizontalBarChart
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether)

from .money import format_money
from .report import (
    Breakdown, aggregate_by_category, aggregate_income, category_of)

_HEADER_BG = colors.HexColor("#1f3a5f")
_ROW_ALT = colors.HexColor("#f2f5f9")
_BAR_FILL = colors.HexColor("#2f6db5")
_MUTED = colors.HexColor("#555555")


def render(path, *, period: tuple[datetime.date, datetime.date],
           breakdown: Breakdown, categories: dict,
           income_categories: dict | None = None) -> Path:
    """Write the report PDF for ``period`` and return its path."""
    income_categories = income_categories or {}
    path = Path(path)
    start, end = period
    month_label = start.strftime("%B %Y")

    spend_totals, non_spend_totals = aggregate_by_category(
        breakdown.spending, categories)
    total_spend = sum(c.total for c in spend_totals)
    card_spend = sum(-t.amount for t in breakdown.spending
                     if t.provider == "barclays"
                     and category_of(t, categories) not in _non_spend_names())
    current_spend = total_spend - card_spend

    styles = _styles()
    story = []
    story.append(Paragraph("Monthly Spending Report", styles["title"]))
    story.append(Paragraph(month_label, styles["subtitle"]))
    story.append(Paragraph(
        f"{start.strftime('%d %b %Y')} – {end.strftime('%d %b %Y')}",
        styles["muted"]))
    story.append(Spacer(1, 6 * mm))

    income_totals, excluded_income = aggregate_income(
        breakdown.income, income_categories)
    real_income = sum(c.total for c in income_totals)
    savings_total = sum(c.total for c in non_spend_totals
                        if c.category == "Savings")
    story.append(Paragraph("Summary", styles["h2"]))
    story.append(_summary_table(breakdown, total_spend, card_spend,
                                current_spend, real_income, savings_total))
    story.append(Spacer(1, 6 * mm))

    if spend_totals:
        story.append(Paragraph("Spending by category", styles["h2"]))
        story.append(_category_table(spend_totals, total_spend))
        story.append(Spacer(1, 4 * mm))
        story.append(_category_chart(spend_totals))
        story.append(Spacer(1, 6 * mm))

    if income_totals:
        story.append(Paragraph("Income by category", styles["h2"]))
        story.append(_category_table(income_totals, None))
        if len(income_totals) > 1:
            story.append(Spacer(1, 4 * mm))
            story.append(_category_chart(income_totals))
        story.append(Spacer(1, 6 * mm))

    if excluded_income:
        story.append(Paragraph(
            "Excluded from income (savings top-ups, benefits)", styles["h2"]))
        story.append(_category_table(excluded_income, None))
        story.append(Spacer(1, 6 * mm))

    if non_spend_totals:
        story.append(Paragraph("Savings (excluded from spend)", styles["h2"]))
        story.append(_category_table(non_spend_totals, None))
        story.append(Spacer(1, 6 * mm))

    story.append(Paragraph("Transactions by category", styles["h2"]))
    for block in _transaction_blocks(breakdown.spending, categories, styles):
        story.append(block)

    doc = SimpleDocTemplate(
        str(path), pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"Spending {month_label}")
    doc.build(story)
    return path


def _non_spend_names() -> frozenset:
    from .report import NON_SPEND_CATEGORIES
    return NON_SPEND_CATEGORIES


def _styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=base["Title"], fontSize=22,
                                spaceAfter=2),
        "subtitle": ParagraphStyle("subtitle", parent=base["Heading2"],
                                   fontSize=14, textColor=_HEADER_BG, spaceAfter=0),
        "muted": ParagraphStyle("muted", parent=base["Normal"], fontSize=9,
                                textColor=_MUTED),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontSize=13,
                             textColor=_HEADER_BG, spaceBefore=4, spaceAfter=6),
        "h3": ParagraphStyle("h3", parent=base["Heading3"], fontSize=11,
                             textColor=_HEADER_BG, spaceBefore=2, spaceAfter=4),
        "cell": ParagraphStyle("cell", parent=base["Normal"], fontSize=9),
    }


def _summary_table(breakdown: Breakdown, total_spend: float,
                   card_spend: float, current_spend: float,
                   income_total: float, savings_total: float = 0.0) -> Table:
    net = income_total - total_spend
    rows = [
        ["Current-account spend (NatWest)", format_money(current_spend)],
        ["Credit-card spend (Barclays)", format_money(card_spend)],
        ["Total spend", format_money(total_spend)],
        ["Income", format_money(income_total)],
        ["Net (income − spend)", format_money(net)],
    ]
    # Rows below the net line are money that left the account but is not "spend":
    # shown for completeness, greyed out and set apart.
    first_excluded = len(rows)
    if savings_total:
        rows.append(["Savings — excluded", format_money(-savings_total)])
    if breakdown.card_payments:
        when = ", ".join(t.date.strftime("%d %b") for t in breakdown.card_payments)
        rows.append([
            f"Credit-card payment — excluded ({when})",
            format_money(-breakdown.card_payment_total)])

    table = Table(rows, colWidths=[110 * mm, 40 * mm])
    style = [
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.3, colors.HexColor("#dddddd")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        # Total-spend row emphasised.
        ("FONTNAME", (0, 2), (-1, 2), "Helvetica-Bold"),
        ("LINEABOVE", (0, 2), (-1, 2), 0.6, _HEADER_BG),
    ]
    if len(rows) > first_excluded:
        style.append(("TEXTCOLOR", (0, first_excluded), (-1, -1), _MUTED))
        style.append(("LINEABOVE", (0, first_excluded), (-1, first_excluded),
                      0.6, _MUTED))
    table.setStyle(TableStyle(style))
    return table


def _category_table(totals, total_spend: float | None) -> Table:
    header = ["Category", "Amount", "Txns"]
    if total_spend:
        header.insert(2, "% of spend")
    rows = [header]
    for c in totals:
        row = [c.category, format_money(c.total), str(c.count)]
        if total_spend:
            pct = (c.total / total_spend * 100) if total_spend else 0
            row.insert(2, f"{pct:.0f}%")
        rows.append(row)

    ncols = len(header)
    widths = [60 * mm, 32 * mm] + ([20 * mm] if total_spend else []) + [20 * mm]
    table = Table(rows, colWidths=widths[:ncols])
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _ROW_ALT]),
    ]
    table.setStyle(TableStyle(style))
    return table


def _category_chart(totals) -> Drawing:
    data = [[c.total for c in totals]]
    labels = [c.category for c in totals]
    n = len(totals)
    row_h = 7 * mm
    height = max(40 * mm, n * row_h + 20)
    width = 150 * mm

    drawing = Drawing(width, height)
    chart = HorizontalBarChart()
    chart.x = 45 * mm
    chart.y = 10
    chart.width = width - 55 * mm
    chart.height = height - 20
    chart.data = data
    chart.bars[0].fillColor = _BAR_FILL
    chart.bars[0].strokeColor = None
    chart.valueAxis.valueMin = 0
    chart.valueAxis.labelTextFormat = lambda v: f"£{v:,.0f}"
    chart.valueAxis.labels.fontSize = 7
    chart.categoryAxis.categoryNames = labels
    chart.categoryAxis.labels.fontSize = 8
    chart.categoryAxis.labels.boxAnchor = "e"
    chart.categoryAxis.labels.dx = -4
    chart.barWidth = row_h * 0.6
    drawing.add(chart)
    return drawing


def _transaction_blocks(spending, categories: dict, styles: dict) -> list:
    """One KeepTogether block per category: a heading + its itemised rows."""
    grouped: dict[str, list] = {}
    for t in spending:
        grouped.setdefault(category_of(t, categories), []).append(t)

    # Spend categories first (by size), then non-spend, then uncategorised.
    order = sorted(grouped,
                   key=lambda c: sum(-t.amount for t in grouped[c]), reverse=True)

    blocks = []
    for cat in order:
        txns = sorted(grouped[cat], key=lambda t: t.date)
        total = sum(-t.amount for t in txns)
        rows = [["Date", "Description", "Bank", "Amount"]]
        for t in txns:
            merchant = ""
            info = categories.get(" ".join((t.description or "").split()).upper())
            if info:
                merchant = info.get("merchant") or ""
            desc = merchant or t.description or ""
            rows.append([
                t.date.strftime("%d %b"),
                Paragraph(_escape(desc), styles["cell"]),
                t.provider.capitalize(),
                format_money(t.amount),
            ])
        rows.append(["", "", "Total", format_money(-total)])
        table = Table(rows, colWidths=[18 * mm, 92 * mm, 24 * mm, 28 * mm])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), _ROW_ALT),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("ALIGN", (3, 0), (3, -1), "RIGHT"),
            ("ALIGN", (2, 0), (2, -1), "CENTER"),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
            ("LINEBELOW", (0, 0), (-1, 0), 0.3, colors.HexColor("#cccccc")),
            ("FONTNAME", (2, -1), (-1, -1), "Helvetica-Bold"),
            ("LINEABOVE", (0, -1), (-1, -1), 0.4, _MUTED),
        ]))
        heading = Paragraph(
            f"{cat} &nbsp;—&nbsp; {format_money(total)}", styles["h3"])
        blocks.append(KeepTogether([heading, table, Spacer(1, 4 * mm)]))
    return blocks


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
