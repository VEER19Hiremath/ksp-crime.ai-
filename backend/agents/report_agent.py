"""Generate a structured police investigation PDF from chat evidence.

The conversation is retained only as an appendix. The main report identifies
the FIRs, offences, stations, people, intelligence findings, and audit sources.
"""
from __future__ import annotations

import io
import os
import re
from datetime import datetime, timezone
from html import escape
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

NAVY = colors.HexColor("#0B1F3A")
SAFFRON = colors.HexColor("#C45C26")
GREEN = colors.HexColor("#2F6F4E")
SAND = colors.HexColor("#F7F3EB")
PALE_BLUE = colors.HexColor("#EAF0F7")
MUTED = colors.HexColor("#5C6B7A")
BORDER = colors.HexColor("#D9D3C8")
WHITE = colors.white


def _register_font() -> tuple[str, str]:
    """Use a Kannada-capable Windows font when available."""
    candidates = [
        ("Nirmala", r"C:\Windows\Fonts\Nirmala.ttf", r"C:\Windows\Fonts\NirmalaB.ttf"),
        ("NotoSans", "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
         "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"),
        ("DejaVu", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ]
    for family, regular, bold in candidates:
        if not os.path.exists(regular):
            continue
        try:
            pdfmetrics.registerFont(TTFont(family, regular))
            bold_name = family
            if os.path.exists(bold):
                bold_name = f"{family}-Bold"
                pdfmetrics.registerFont(TTFont(bold_name, bold))
            return family, bold_name
        except Exception:
            continue
    return "Helvetica", "Helvetica-Bold"


FONT, FONT_BOLD = _register_font()


def _text(value: Any, limit: int = 1200) -> str:
    raw = str(value or "—").replace("\x00", "").strip()
    if len(raw) > limit:
        raw = raw[: limit - 1].rsplit(" ", 1)[0] + "…"
    return escape(raw).replace("\n", "<br/>")


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "ReportTitle", parent=base["Title"], fontName=FONT_BOLD,
            fontSize=23, leading=28, textColor=NAVY, alignment=TA_LEFT,
            spaceAfter=5 * mm,
        ),
        "subtitle": ParagraphStyle(
            "Subtitle", parent=base["Normal"], fontName=FONT,
            fontSize=10, leading=15, textColor=MUTED,
        ),
        "h1": ParagraphStyle(
            "Section", parent=base["Heading1"], fontName=FONT_BOLD,
            fontSize=15, leading=19, textColor=NAVY, spaceBefore=5 * mm,
            spaceAfter=2.5 * mm, keepWithNext=True,
        ),
        "h2": ParagraphStyle(
            "Subsection", parent=base["Heading2"], fontName=FONT_BOLD,
            fontSize=11, leading=15, textColor=SAFFRON, spaceBefore=3 * mm,
            spaceAfter=1.5 * mm, keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "Body", parent=base["BodyText"], fontName=FONT,
            fontSize=9, leading=13, textColor=NAVY, spaceAfter=1.5 * mm,
        ),
        "small": ParagraphStyle(
            "Small", parent=base["BodyText"], fontName=FONT,
            fontSize=7.5, leading=10, textColor=MUTED,
        ),
        "label": ParagraphStyle(
            "Label", parent=base["BodyText"], fontName=FONT_BOLD,
            fontSize=7.5, leading=10, textColor=MUTED,
        ),
        "value": ParagraphStyle(
            "Value", parent=base["BodyText"], fontName=FONT_BOLD,
            fontSize=9, leading=12, textColor=NAVY,
        ),
        "badge": ParagraphStyle(
            "Badge", parent=base["BodyText"], fontName=FONT_BOLD,
            fontSize=8, leading=11, textColor=WHITE, alignment=TA_CENTER,
        ),
        "code": ParagraphStyle(
            "AuditCode", parent=base["Code"], fontName="Courier",
            fontSize=6.5, leading=9, textColor=colors.HexColor("#334155"),
            wordWrap="CJK",
        ),
        "center": ParagraphStyle(
            "Center", parent=base["BodyText"], fontName=FONT,
            fontSize=8, leading=11, textColor=MUTED, alignment=TA_CENTER,
        ),
    }


def _p(value: Any, style: ParagraphStyle) -> Paragraph:
    return Paragraph(_text(value), style)


def _extract_cases(turns: list[dict]) -> list[dict]:
    cases: dict[str, dict] = {}
    for turn in turns:
        for row in turn.get("rows") or []:
            if not isinstance(row, dict):
                continue
            crime_no = row.get("crime_no") or row.get("case_no")
            if not crime_no:
                continue
            key = str(crime_no)
            current = cases.setdefault(key, {"crime_no": key})
            for field in (
                "case_no", "crime_head_name", "crime_group_name", "unit_name",
                "district_name", "case_status_name", "brief_facts",
                "officer_name", "crime_registered_date",
            ):
                if row.get(field) and not current.get(field):
                    current[field] = row[field]
            people = current.setdefault("people", [])
            person = (
                row.get("person_name") or row.get("accused_name")
                or row.get("victim_name")
            )
            if person:
                item = {"name": str(person), "role": str(row.get("role") or "person")}
                if item not in people:
                    people.append(item)
    return list(cases.values())


def _extract_people(turns: list[dict]) -> list[dict]:
    people: dict[tuple[str, str], dict] = {}
    for turn in turns:
        for row in turn.get("rows") or []:
            if not isinstance(row, dict):
                continue
            name = (
                row.get("person_name") or row.get("accused_name")
                or row.get("victim_name") or row.get("officer_name")
            )
            if not name:
                continue
            role = str(
                row.get("role")
                or ("officer" if row.get("officer_name") else "person")
            ).title()
            key = (str(name).lower(), role.lower())
            item = people.setdefault(
                key, {"name": str(name), "role": role, "firs": set(), "crimes": set()}
            )
            if row.get("crime_no"):
                item["firs"].add(str(row["crime_no"]))
            if row.get("crime_head_name"):
                item["crimes"].add(str(row["crime_head_name"]))
    result = []
    for item in people.values():
        result.append({
            **item,
            "firs": sorted(item["firs"]),
            "crimes": sorted(item["crimes"]),
        })
    return result


def _case_numbers_from_text(turns: list[dict]) -> list[str]:
    values: list[str] = []
    for turn in turns:
        combined = f"{turn.get('question', '')} {turn.get('answer', '')}"
        for number in re.findall(r"\b\d{10,}\b", combined):
            if number not in values:
                values.append(number)
    return values


def _kv_table(items: list[tuple[str, Any]], st: dict) -> Table:
    cells = []
    for label, value in items:
        cells.append([
            Paragraph(_text(label), st["label"]),
            Paragraph(_text(value), st["value"]),
        ])
    table = Table(cells, colWidths=[43 * mm, 119 * mm], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), SAND),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def _section_title(number: str, title: str, st: dict) -> list:
    return [
        Spacer(1, 2 * mm),
        Paragraph(f"{escape(number)}&nbsp;&nbsp;{escape(title)}", st["h1"]),
        HRFlowable(width="100%", thickness=1.2, color=SAFFRON, spaceAfter=3 * mm),
    ]


def _page_chrome(canvas, doc) -> None:
    canvas.saveState()
    width, height = A4
    canvas.setFillColor(NAVY)
    canvas.rect(0, height - 14 * mm, width, 14 * mm, stroke=0, fill=1)
    canvas.setFillColor(WHITE)
    canvas.setFont(FONT_BOLD, 8)
    canvas.drawString(18 * mm, height - 9 * mm, "KARNATAKA STATE POLICE · CRIME INTELLIGENCE")
    canvas.setFillColor(MUTED)
    canvas.setFont(FONT, 7)
    canvas.drawString(18 * mm, 10 * mm, "CONFIDENTIAL · INVESTIGATIVE USE ONLY")
    canvas.drawRightString(width - 18 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


def _merge_turns_for_report(session_id: str, client_turns: list[dict]) -> list[dict]:
    """Prefer DB turns (have rows_json) and append any unsaved client-only turns."""
    from core.history import load_turns

    db_turns = load_turns(session_id) if session_id else []
    skip = {"(call started)", "(voice)", "(voice call)"}
    merged: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def _add(turn: dict) -> None:
        q = str(turn.get("question") or "").strip()
        a = str(turn.get("answer") or "").strip()
        if q.lower() in skip:
            return
        if not q and not a:
            return
        key = (q.lower(), a[:120].lower())
        if key in seen:
            return
        seen.add(key)
        merged.append({
            "question": q,
            "answer": a,
            "tool": turn.get("tool"),
            "query": turn.get("query"),
            "rows": turn.get("rows") or [],
        })

    for turn in db_turns:
        _add(turn)
    for turn in client_turns or []:
        _add(turn)
    return merged


def _hydrate_cases(cases: list[dict]) -> list[dict]:
    """Fill FIR profile fields from Postgres when export only has a crime number."""
    from agents.investigation_briefing import _fetch_case

    for case in cases:
        if case.get("crime_head_name") and case.get("unit_name"):
            continue
        crime_no = str(case.get("crime_no") or "").strip()
        if not crime_no:
            continue
        try:
            fetched = _fetch_case(crime_no)
        except Exception:
            fetched = None
        if not fetched:
            continue
        for field in (
            "case_no", "crime_head_name", "crime_group_name", "unit_name",
            "district_name", "case_status_name", "brief_facts", "officer_name",
            "crime_registered_date",
        ):
            if fetched.get(field) and not case.get(field):
                case[field] = fetched[field]
    return cases


def build_investigation_pdf(session_id: str, turns: list[dict]) -> bytes:
    """Build a styled intelligence report from structured result rows."""
    buffer = io.BytesIO()
    generated = datetime.now(timezone.utc)
    turns = _merge_turns_for_report(session_id, turns)
    cases = _hydrate_cases(_extract_cases(turns))
    people = _extract_people(turns)
    text_case_numbers = _case_numbers_from_text(turns)
    for number in text_case_numbers:
        if not any(c["crime_no"] == number for c in cases):
            cases.append({"crime_no": number, "people": []})
    cases = _hydrate_cases(cases)

    st = _styles()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=22 * mm,
        bottomMargin=18 * mm,
        title=f"KSP Investigation Report {session_id}",
        author="KSP Crime Intelligence Assistant",
        subject="Crime intelligence and investigation briefing",
    )
    story: list = []

    # Cover / identity block
    badge = Table(
        [[Paragraph("CONFIDENTIAL", st["badge"])]],
        colWidths=[38 * mm],
        hAlign="LEFT",
    )
    badge.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), SAFFRON),
        ("BOX", (0, 0), (-1, -1), 0, SAFFRON),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.extend([
        Spacer(1, 9 * mm),
        badge,
        Spacer(1, 5 * mm),
        Paragraph("CRIME INTELLIGENCE<br/>INVESTIGATION REPORT", st["title"]),
        Paragraph(
            "Structured briefing generated from live FIR records, investigative "
            "analytics, and the session audit trail.",
            st["subtitle"],
        ),
        Spacer(1, 7 * mm),
        _kv_table([
            ("Report reference", session_id),
            ("Generated", generated.strftime("%d %B %Y · %H:%M UTC")),
            ("FIRs identified", len(cases)),
            ("People identified", len(people)),
            ("Intelligence interactions", len(turns)),
            ("Classification", "CONFIDENTIAL · INVESTIGATIVE USE ONLY"),
        ], st),
        Spacer(1, 8 * mm),
    ])

    crime_types = sorted({
        str(c.get("crime_head_name")) for c in cases if c.get("crime_head_name")
    })
    stations = sorted({str(c.get("unit_name")) for c in cases if c.get("unit_name")})
    statuses = sorted({
        str(c.get("case_status_name")) for c in cases if c.get("case_status_name")
    })
    story.extend(_section_title("01", "EXECUTIVE INTELLIGENCE SUMMARY", st))
    summary_rows = [
        ("Primary offence(s)", ", ".join(crime_types) or "Not established in exported evidence"),
        ("Police station(s)", ", ".join(stations) or "Not established in exported evidence"),
        ("Case status", ", ".join(statuses) or "Not established in exported evidence"),
        ("Network scope", f"{len(people)} identified person/role records"),
    ]
    story.append(_kv_table(summary_rows, st))
    story.append(Spacer(1, 3 * mm))
    if not cases:
        story.append(Paragraph(
            "No FIR was identified in this session. This document is an intelligence "
            "conversation report, not a case-specific investigation report.",
            st["body"],
        ))
    else:
        story.append(Paragraph(
            f"This report consolidates {len(cases)} FIR record(s) discussed during "
            f"the session. The principal recorded offence categories are "
            f"{escape(', '.join(crime_types) or 'not yet classified')}. "
            "All findings should be verified against the source FIR before operational action.",
            st["body"],
        ))

    # Case profiles
    story.extend(_section_title("02", "FIR / CASE PROFILES", st))
    if cases:
        for index, case in enumerate(cases, 1):
            title = (
                f"CASE {index:02d} · {case.get('crime_head_name') or 'OFFENCE NOT CLASSIFIED'}"
            )
            block = [
                Paragraph(_text(title), st["h2"]),
                _kv_table([
                    ("Crime / FIR number", case.get("crime_no")),
                    ("Case number", case.get("case_no") or "—"),
                    ("Crime type", case.get("crime_head_name") or "—"),
                    ("Crime group", case.get("crime_group_name") or "—"),
                    ("Police station", case.get("unit_name") or "—"),
                    ("District", case.get("district_name") or "—"),
                    ("Current status", case.get("case_status_name") or "—"),
                    ("Investigating officer", case.get("officer_name") or "—"),
                    ("Brief facts", case.get("brief_facts") or "Not available in exported rows"),
                ], st),
                Spacer(1, 3 * mm),
            ]
            story.append(KeepTogether(block))
    else:
        story.append(Paragraph("No case records were returned in this session.", st["body"]))

    # People and network
    story.extend(_section_title("03", "PERSONS & CRIMINAL NETWORK INDICATORS", st))
    if people:
        data = [[
            Paragraph("PERSON", st["label"]),
            Paragraph("ROLE", st["label"]),
            Paragraph("LINKED FIRs", st["label"]),
            Paragraph("RECORDED CRIMES", st["label"]),
        ]]
        for person in people:
            data.append([
                _p(person["name"], st["body"]),
                _p(person["role"], st["body"]),
                _p(", ".join(person["firs"]) or "—", st["small"]),
                _p(", ".join(person["crimes"]) or "—", st["small"]),
            ])
        people_table = Table(
            data, colWidths=[43 * mm, 25 * mm, 53 * mm, 41 * mm],
            repeatRows=1, hAlign="LEFT",
        )
        people_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, PALE_BLUE]),
            ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
            ("INNERGRID", (0, 0), (-1, -1), 0.3, BORDER),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(people_table)
    else:
        story.append(Paragraph(
            "No named persons were present in the exported result rows. Use the "
            "criminal-network view to expand a FIR into accused, victim, officer, "
            "and co-accused links.",
            st["body"],
        ))

    # Findings and recommendations
    story.extend(_section_title("04", "AI-ASSISTED FINDINGS & NEXT ACTIONS", st))
    finding_turns = [
        t for t in turns
        if t.get("tool") in (
            "analytics_agent",
            "graph_agent",
            "investigation_briefing",
            "sql_agent",
        )
        and str(t.get("answer") or "").strip()
        and str(t.get("question") or "").strip().lower()
        not in {"(call started)", "(voice)", "(voice call)"}
    ]
    if finding_turns:
        for i, turn in enumerate(finding_turns[:12], 1):
            answer = str(turn.get("answer") or "").strip()
            if not answer:
                continue
            story.append(KeepTogether([
                Paragraph(f"Finding {i:02d} · {_text(turn.get('question'))}", st["h2"]),
                Paragraph(_text(answer, 900), st["body"]),
            ]))
    else:
        story.append(Paragraph(
            "No analytical or case findings were generated in this session.",
            st["body"],
        ))
    story.append(Spacer(1, 2 * mm))
    story.append(_kv_table([
        ("Validation", "Confirm every identity, FIR number, and status against the source record."),
        ("Network review", "Review co-accused and repeat-person links before treating them as associations."),
        ("Evidence preservation", "Record CCTV, witness, device, vehicle, and forensic collection in the case diary."),
        ("Supervisory review", "Early-warning and behavioral outputs are decision support, not proof of guilt."),
    ], st))

    # Conversation appendix
    story.append(PageBreak())
    story.extend(_section_title("A", "INVESTIGATION CONVERSATION APPENDIX", st))
    story.append(Paragraph(
        "This appendix preserves the investigator's questions and assistant "
        "briefings. It is supporting context, not the primary report body.",
        st["small"],
    ))
    for i, turn in enumerate(turns, 1):
        story.append(Paragraph(f"TURN {i:02d} · INVESTIGATOR", st["h2"]))
        story.append(Paragraph(_text(turn.get("question")), st["body"]))
        story.append(Paragraph("CRIME INTELLIGENCE ASSISTANT", st["label"]))
        story.append(Paragraph(_text(turn.get("answer"), 1600), st["body"]))
        story.append(Spacer(1, 2 * mm))

    # Audit appendix
    audit_turns = [t for t in turns if t.get("query") or t.get("tool")]
    if audit_turns:
        story.append(PageBreak())
        story.extend(_section_title("B", "EXPLAINABILITY & AUDIT TRAIL", st))
        story.append(Paragraph(
            "Queries below identify the deterministic briefing, SQL, Cypher, or "
            "analytics source used for each finding.",
            st["small"],
        ))
        for i, turn in enumerate(audit_turns, 1):
            story.append(Paragraph(
                f"Audit {i:02d} · {_text(turn.get('tool') or 'unknown tool')}",
                st["h2"],
            ))
            story.append(Paragraph(_text(turn.get("question")), st["small"]))
            story.append(Paragraph(_text(turn.get("query") or "No query captured", 2400), st["code"]))
            story.append(Spacer(1, 2 * mm))

    story.extend([
        Spacer(1, 6 * mm),
        HRFlowable(width="100%", thickness=0.7, color=BORDER),
        Spacer(1, 3 * mm),
        Paragraph(
            "DISCLAIMER · AI-generated investigative assistance. Verify all facts "
            "against SCRB/FIR source records. This report does not establish guilt.",
            st["center"],
        ),
    ])

    doc.build(story, onFirstPage=_page_chrome, onLaterPages=_page_chrome)
    return buffer.getvalue()
