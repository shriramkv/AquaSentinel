"""Survey report: data tables straight from the database, plus an optional Gemini narrative, as a PDF."""
from __future__ import annotations

import re
import time
from datetime import datetime
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import config, cpcb

TEAL = colors.HexColor("#0f766e")
INK = colors.HexColor("#111827")
MUTED = colors.HexColor("#6b7280")


def collect(hub, hours: float) -> dict:
    """Everything the report tables need, read from storage."""
    since = time.time() - hours * 3600
    stats = {p: hub.store.stats(p, since) for p in config.PARAMS}
    overall = {}
    for p, rows in stats.items():
        n = sum(r["n"] for r in rows)
        if n:
            overall[p] = {"n": n, "min": min(r["min"] for r in rows), "max": max(r["max"] for r in rows),
                          "mean": sum(r["mean"] * r["n"] for r in rows) / n}
    mean = lambda p: overall.get(p, {}).get("mean")
    worst = None
    if all(p in overall for p in ("ph", "do", "ec")):
        ph = overall["ph"]
        worst = {"ph": ph["min"] if 7.5 - ph["min"] > ph["max"] - 7.5 else ph["max"], "do": overall["do"]["min"], "ec": overall["ec"]["max"]}
    return {
        "hours": hours, "generated": time.time(), "lake": hub.lake.name, "simulated": hub.simulated,
        "stats": stats, "overall": overall,
        "cpcb": cpcb.classify(mean("ph"), mean("do"), mean("ec")) if overall else None,
        "cpcb_worst": worst and {"values": worst, **cpcb.classify(worst["ph"], worst["do"], worst["ec"])},
        "anomalies": hub.store.latest("anomalies", 40, since=since, ts_col="started")[::-1],
        "diagnoses": {d["anomaly_id"]: d["body"] for d in hub.store.latest("diagnoses", 40, since=since)[::-1]},
        "zones": hub.zone_health.latest, "zone_model": hub.zone_health.model_info,
        "readings": hub.store.count_readings(since),
    }


def _fmt(p: str, v) -> str:
    return "-" if v is None else f"{v:.{config.PARAM_META[p]['digits']}f}"


def _md_inline(s: str) -> str:
    s = escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    return re.sub(r"(?<!\*)\*(?!\*)(.+?)\*", r"<i>\1</i>", s)


def _markdown(md: str, st: dict) -> list:
    """Small Markdown subset: headings, bullets, paragraphs, bold and italics."""
    out, para = [], []

    def flush():
        if para:
            out.append(Paragraph(_md_inline(" ".join(para)), st["body"]))
            para.clear()

    for line in md.splitlines():
        t = line.strip()
        if not t:
            flush()
        elif t.startswith("#"):
            flush()
            out.append(Paragraph(_md_inline(t.lstrip("#").strip()), st["h3"]))
        elif re.match(r"^([-*•]|\d+[.)])\s+", t):
            flush()
            out.append(Paragraph(_md_inline(re.sub(r"^([-*•]|\d+[.)])\s+", "", t)), st["bullet"], bulletText="•"))
        elif t.startswith("|"):
            flush()
            if not re.match(r"^\|[\s:|-]+\|?$", t):
                out.append(Paragraph(_md_inline("  ·  ".join(c.strip() for c in t.strip("|").split("|"))), st["body"]))
        else:
            para.append(t)
    flush()
    return out


def _table(rows: list[list], widths=None) -> Table:
    t = Table(rows, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), INK), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f4f6")]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def build_pdf(path, data: dict, narrative: str | None, model: str | None) -> None:
    ss = getSampleStyleSheet()
    st = {
        "title": ParagraphStyle("t", parent=ss["Title"], fontSize=19, textColor=INK, alignment=0, spaceAfter=2),
        "sub": ParagraphStyle("s", parent=ss["Normal"], fontSize=9.5, textColor=MUTED, spaceAfter=8),
        "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontSize=12.5, textColor=TEAL, spaceBefore=12, spaceAfter=5),
        "h3": ParagraphStyle("h3", parent=ss["Heading3"], fontSize=10.5, textColor=INK, spaceBefore=8, spaceAfter=3),
        "body": ParagraphStyle("b", parent=ss["Normal"], fontSize=9.5, leading=13.5, spaceAfter=5),
        "bullet": ParagraphStyle("bl", parent=ss["Normal"], fontSize=9.5, leading=13.5, leftIndent=12, bulletIndent=2, spaceAfter=2),
        "note": ParagraphStyle("n", parent=ss["Normal"], fontSize=8, leading=11, textColor=MUTED, spaceAfter=4),
        "cell": ParagraphStyle("c", parent=ss["Normal"], fontSize=8.5, leading=11),
    }
    tz = ZoneInfo(config.POND_TZ)
    when = lambda ts: datetime.fromtimestamp(ts, tz).strftime("%d %b %Y %H:%M")
    P = lambda s, k="cell": Paragraph(escape(str(s)), st[k])
    el = [Paragraph(f"Water quality survey report: {escape(data['lake'])}", st["title"]),
          Paragraph(f"AquaSentinel autonomous survey · last {data['hours']:g} h · generated {when(data['generated'])} IST · "
                    f"{data['readings']} readings", st["sub"])]
    if data["simulated"]:
        el.append(Paragraph("<b>Simulated data.</b> This report was generated from the built-in lake simulator for "
                            "demonstration. It does not describe a real water body and must not be used for regulatory action.",
                            ParagraphStyle("warn", parent=st["body"], backColor=colors.HexColor("#fef3c7"), borderPadding=6,
                                           textColor=colors.HexColor("#92400e"), spaceAfter=10)))

    el.append(Paragraph("1. Measured parameters", st["h2"]))
    if data["overall"]:
        rows = [["Parameter", "Unit", "Readings", "Min", "Mean", "Max"]]
        for p in config.PARAMS:
            o = data["overall"].get(p)
            if o:
                m = config.PARAM_META[p]
                rows.append([m["label"], m["unit"] or "-", o["n"], _fmt(p, o["min"]), _fmt(p, o["mean"]), _fmt(p, o["max"])])
        el.append(_table(rows, [48 * mm, 22 * mm, 22 * mm, 26 * mm, 26 * mm, 26 * mm]))
        el.append(Spacer(1, 6))
        zones = sorted({r["zone"] for rows_ in data["stats"].values() for r in rows_})
        # Short headings: "Dissolved oxygen" does not fit a 24 mm column
        short = {"ph": "pH", "do": "DO", "temp": "Temp.", "ec": "EC", "nitrate": "Nitrate", "turbidity": "Turbidity"}
        rows = [["Zone mean"] + [short[p] for p in config.PARAMS]]
        for z in zones:
            rows.append([z] + [_fmt(p, next((r["mean"] for r in data["stats"][p] if r["zone"] == z), None)) for p in config.PARAMS])
        el.append(_table(rows, [26 * mm] + [24 * mm] * 6))
    else:
        el.append(P("No readings in this window.", "body"))

    el.append(Paragraph("2. CPCB designated best use, from measured parameters", st["h2"]))
    c = data["cpcb"]
    if c:
        el.append(Paragraph(f"Lake-wide means meet the pH, DO and conductivity limits of <b>Class "
                            f"{escape(c['best_class_on_measured_parameters'])}</b>: {escape(c['use'])}.", st["body"]))
        w = data.get("cpcb_worst")
        if w:
            v = w["values"]
            cls = w["best_class_on_measured_parameters"]
            el.append(Paragraph(f"The worst readings in the window (pH {v['ph']:.2f}, DO {v['do']:.2f} mg/L, conductivity "
                                f"{v['ec']:.0f} µS/cm) meet the limits of <b>{'no class' if cls == 'below E' else 'Class ' + escape(cls)}</b>. "
                                "A lake-wide mean can hide a local discharge, so both are shown.", st["body"]))
        el.append(Paragraph(escape(c["caveat"]) + " " + escape(cpcb.CRITERIA["verify_note"]), st["note"]))

    el.append(Paragraph("3. Anomalies", st["h2"]))
    if data["anomalies"]:
        rows = [["#", "Time (IST)", "Zone", "Parameters", "Peak readings", "Source estimate", "Probable cause"]]
        for a in data["anomalies"]:
            peak = ", ".join(f"{config.PARAM_META[p]['label']} {_fmt(p, (a['peak'] or {}).get(p))}" for p in a["params"])
            src = f"{a['source_lat']:.5f}, {a['source_lon']:.5f}" if a.get("source_lat") is not None else (
                f"same as #{a['linked_to']}" if a.get("linked_to") else "not traced")
            d = data["diagnoses"].get(a["id"])
            cause = f"{d.get('probable_cause', '?').replace('_', ' ')} ({d.get('confidence', '?')})" if d else "not diagnosed"
            rows.append([a["id"], when(a["started"]), a["zone"], P(", ".join(config.PARAM_META[p]["label"] for p in a["params"])),
                         P(peak), P(src), P(cause)])
        el.append(_table(rows, [8 * mm, 27 * mm, 11 * mm, 30 * mm, 42 * mm, 28 * mm, 30 * mm]))
        if any(data["diagnoses"].values()):
            el.append(Paragraph("Probable causes are written by a Gemini agent from the sensor and image evidence. "
                                "They are leads for inspection and lab confirmation, not findings of fact.", st["note"]))
    else:
        el.append(P("No anomalies were detected in this window.", "body"))

    el.append(Paragraph("4. Risk to aquatic life by zone (machine-learning model)", st["h2"]))
    zs = [z for z in data["zones"].values() if z.get("available")]
    if zs:
        rows = [["Zone", "Status", "Confidence", "Reliable", "Main factor"]]
        for z in zs:
            f = z["top_factors"][0]["description"] if z.get("top_factors") else "-"
            rows.append([z["zone"], z["health_status"], f"{z['confidence']:.0%}", "yes" if z["reliable"] else "provisional", P(f)])
        el.append(_table(rows, [14 * mm, 24 * mm, 24 * mm, 24 * mm, 84 * mm]))
        el.append(Paragraph(escape(data["zone_model"].get("note", "")), st["note"]))
    else:
        el.append(P("Zone health model not available for this window.", "body"))

    if narrative:
        el.append(Paragraph("5. Narrative", st["h2"]))
        el.append(Paragraph(f"Written by Gemini ({escape(model or '')}) from the data above. Check it against the tables before relying on it.", st["note"]))
        el += _markdown(narrative, st)

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 10 * mm, "AquaSentinel survey report" + (" · SIMULATED DATA" if data["simulated"] else ""))
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
                      title=f"Water quality survey report: {data['lake']}", author="AquaSentinel").build(
        el, onFirstPage=footer, onLaterPages=footer)
