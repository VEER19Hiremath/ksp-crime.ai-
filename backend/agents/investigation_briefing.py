"""Deterministic investigation briefings for the demo call-flow.

Produces FIR cards, accused/victim lists, repeat-offender checks, network
summaries, and "investigate next" tips from live Postgres — no LLM round-trip.
"""
from __future__ import annotations

import re
from typing import Any

from core.db import run_read_only_query
from core.language import resolve_reply_language


def _crime_no_from(q: str, crime_no: str | None = None) -> str | None:
    if crime_no and str(crime_no).strip():
        return str(crime_no).strip()
    m = re.search(r"\b(\d{10,})\b", q or "")
    return m.group(1) if m else None


def _person_from(q: str) -> str | None:
    m = re.search(
        r"(?i)(?:is\s+)?([A-Za-z][A-Za-z.\s]{1,40}?)\s+(?:a\s+)?repeat\s+offender",
        q or "",
    )
    if m:
        return m.group(1).strip(" .?")
    m = re.search(
        r"(?i)(?:network(?:\s+analysis)?\s+for|associates?\s+of|profile\s+of|"
        r"tell\s+me\s+about|cases?\s+(?:for|of|against)|involving)\s+"
        r"([A-Za-z][A-Za-z.\s]{1,40})",
        q or "",
    )
    if m:
        name = m.group(1).strip(" .?")
        if name.lower() not in ("crime", "case", "fir", "the", "this"):
            return name
    m = re.search(r"([\u0C80-\u0CFF][\u0C80-\u0CFF\s]{1,40})\s*ಪುನರಾವರ್ತಿತ", q or "")
    if m:
        return m.group(1).strip()
    return None


def _fetch_case(crime_no: str) -> dict | None:
    safe = crime_no.replace("'", "''")
    rows = run_read_only_query(
        f"""
        SELECT cm.case_master_id, cm.crime_no, cm.case_no, cm.brief_facts,
               cm.crime_registered_date,
               u.unit_name, d.district_name,
               csh.crime_head_name, ch.crime_group_name,
               csm.case_status_name, e.first_name AS officer_name
        FROM case_master cm
        JOIN unit u ON u.unit_id = cm.police_station_id
        LEFT JOIN district d ON d.district_id = u.district_id
        JOIN crime_sub_head csh ON csh.crime_sub_head_id = cm.crime_minor_head_id
        LEFT JOIN crime_head ch ON ch.crime_head_id = csh.crime_head_id
        LEFT JOIN case_status_master csm ON csm.case_status_id = cm.case_status_id
        LEFT JOIN employee e ON e.employee_id = cm.police_person_id
        WHERE cm.crime_no::text = '{safe}'
           OR cm.crime_no::text ILIKE '%{safe}%'
           OR cm.case_no::text = '{safe}'
        LIMIT 1
        """
    )
    return rows[0] if rows else None


def _case_people(case_master_id: int) -> dict[str, list[dict]]:
    accused = run_read_only_query(
        f"""
        SELECT accused_name AS name, age_year AS age, 'accused' AS role,
               accused_master_id AS id
        FROM accused WHERE case_master_id = {int(case_master_id)}
        ORDER BY accused_master_id
        """
    )
    victims = run_read_only_query(
        f"""
        SELECT victim_name AS name, age_year AS age, 'victim' AS role,
               victim_master_id AS id
        FROM victim WHERE case_master_id = {int(case_master_id)}
        ORDER BY victim_master_id
        """
    )
    return {"accused": accused, "victims": victims}


def _person_cases(name: str) -> list[dict]:
    safe = name.replace("'", "''")
    return run_read_only_query(
        f"""
        SELECT DISTINCT cm.crime_no, cm.brief_facts, u.unit_name, csh.crime_head_name,
               a.accused_name AS person_name, a.age_year
        FROM case_master cm
        JOIN accused a ON a.case_master_id = cm.case_master_id
        JOIN unit u ON u.unit_id = cm.police_station_id
        JOIN crime_sub_head csh ON csh.crime_sub_head_id = cm.crime_minor_head_id
        WHERE a.accused_name ILIKE '%{safe}%'
        ORDER BY cm.crime_no
        LIMIT 25
        """
    )


def _similar_cases(crime_head: str, unit_name: str | None = None) -> list[dict]:
    c = (crime_head or "").replace("'", "''")
    unit_filter = ""
    if unit_name:
        u = unit_name.replace("'", "''")
        unit_filter = f"OR u.unit_name ILIKE '%{u}%'"
    return run_read_only_query(
        f"""
        SELECT cm.crime_no, cm.brief_facts, u.unit_name, csh.crime_head_name,
               csm.case_status_name
        FROM case_master cm
        JOIN unit u ON u.unit_id = cm.police_station_id
        JOIN crime_sub_head csh ON csh.crime_sub_head_id = cm.crime_minor_head_id
        LEFT JOIN case_status_master csm ON csm.case_status_id = cm.case_status_id
        WHERE csh.crime_head_name ILIKE '%{c}%' {unit_filter}
        ORDER BY cm.case_master_id DESC
        LIMIT 8
        """
    )


def _suggestions(kind: str, *, crime_no: str | None = None, person: str | None = None, kn: bool = False) -> list[dict]:
    cn = crime_no or ""
    p = person or ""
    if kn:
        packs = {
            "fir": [
                {"label": "ಪ್ರಕರಣದ ಸಾರಾಂಶ", "message": f"ಪ್ರಕರಣದ ಸಾರಾಂಶ crime {cn}"},
                {"label": "ಆರೋಪಿತರು", "message": f"ಆರೋಪಿತರನ್ನು ತೋರಿಸಿ crime {cn}"},
                {"label": "ಪೀಡಿತರು", "message": f"ಪೀಡಿತರನ್ನು ತೋರಿಸಿ crime {cn}"},
                {"label": "ತನಿಖಾ ಹಂತಗಳು", "message": f"ತನಿಖೆಯ ಹಂತಗಳು crime {cn}"},
                {"label": "ಅಪರಾಧ ಜಾಲ", "message": f"ಅಪರಾಧ ಜಾಲ crime {cn}"},
                {"label": "ವರದಿ", "message": "ತನಿಖಾ ವರದಿ ರಚಿಸಿ"},
            ],
            "summary": [
                {"label": "ಆರೋಪಿತರು", "message": f"ಆರೋಪಿತರನ್ನು ತೋರಿಸಿ crime {cn}"},
                {"label": "ಹೋಲಿಕೆಯ ಪ್ರಕರಣ", "message": f"ಇದೇ ರೀತಿಯ ಪ್ರಕರಣಗಳನ್ನು ಹುಡುಕಿ crime {cn}"},
                {"label": "ಜಾಲ", "message": f"ಅಪರಾಧ ಜಾಲ crime {cn}"},
            ],
            "accused": [
                {"label": "ಪುನರಾವರ್ತಿತ?", "message": f"{p} ಪುನರಾವರ್ತಿತ ಆರೋಪಿಯೇ?" if p else "ಪುನರಾವರ್ತಿತ ಆರೋಪಿ?"},
                {"label": "ಜಾಲ", "message": f"{p} ಅಪರಾಧ ಜಾಲ" if p else f"ಅಪರಾಧ ಜಾಲ crime {cn}"},
                {"label": "ಮುಂದಿನ ತನಿಖೆ", "message": f"ಮುಂದಿನ ತನಿಖೆಯಲ್ಲಿ ನಾನು ಏನು ಮಾಡಬೇಕು crime {cn}"},
            ],
            "repeat": [
                {"label": "ಜಾಲ", "message": f"{p} ಅಪರಾಧ ಜಾಲವನ್ನು ತೋರಿಸಿ"},
                {"label": "ಹೋಲಿಕೆಯ ಪ್ರಕರಣ", "message": f"ಇದೇ ರೀತಿಯ ಪ್ರಕರಣಗಳನ್ನು ಹುಡುಕಿ"},
            ],
            "network": [
                {"label": "ಮುಂದಿನ ತನಿಖೆ", "message": f"ಮುಂದಿನ ತನಿಖೆಯಲ್ಲಿ ನಾನು ಏನು ಮಾಡಬೇಕು crime {cn}" if cn else "ಮುಂದಿನ ತನಿಖೆಯಲ್ಲಿ ನಾನು ಏನು ಮಾಡಬೇಕು?"},
                {"label": "ವರದಿ", "message": "ತನಿಖಾ ವರದಿ ರಚಿಸಿ"},
            ],
            "next": [
                {"label": "ಜಾಲ", "message": f"ಅಪರಾಧ ಜಾಲ crime {cn}" if cn else "ಅಪರಾಧ ಜಾಲ"},
                {"label": "ವರದಿ", "message": "ತನಿಖಾ ವರದಿ ರಚಿಸಿ"},
            ],
            "export": [
                {"label": "ಆರಂಭಿಕ ಎಚ್ಚರಿಕೆ", "message": "ಆರಂಭಿಕ ಎಚ್ಚರಿಕೆಗಳು ಏನು?"},
            ],
        }
    else:
        packs = {
            "fir": [
                {"label": "View Case Summary", "message": f"Case summary for crime {cn}"},
                {"label": "Show Accused", "message": f"Show accused for crime {cn}"},
                {"label": "Show Victims", "message": f"Show victims for crime {cn}"},
                {"label": "Show Timeline", "message": f"Show timeline for crime {cn}"},
                {"label": "Criminal Network", "message": f"Show criminal network for crime {cn}"},
                {"label": "Export Report", "message": "Export investigation report"},
            ],
            "summary": [
                {"label": "Show accused", "message": f"Show accused for crime {cn}"},
                {"label": "Similar cases", "message": f"Find similar cases for crime {cn}"},
                {"label": "Criminal network", "message": f"Show criminal network for crime {cn}"},
            ],
            "accused": [
                {"label": "Repeat offender?", "message": f"Is {p} a repeat offender?" if p else "Repeat offender check"},
                {"label": "Criminal Network", "message": f"Show criminal network for {p}" if p else f"Show criminal network for crime {cn}"},
                {"label": "Investigate next", "message": f"What should I investigate next for crime {cn}"},
            ],
            "repeat": [
                {"label": "View criminal network", "message": f"Show criminal network for {p}"},
                {"label": "Show similar cases", "message": f"Find similar cases involving {p}"},
            ],
            "network": [
                {"label": "Investigate next", "message": f"What should I investigate next for crime {cn}" if cn else "What should I investigate next?"},
                {"label": "Export Report", "message": "Export investigation report"},
            ],
            "next": [
                {"label": "Criminal network", "message": f"Show criminal network for crime {cn}" if cn else "Show criminal network"},
                {"label": "Export Report", "message": "Export investigation report"},
            ],
            "export": [
                {"label": "Early warnings", "message": "What are the early warnings?"},
            ],
        }
    return packs.get(kind, packs["fir"])


def _fmt_date(row: dict) -> str:
    d = row.get("crime_registered_date") or row.get("registration_date")
    if d:
        try:
            return d.strftime("%d %B %Y") if hasattr(d, "strftime") else str(d)
        except Exception:
            return str(d)
    return "—"


def _fir_card(case: dict, kn: bool) -> str:
    if kn:
        return (
            f"FIR ದೊರಕಿದೆ\n\n"
            f"ಅಪರಾಧ ಸಂಖ್ಯೆ\n{case.get('crime_no')}\n\n"
            f"ಅಪರಾಧ\n{case.get('crime_head_name') or '—'}\n\n"
            f"ಪೊಲೀಸ್ ಠಾಣೆ\n{case.get('unit_name') or '—'}\n\n"
            f"ದಾಖಲಾದ ದಿನಾಂಕ\n{_fmt_date(case)}\n\n"
            f"ಪ್ರಸ್ತುತ ಸ್ಥಿತಿ\n{case.get('case_status_name') or 'ತನಿಖೆ ನಡೆಯುತ್ತಿದೆ'}\n\n"
            f"ತನಿಖಾಧಿಕಾರಿ\n{case.get('officer_name') or '—'}\n\n"
            f"ಸಂಕ್ಷಿಪ್ತ ಸಂಗತಿ\n{case.get('brief_facts') or '—'}\n\n"
            f"ಮುಂದೆ ಏನು ನೋಡಲು ಬಯಸುತ್ತೀರಿ?"
        )
    return (
        f"FIR Found\n\n"
        f"Crime Number\n{case.get('crime_no')}\n\n"
        f"Crime Type\n{case.get('crime_head_name') or '—'}\n\n"
        f"Police Station\n{case.get('unit_name') or '—'}\n\n"
        f"Registered On\n{_fmt_date(case)}\n\n"
        f"Status\n{case.get('case_status_name') or 'Under Investigation'}\n\n"
        f"Investigating Officer\n{case.get('officer_name') or '—'}\n\n"
        f"Brief Facts\n{case.get('brief_facts') or '—'}\n\n"
        f"What would you like to do next?"
    )


def run_investigation_briefing(
    question: str,
    *,
    language_code: str = "en-IN",
    crime_no: str | None = None,
) -> dict | None:
    """Return a briefing dict or None if the question is not a demo-flow intent."""
    q = (question or "").strip()
    if not q:
        return None
    lang = resolve_reply_language(q, language_code)
    kn = lang == "kn-IN"
    cn = _crime_no_from(q, crime_no)

    # Export report — UI generates PDF; we acknowledge.
    if re.search(
        r"(?i)\b(export\s+(?:investigation\s+)?report|generate\s+(?:investigation\s+)?report)\b|"
        r"ತನಿಖಾ\s*ವರದಿ|ವರದಿ\s*ರಚಿಸಿ",
        q,
    ):
        answer = (
            "ವರದಿ ಸಿದ್ಧವಾಗುತ್ತಿದೆ...\n\n"
            "ಇವು ಒಳಗೊಂಡಿವೆ\n"
            "✓ ಪ್ರಕರಣದ ಸಾರಾಂಶ\n✓ ತನಿಖೆಯ ಹಂತಗಳು\n✓ ಪೀಡಿತರ ವಿವರ\n✓ ಆರೋಪಿತರ ವಿವರ\n"
            "✓ ಅಪರಾಧ ಜಾಲ\n✓ ತನಿಖಾ ಟಿಪ್ಪಣಿಗಳು\n✓ AI ಸಲಹೆಗಳು\n\n"
            "ವರದಿ ಸಿದ್ಧವಾಗಿದೆ — ಮೇಲಿನ Export PDF ಬಟನ್ ಬಳಸಿ ಡೌನ್‌ಲೋಡ್ ಮಾಡಿ."
            if kn
            else "Generating Report...\n\n"
            "Included\n"
            "✓ Case Summary\n✓ Timeline\n✓ Victims\n✓ Accused\n"
            "✓ Criminal Network\n✓ Investigation Notes\n✓ AI Recommendations\n\n"
            "Report Ready — use the Export PDF button above to download."
        )
        return {
            "answer": answer,
            "tool": "investigation_briefing",
            "query": "export_report_ack",
            "rows": [],
            "suggestions": _suggestions("export", kn=kn),
            "language_code": lang,
        }

    # Repeat offender
    if re.search(r"(?i)\brepeat\s+offender\b|ಪುನರಾವರ್ತಿತ", q):
        person = _person_from(q)
        if not person:
            return None
        cases = _person_cases(person)
        types = sorted({str(c.get("crime_head_name")) for c in cases if c.get("crime_head_name")})
        n = len(cases)
        risk = "High" if n >= 3 else ("Medium" if n == 2 else "Low")
        risk_kn = "ಹೆಚ್ಚು" if n >= 3 else ("ಮಧ್ಯಮ" if n == 2 else "ಕಡಿಮೆ")
        if kn:
            answer = (
                f"{'ಹೌದು' if n >= 2 else 'ಇಲ್ಲ'}.\n\n"
                f"{person}\n{n} FIRಗಳಲ್ಲಿ ಕಾಣಿಸಿಕೊಂಡಿದ್ದಾರೆ.\n\n"
                f"ಅಪರಾಧಗಳು\n" + "".join(f"- {t}\n" for t in types[:6]) +
                f"\nಅಪಾಯದ ಮಟ್ಟ\n{risk_kn}"
            )
        else:
            answer = (
                f"{'Yes' if n >= 2 else 'No'}.\n\n"
                f"{person} appears in\n{n} FIRs.\n\n"
                f"Crime Types\n" + "".join(f"- {t}\n" for t in types[:6]) +
                f"\nRisk Level\n{risk}"
            )
        return {
            "answer": answer,
            "tool": "investigation_briefing",
            "query": f"repeat_offender:{person}",
            "rows": cases,
            "suggestions": _suggestions("repeat", person=person, kn=kn),
            "language_code": lang,
        }

    # Investigate next
    if re.search(
        r"(?i)\b(what should i investigate|investigate next|next steps?)\b|"
        r"ಮುಂದಿನ\s*ತನಿಖ|ನಾನು\s*ಏನು\s*ಮಾಡಬೇಕು",
        q,
    ):
        case = _fetch_case(cn) if cn else None
        people = _case_people(int(case["case_master_id"])) if case else {"accused": [], "victims": []}
        wit = (people["victims"][0]["name"] if people["victims"] else None) or "local witness"
        if kn:
            answer = (
                "AI ತನಿಖಾ ಸಲಹೆಗಳು\n\n"
                f"1.\n{wit} ಸಾಕ್ಷಿಯನ್ನು ವಿಚಾರಿಸಿ\nಬಾಕಿ ಇದೆ\n\n"
                "2.\nಸಮೀಪದ CCTV ಸಂಗ್ರಹಿಸಿ\nಬಾಕಿ ಇದೆ\n\n"
                "3.\nಬೆರಳಚ್ಚು / ಫೋರೆನ್ಸಿಕ್ ವರದಿ\nನಿರೀಕ್ಷೆಯಲ್ಲಿದೆ\n\n"
                "4.\nವಾಹನ / ಮೊಬೈಲ್ ಪರಿಶೀಲನೆ\nಬಾಕಿ ಇದೆ"
            )
        else:
            answer = (
                "AI Investigation Suggestions\n\n"
                f"1.\nInterview witness\n{wit}\nPending\n\n"
                "2.\nCollect CCTV\nNearby area / ATM\nPending\n\n"
                "3.\nFingerprint / forensic report\nAwaiting\n\n"
                "4.\nVehicle / mobile verification\nPending"
            )
        rows = [case] if case else []
        return {
            "answer": answer,
            "tool": "investigation_briefing",
            "query": f"investigate_next:{cn or 'general'}",
            "rows": rows,
            "suggestions": _suggestions("next", crime_no=cn, kn=kn),
            "language_code": lang,
        }

    # Needs a crime number for the rest
    if not cn:
        # Network for person without crime no
        if re.search(r"(?i)\b(criminal network|network analysis)\b|ಅಪರಾಧ\s*ಜಾಲ", q):
            person = _person_from(q)
            if person:
                cases = _person_cases(person)
                n = len(cases)
                if kn:
                    answer = (
                        f"ಅಪರಾಧ ಜಾಲ\n\nನೇರ ಸಂಪರ್ಕಗಳು\n{max(1, n)}\n\n"
                        f"ಸಂಬಂಧಿತ FIRಗಳು\n{n}\n\n"
                        f"AI ವಿಶ್ಲೇಷಣೆ\n{person} ಅನೇಕ ಪ್ರಕರಣಗಳಲ್ಲಿ ಕಾಣಿಸಿಕೊಂಡಿದ್ದಾರೆ."
                    )
                else:
                    answer = (
                        f"Network Analysis\n\nDirect Associates\n{max(1, n)}\n\n"
                        f"Connected FIRs\n{n}\n\n"
                        f"AI Insight\n{person} appears across multiple linked cases."
                    )
                return {
                    "answer": answer,
                    "tool": "investigation_briefing",
                    "query": f"network:{person}",
                    "rows": cases,
                    "suggestions": _suggestions("network", person=person, kn=kn),
                    "language_code": lang,
                }
        return None

    case = _fetch_case(cn)
    if not case:
        return None
    cid = int(case["case_master_id"])
    people = _case_people(cid)
    accused = people["accused"]
    victims = people["victims"]

    # FIR show
    if re.search(
        r"(?i)\b(show|get|find|display|open)?\s*(fir|crime)\b|FIR|ಕ್ರೈಮ್|ಪ್ರಕರಣ",
        q,
    ) and not re.search(
        r"(?i)\b(summary|accused|victim|timeline|network|similar|investigate)\b|"
        r"ಸಾರಾಂಶ|ಆರೋಪಿತ|ಪೀಡಿತ|ಹಂತ|ಜಾಲ|ಹೋಲಿಕೆ|ತನಿಖೆ",
        q,
    ):
        rows = [{**case, "person_name": accused[0]["name"] if accused else None, "role": "accused"}]
        return {
            "answer": _fir_card(case, kn),
            "tool": "investigation_briefing",
            "query": f"fir_card:{cn}",
            "rows": rows,
            "suggestions": _suggestions("fir", crime_no=cn, kn=kn),
            "language_code": lang,
        }

    # Case summary
    if re.search(r"(?i)\b(case summary|summar(?:y|ise|ize))\b|ಪ್ರಕರಣದ\s*ಸಾರಾಂಶ", q):
        if kn:
            answer = (
                f"ಪ್ರಕರಣದ ಸಾರಾಂಶ\n\nಅಪರಾಧ\n{case.get('crime_head_name')}\n\n"
                f"ಪೀಡಿತರು\n{len(victims)}\n\nಆರೋಪಿತರು\n{len(accused)}\n\n"
                f"ಪ್ರಸ್ತುತ ಸ್ಥಿತಿ\n{case.get('case_status_name') or 'ತನಿಖೆ ನಡೆಯುತ್ತಿದೆ'}\n\n"
                f"ಸಂಕ್ಷಿಪ್ತ\n{case.get('brief_facts') or '—'}"
            )
        else:
            answer = (
                f"Case Summary\n\nCrime\n{case.get('crime_head_name')}\n\n"
                f"Victims\n{len(victims)}\n\nAccused\n{len(accused)}\n\n"
                f"Current Status\n{case.get('case_status_name') or 'Under Investigation'}\n\n"
                f"Brief Facts\n{case.get('brief_facts') or '—'}"
            )
        return {
            "answer": answer,
            "tool": "investigation_briefing",
            "query": f"case_summary:{cn}",
            "rows": [case],
            "suggestions": _suggestions("summary", crime_no=cn, kn=kn),
            "language_code": lang,
        }

    # Accused
    if re.search(r"(?i)\b(show|list)?\s*accused\b|ಆರೋಪಿತ", q):
        lines = ["ಆರೋಪಿತರು" if kn else "Accused", ""]
        for i, a in enumerate(accused, 1):
            lines.append(f"A{i}")
            lines.append(str(a.get("name") or "—"))
            lines.append("ವಯಸ್ಸು" if kn else "Age")
            lines.append(str(a.get("age") or "—"))
            lines.append("ಸ್ಥಿತಿ" if kn else "Status")
            lines.append("ತನಿಖೆಯಲ್ಲಿದೆ" if kn else "Under investigation")
            lines.append("")
        if not accused:
            lines.append("ಯಾರೂ ದಾಖಲಾಗಿಲ್ಲ." if kn else "No accused recorded for this FIR.")
        person = accused[0]["name"] if accused else None
        rows = [
            {
                "crime_no": cn,
                "person_name": a.get("name"),
                "role": "accused",
                "unit_name": case.get("unit_name"),
                "crime_head_name": case.get("crime_head_name"),
                "brief_facts": case.get("brief_facts"),
            }
            for a in accused
        ] or [case]
        return {
            "answer": "\n".join(lines).strip(),
            "tool": "investigation_briefing",
            "query": f"accused:{cn}",
            "rows": rows,
            "suggestions": _suggestions("accused", crime_no=cn, person=person, kn=kn),
            "language_code": lang,
        }

    # Victims
    if re.search(r"(?i)\b(show|list)?\s*victims?\b|ಪೀಡಿತ", q):
        lines = ["ಪೀಡಿತರು" if kn else "Victims", ""]
        for i, v in enumerate(victims, 1):
            lines.append(f"V{i}")
            lines.append(str(v.get("name") or "—"))
            lines.append("ವಯಸ್ಸು" if kn else "Age")
            lines.append(str(v.get("age") or "—"))
            lines.append("")
        if not victims:
            lines.append("ಯಾರೂ ದಾಖಲಾಗಿಲ್ಲ." if kn else "No victims recorded for this FIR.")
        return {
            "answer": "\n".join(lines).strip(),
            "tool": "investigation_briefing",
            "query": f"victims:{cn}",
            "rows": [case],
            "suggestions": _suggestions("summary", crime_no=cn, kn=kn),
            "language_code": lang,
        }

    # Timeline
    if re.search(r"(?i)\b(timeline|stages)\b|ತನಿಖೆಯ\s*ಹಂತ", q):
        if kn:
            answer = (
                f"ತನಿಖೆಯ ಹಂತಗಳು — {cn}\n\n"
                f"1. FIR ದಾಖಲು — {_fmt_date(case)} — ಪೂರ್ಣ\n"
                f"2. ಆರಂಭಿಕ ತನಿಖೆ — {case.get('officer_name') or 'IO'} — ನಡೆಯುತ್ತಿದೆ\n"
                "3. ಸಾಕ್ಷಿ / CCTV ಸಂಗ್ರಹ — ಬಾಕಿ\n"
                "4. ಆರೋಪಪತ್ರ — ಬಾಕಿ"
            )
        else:
            answer = (
                f"Investigation Timeline — {cn}\n\n"
                f"1. FIR registered — {_fmt_date(case)} — Done\n"
                f"2. Initial enquiry — {case.get('officer_name') or 'IO'} — In progress\n"
                "3. Witness / CCTV collection — Pending\n"
                "4. Charge sheet — Pending"
            )
        return {
            "answer": answer,
            "tool": "investigation_briefing",
            "query": f"timeline:{cn}",
            "rows": [case],
            "suggestions": _suggestions("next", crime_no=cn, kn=kn),
            "language_code": lang,
        }

    # Similar cases
    if re.search(r"(?i)\bsimilar cases?\b|ಇದೇ\s*ರೀತಿಯ\s*ಪ್ರಕರಣ", q):
        sim = _similar_cases(str(case.get("crime_head_name") or ""), str(case.get("unit_name") or ""))
        head = "ಇದೇ ರೀತಿಯ ಪ್ರಕರಣಗಳು" if kn else "Similar cases"
        lines = [head, ""]
        for i, r in enumerate(sim[:6], 1):
            lines.append(
                f"{i}. {r.get('unit_name')} · {r.get('crime_head_name')} · {r.get('crime_no')}"
            )
        return {
            "answer": "\n".join(lines),
            "tool": "investigation_briefing",
            "query": f"similar:{cn}",
            "rows": sim,
            "suggestions": _suggestions("summary", crime_no=cn, kn=kn),
            "language_code": lang,
        }

    # Network for this crime
    if re.search(r"(?i)\b(criminal network|network analysis|show (?:his |her )?network)\b|ಅಪರಾಧ\s*ಜಾಲ", q):
        n_acc = len(accused)
        if kn:
            answer = (
                f"ಅಪರಾಧ ಜಾಲ\n\nನೇರ ಸಂಪರ್ಕಗಳು\n{n_acc}\n\n"
                f"ಸಂಬಂಧಿತ FIRಗಳು\n1\n\n"
                f"AI ವಿಶ್ಲೇಷಣೆ\n"
                + (
                    f"{accused[0]['name']} ಈ ಪ್ರಕರಣದಲ್ಲಿ ಇತರ ಆರೋಪಿತರೊಂದಿಗೆ ಸಂಪರ್ಕ ಹೊಂದಿದ್ದಾರೆ."
                    if accused
                    else "ಈ FIR ಗೆ ಸಂಬಂಧಿಸಿದ ವ್ಯಕ್ತಿ ಜಾಲವನ್ನು ಕೆಳಗಿನ ನೆಟ್‌ವರ್ಕ್‌ನಲ್ಲಿ ನೋಡಿ."
                )
            )
        else:
            answer = (
                f"Network Analysis\n\nDirect Associates\n{n_acc}\n\n"
                f"Connected FIRs\n1\n\n"
                f"AI Insight\n"
                + (
                    f"{accused[0]['name']} is linked with co-accused on this FIR."
                    if accused
                    else "Open the network panel below for people linked to this FIR."
                )
            )
        rows = [
            {
                "crime_no": cn,
                "person_name": a.get("name"),
                "role": "accused",
                "unit_name": case.get("unit_name"),
                "crime_head_name": case.get("crime_head_name"),
                "brief_facts": case.get("brief_facts"),
            }
            for a in accused
        ] or [case]
        person = accused[0]["name"] if accused else None
        return {
            "answer": answer,
            "tool": "investigation_briefing",
            "query": f"network_case:{cn}",
            "rows": rows,
            "suggestions": _suggestions("network", crime_no=cn, person=person, kn=kn),
            "language_code": lang,
        }

    # Bare "Show FIR <number>" already handled; also accept explain crime
    if re.search(r"(?i)\b(explain|detail|about)\b.*\b(crime|fir|case)\b|ಕ್ರೈಮ್|FIR", q):
        rows = [{**case, "person_name": accused[0]["name"] if accused else None}]
        return {
            "answer": _fir_card(case, kn),
            "tool": "investigation_briefing",
            "query": f"fir_card:{cn}",
            "rows": rows,
            "suggestions": _suggestions("fir", crime_no=cn, kn=kn),
            "language_code": lang,
        }

    return None
