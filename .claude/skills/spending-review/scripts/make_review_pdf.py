#!/usr/bin/env python3
"""Render a spending-review PDF from a JSON spec of content blocks.

The spec separates *rendering* (this deterministic script) from *content* (which
the model assembles each month from the category breakdown and fresh research).
That way the look stays consistent and you never hand-fiddle reportlab.

Run inside an environment with reportlab (the banklib venv at
``~/.cache/banklib/venv`` has it):

    python make_review_pdf.py --spec spec.json --out review.pdf

Spec shape:
    {"title": "...", "subtitle": "...", "dateline": "...",
     "blocks": [ <block>, ... ]}

Each block is one object with a single recognised key:
    {"h2": "Heading"}
    {"p": "<b>html</b> paragraph"}
    {"note": "small muted caveat"}
    {"hr": "header" | "accent"}
    {"spacer_mm": 4}
    {"bullets": ["item one", "item two"], "bullet": "•"}
    {"headline": "emphasised blue callout line"}
    {"table": {"columns": ["A","B","C"], "rows": [["x","y","z"], ...],
               "widths_mm": [52,20,55], "total_row": true}}
    {"deepdive": {"heading": "...",
                  "groups": [["label", total, count, pct_or_null,
                              [["merchant", amt, count], ...]], ...],
                  "note": "..."}}
    {"sources": [["name","https://url"], ...]}

Numbers in table cells are plain strings you format yourself (e.g. "£791");
deepdive amounts are numbers and are formatted as £x,xxx.xx by the renderer.
HTML markup allowed in text: <b> <i> <br/> &amp; &rarr; &middot; etc.
"""
from __future__ import annotations

import argparse
import json
import sys

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, ListFlowable,
    ListItem, HRFlowable)

HEADER = colors.HexColor("#1f3a5f")
ALT = colors.HexColor("#f2f5f9")
MUTED = colors.HexColor("#555555")
ACCENT = colors.HexColor("#b5361f")

_b = getSampleStyleSheet()
S = {
    "title": ParagraphStyle("t", parent=_b["Title"], fontSize=22, spaceAfter=2),
    "sub": ParagraphStyle("s", parent=_b["Heading2"], fontSize=13, textColor=HEADER,
                          spaceAfter=0),
    "muted": ParagraphStyle("m", parent=_b["Normal"], fontSize=9, textColor=MUTED),
    "h2": ParagraphStyle("h2", parent=_b["Heading2"], fontSize=13, textColor=HEADER,
                         spaceBefore=10, spaceAfter=5),
    "body": ParagraphStyle("b", parent=_b["Normal"], fontSize=10, leading=14,
                           spaceAfter=5),
    "note": ParagraphStyle("n", parent=_b["Normal"], fontSize=9, leading=12,
                           textColor=MUTED, spaceAfter=4),
    "headline": ParagraphStyle("hl", parent=_b["Normal"], fontSize=11, leading=15,
                               textColor=HEADER, spaceBefore=4, spaceAfter=4),
    "cell": ParagraphStyle("c", parent=_b["Normal"], fontSize=9, leading=11),
    "cellb": ParagraphStyle("cb", parent=_b["Normal"], fontSize=9, leading=11,
                            fontName="Helvetica-Bold"),
    "sub_i": ParagraphStyle("si", parent=_b["Normal"], fontSize=9, leading=11,
                            leftIndent=10),
}


def P(text, style="body"):
    return Paragraph(text, S[style])


def _bullets(items, bullet="•"):
    return ListFlowable([ListItem(P(i), value=bullet, leftIndent=12) for i in items],
                        bulletType="bullet", start=bullet, leftIndent=12)


def _table(spec):
    cols = spec["columns"]
    rows = spec["rows"]
    total_row = spec.get("total_row", False)
    widths = [w * mm for w in spec.get("widths_mm", [])] or None
    data = [[Paragraph(c, S["cellb"]) for c in cols]]
    for r in rows:
        data.append([Paragraph(str(c), S["cell"]) for c in r])
    t = Table(data, colWidths=widths)
    st = [
        ("BACKGROUND", (0, 0), (-1, 0), HEADER),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2 if total_row else -1), [colors.white, ALT]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.HexColor("#dddddd")),
    ]
    if total_row:
        st += [("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#e7edf5")),
               ("LINEABOVE", (0, -1), (-1, -1), 0.6, HEADER),
               ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold")]
    t.setStyle(TableStyle(st))
    return t


def _deepdive(spec):
    flow = [P(spec["heading"], "h2")]
    data = [["Item", "Spend", "Txns"]]
    bold_rows = []
    r = 1
    for label, amt, cnt, pct, subs in spec["groups"]:
        pct_txt = f"  ({pct:.0f}%)" if pct is not None else ""
        data.append([Paragraph(f"<b>{label}</b>{pct_txt}", S["cell"]),
                     f"£{amt:,.2f}", str(cnt)])
        bold_rows.append(r)
        r += 1
        for m, ma, mc in subs:
            data.append([Paragraph(m, S["sub_i"]), f"£{ma:,.2f}", str(mc)])
            r += 1
    t = Table(data, colWidths=[114 * mm, 30 * mm, 18 * mm])
    st = [
        ("BACKGROUND", (0, 0), (-1, 0), HEADER),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]
    for br in bold_rows:
        st += [("BACKGROUND", (0, br), (-1, br), ALT),
               ("FONTNAME", (1, br), (-1, br), "Helvetica-Bold"),
               ("LINEABOVE", (0, br), (-1, br), 0.25, colors.HexColor("#cfd8e3"))]
    t.setStyle(TableStyle(st))
    flow.append(t)
    if spec.get("note"):
        flow += [Spacer(1, 1.5 * mm), P(spec["note"], "note")]
    flow.append(Spacer(1, 4 * mm))
    return flow


def build(spec) -> list:
    story = [P(spec["title"], "title")]
    if spec.get("subtitle"):
        story.append(P(spec["subtitle"], "sub"))
    if spec.get("dateline"):
        story.append(P(spec["dateline"], "muted"))
    story += [Spacer(1, 3 * mm), HRFlowable(width="100%", thickness=0.8, color=HEADER),
              Spacer(1, 3 * mm)]
    for block in spec["blocks"]:
        (k, v), = block.items()
        if k == "h2":
            story.append(P(v, "h2"))
        elif k == "p":
            story.append(P(v))
        elif k == "note":
            story.append(P(v, "note"))
        elif k == "headline":
            story.append(P(v, "headline"))
        elif k == "hr":
            story.append(HRFlowable(width="100%", thickness=0.6,
                                    color=ACCENT if v == "accent" else HEADER))
        elif k == "spacer_mm":
            story.append(Spacer(1, float(v) * mm))
        elif k == "bullets":
            story.append(_bullets(v, block.get("bullet", "•")))
        elif k == "table":
            story.append(_table(v))
        elif k == "deepdive":
            story += _deepdive(v)
        elif k == "sources":
            story.append(_bullets(
                [f'<a href="{u}" color="#1f3a5f">{n}</a>' for n, u in v], "–"))
        else:
            raise SystemExit(f"Unknown block type: {k!r}")
    return story


def main() -> int:
    ap = argparse.ArgumentParser(description="Render a spending-review PDF.")
    ap.add_argument("--spec", required=True, help="path to the spec JSON")
    ap.add_argument("--out", required=True, help="output PDF path")
    args = ap.parse_args()

    spec = json.loads(open(args.spec, encoding="utf-8").read())
    doc = SimpleDocTemplate(args.out, pagesize=A4, leftMargin=18 * mm,
                            rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
                            title=spec.get("title", "Spending Review"))
    doc.build(build(spec))
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
