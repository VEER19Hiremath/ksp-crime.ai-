"""NL -> SQL over the KSP schema (Neon), grounded with the schema + a few-shot
prompt. Runs the generated SQL read-only (see core/db.py) and returns rows plus
the SQL itself so the response stays explainable/auditable."""
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage

from core.db import run_read_only_query
from core.llm import get_llm

logger = logging.getLogger(__name__)

SCHEMA_SUMMARY = """
Tables (snake_case, PostgreSQL):
case_master(case_master_id, crime_no, case_no, crime_registered_date, police_person_id,
  police_station_id, case_category_id, gravity_offence_id, crime_major_head_id,
  crime_minor_head_id, case_status_id, court_id, incident_from_date, incident_to_date,
  latitude, longitude, brief_facts)
accused(accused_master_id, case_master_id, accused_name, age_year, gender_id, person_id)
victim(victim_master_id, case_master_id, victim_name, age_year, gender_id, victim_police)
complainant_details(complainant_id, case_master_id, complainant_name, age_year,
  occupation_id, religion_id, caste_id, gender_id)
arrest_surrender(arrest_surrender_id, case_master_id, arrest_surrender_date, io_id,
  court_id, accused_master_id, is_accused, is_complainant_accused)
chargesheet_details(cs_id, case_master_id, cs_date, cs_type, police_person_id)
employee(employee_id, district_id, unit_id, rank_id, designation_id, first_name)
unit(unit_id, unit_name, type_id, parent_unit, state_id, district_id)
district(district_id, district_name, state_id)
court(court_id, court_name, district_id, state_id)
case_category(case_category_id, lookup_value)         -- FIR, UDR, Zero FIR, PAR
gravity_offence(gravity_offence_id, lookup_value)      -- Heinous, Non-Heinous
crime_head(crime_head_id, crime_group_name)            -- e.g. Crimes Against Property
crime_sub_head(crime_sub_head_id, crime_head_id, crime_head_name)  -- e.g. Robbery, Murder
case_status_master(case_status_id, case_status_name)   -- Under Investigation, Charge Sheeted, Closed
act(act_code, act_description, short_name)
section(act_code, section_code, section_description)
act_section_association(case_master_id, act_id, section_id)

Joins of note: case_master.police_station_id -> unit.unit_id -> unit.unit_name (station name, e.g. Hubballi);
case_master.crime_minor_head_id -> crime_sub_head.crime_head_name (crime type, e.g. Robbery);
case_master.case_status_id -> case_status_master.case_status_name (pending = 'Under Investigation');
accused.case_master_id -> case_master — use this for "crimes / cases by person name".
"""

SYSTEM_PROMPT = f"""You are a PostgreSQL expert generating read-only SQL for a Karnataka Police
crime database. Given the investigator's question and the schema below, output ONLY a single
SELECT statement (no explanation, no markdown fences). Never write INSERT/UPDATE/DELETE/DDL.

For any free-text name filter (station/unit names, crime head/sub-head names, person names,
district/court names), use case-insensitive partial matching — e.g. `u.unit_name ILIKE '%hubballi%'`
— never an exact `=` match, since investigators say "Hubballi" but the stored name is often
"Hubballi Rural PS" / "Hubballi City PS" etc.

When free-text filters are used, fix obvious spelling mistakes to match database
labels (e.g. investigator types "kidnaping" but crime_head_name is "Kidnapping").
Use ILIKE '%corrected%' against crime_sub_head.crime_head_name / unit names / person names.

When the question names a person and asks for crimes / cases / history / what they did,
JOIN accused and filter `a.accused_name ILIKE '%name%'`. Also check victim/complainant
and registering officer (`employee.first_name` via `case_master.police_person_id`) when
the person may be an officer ("cases handled by …", "officer …").

When the question asks for cases handled / registered / investigated by an officer,
JOIN employee on case_master.police_person_id and filter `e.first_name ILIKE '%name%'`.

When the question describes an incident in words (e.g. "gold chain snatched", "near railway"),
filter `case_master.brief_facts ILIKE '%…%'` (and still join station/crime type for the SELECT list).

Always SELECT useful briefing columns when listing cases, e.g.:
  cm.crime_no, cm.case_no, cm.brief_facts, u.unit_name, csh.crime_head_name
LIMIT results to 25 unless the investigator asks for a count only.

Examples:
Q: Show robbery cases in Hubballi.
SQL: SELECT cm.crime_no, cm.case_no, cm.brief_facts, u.unit_name, csh.crime_head_name
FROM case_master cm
JOIN unit u ON u.unit_id = cm.police_station_id
JOIN crime_sub_head csh ON csh.crime_sub_head_id = cm.crime_minor_head_id
WHERE u.unit_name ILIKE '%hubballi%' AND csh.crime_head_name ILIKE '%robbery%'
LIMIT 25

Q: gold chain snatched
SQL: SELECT cm.crime_no, cm.case_no, cm.brief_facts, u.unit_name, csh.crime_head_name
FROM case_master cm
JOIN unit u ON u.unit_id = cm.police_station_id
JOIN crime_sub_head csh ON csh.crime_sub_head_id = cm.crime_minor_head_id
WHERE cm.brief_facts ILIKE '%gold chain%'
LIMIT 25

Q: Vijay Kumar list the crimes he did
SQL: SELECT cm.crime_no, cm.case_no, cm.brief_facts, u.unit_name, csh.crime_head_name, a.accused_name
FROM case_master cm
JOIN accused a ON a.case_master_id = cm.case_master_id
JOIN unit u ON u.unit_id = cm.police_station_id
JOIN crime_sub_head csh ON csh.crime_sub_head_id = cm.crime_minor_head_id
WHERE a.accused_name ILIKE '%Vijay Kumar%'
LIMIT 25

Q: Show accused history for Yusuf Ali
SQL: SELECT DISTINCT cm.crime_no, cm.case_no, cm.brief_facts, u.unit_name, csh.crime_head_name,
  COALESCE(a.accused_name, v.victim_name, cd.complainant_name) AS person_name,
  CASE WHEN a.accused_master_id IS NOT NULL THEN 'accused'
       WHEN v.victim_master_id IS NOT NULL THEN 'victim' ELSE 'complainant' END AS role
FROM case_master cm
JOIN unit u ON u.unit_id = cm.police_station_id
JOIN crime_sub_head csh ON csh.crime_sub_head_id = cm.crime_minor_head_id
LEFT JOIN accused a ON a.case_master_id = cm.case_master_id AND a.accused_name ILIKE '%Yusuf Ali%'
LEFT JOIN victim v ON v.case_master_id = cm.case_master_id AND v.victim_name ILIKE '%Yusuf Ali%'
LEFT JOIN complainant_details cd ON cd.case_master_id = cm.case_master_id AND cd.complainant_name ILIKE '%Yusuf Ali%'
WHERE a.accused_master_id IS NOT NULL OR v.victim_master_id IS NOT NULL OR cd.complainant_id IS NOT NULL
LIMIT 25

Q: list all the cases handled by Santosh
SQL: SELECT cm.crime_no, cm.case_no, cm.brief_facts, u.unit_name, csh.crime_head_name,
  e.first_name AS person_name, 'officer' AS role
FROM case_master cm
JOIN employee e ON e.employee_id = cm.police_person_id
JOIN unit u ON u.unit_id = cm.police_station_id
JOIN crime_sub_head csh ON csh.crime_sub_head_id = cm.crime_minor_head_id
WHERE e.first_name ILIKE '%Santosh%'
LIMIT 25

Q: hey show all the cases related kavya
SQL: SELECT DISTINCT cm.crime_no, cm.case_no, cm.brief_facts, u.unit_name, csh.crime_head_name,
  COALESCE(a.accused_name, v.victim_name, cd.complainant_name) AS person_name,
  CASE WHEN a.accused_master_id IS NOT NULL THEN 'accused'
       WHEN v.victim_master_id IS NOT NULL THEN 'victim' ELSE 'complainant' END AS role
FROM case_master cm
JOIN unit u ON u.unit_id = cm.police_station_id
JOIN crime_sub_head csh ON csh.crime_sub_head_id = cm.crime_minor_head_id
LEFT JOIN accused a ON a.case_master_id = cm.case_master_id AND a.accused_name ILIKE '%kavya%'
LEFT JOIN victim v ON v.case_master_id = cm.case_master_id AND v.victim_name ILIKE '%kavya%'
LEFT JOIN complainant_details cd ON cd.case_master_id = cm.case_master_id AND cd.complainant_name ILIKE '%kavya%'
WHERE a.accused_master_id IS NOT NULL OR v.victim_master_id IS NOT NULL OR cd.complainant_id IS NOT NULL
LIMIT 25

{SCHEMA_SUMMARY}
"""

# "Name ... crimes/cases/history/did" — used for a deterministic fallback when LLM SQL fails.
_ACCUSED_HISTORY_RE = re.compile(
    r"""(?ix)
    ^\s*
    (?P<name>[A-Za-z][A-Za-z.'\-\s]{1,60}?)
    \s+
    (?:
        (?:list|show|find|get|what(?:\s+are|\s+were)?|tell\s+me)?\s*
        (?:the\s+)?(?:crimes?|cases?|offences?|offenses?|firs?|history)
        |
        (?:history|cases?|crimes?)
        |
        (?:list\s+the\s+crimes?\s+he\s+did|what\s+did\s+he\s+do|crimes?\s+he\s+did)
    )
    """,
)

_NAME_FIRST_RE = re.compile(
    r"""(?ix)
    ^\s*(?:show|list|find|get)?\s*
    (?:accused\s+)?(?:history\s+(?:for|of)\s+|cases?\s+(?:for|of|against)\s+|crimes?\s+(?:by|of|for)\s+)?
    (?P<name>[A-Za-z][A-Za-z.'\-\s]{1,40}?)
    \s*(?:['’]s)?\s*
    (?:crimes?|cases?|history|offences?|offenses?)?\s*[.?]?\s*$
    """,
)

# "hey show all the cases related kavya" / "cases involving Kavya Naik"
_RELATED_PERSON_RE = re.compile(
    r"""(?ix)
    (?:(?:hey|hi|hello|please|ok|okay)[,!\s]+)*
    (?:show|list|find|get|display|give|tell\s+me|tell)?\s*
    (?:me\s+)?
    (?:all\s+|any\s+|every\s+)?
    (?:the\s+)?
    (?:cases?|crimes?|firs?|offences?|offenses?|history)
    \s+
    (?:related\s+(?:to\s+)?|involving\s+|linked\s+to\s+|for\s+|of\s+|against\s+|by\s+|about\s+|with\s+|regarding\s+)?
    (?P<name>[A-Za-z][A-Za-z.'\-]{1,40}(?:\s+[A-Za-z][A-Za-z.'\-]{1,40}){0,3})
    \s*[.?!]?\s*$
    """,
)

# "hey tell about kavya" / "tell me about Kavya case" / "what about kavya"
_ABOUT_PERSON_RE = re.compile(
    r"""(?ix)
    (?:(?:hey|hi|hello|please|ok|okay)[,!\s]+)*
    (?:
        (?:tell(?:\s+me)?|show(?:\s+me)?|give(?:\s+me)?|explain|describe)\s+
        (?:me\s+)?(?:about|regarding)\s+
      | (?:what\s+(?:about|of)|about|regarding)\s+
    )
    (?:the\s+)?
    (?P<name>[A-Za-z][A-Za-z.'\-]{1,40}(?:\s+[A-Za-z][A-Za-z.'\-]{1,40}){0,3})
    (?:\s+(?:cases?|crimes?|firs?|history|details?|info(?:rmation)?|records?|profile|file|files))?
    \s*[.?!]?\s*$
    """,
)

# "kavya cases" / "kavya related cases" / "cases of kavya"
_SHORT_PERSON_RE = re.compile(
    r"""(?ix)
    ^\s*(?:(?:hey|hi|hello|please)[,!\s]+)?
    (?:
        (?P<name1>[A-Za-z][A-Za-z.'\-]{2,40}(?:\s+[A-Za-z][A-Za-z.'\-]{1,40}){0,2})
        \s+(?:related\s+)?(?:cases?|crimes?|history|firs?)
      |
        (?:cases?|crimes?|history)\s+(?:of|for|about|related\s+to|involving)\s+
        (?P<name2>[A-Za-z][A-Za-z.'\-]{2,40}(?:\s+[A-Za-z][A-Za-z.'\-]{1,40}){0,2})
    )
    \s*[.?!]?\s*$
    """,
)

# "list all the cases handled by santosh" / "cases registered by Santosh Naik" / "officer Santosh"
_OFFICER_HANDLED_RE = re.compile(
    r"""(?ix)
    (?:(?:hey|hi|hello|please|ok|okay)[,!\s]+)*
    (?:show|list|find|get|display|give|tell(?:\s+me)?)?\s*
    (?:me\s+)?
    (?:all\s+|any\s+|every\s+)?
    (?:the\s+)?
    (?:
        (?:cases?|crimes?|firs?)\s+
        (?:handled|registered|investigated|investigating|worked\s+on|dealt\s+with)\s+by\s+
      | (?:cases?|crimes?|firs?)\s+(?:of|for)\s+(?:officer|io|sho|asi|psi|inspector)\s+
      | (?:officer|io|sho|asi|psi|inspector)\s+
    )
    (?P<name>[A-Za-z][A-Za-z.'\-]{1,40}(?:\s+[A-Za-z][A-Za-z.'\-]{1,40}){0,3})
    \s*[.?!]?\s*$
    """,
)

# Bare person / officer name: "Santosh Naik" / "santosh"
_BARE_NAME_RE = re.compile(
    r"""(?ix)
    ^\s*(?P<name>[A-Za-z][A-Za-z.'\-]{2,40}(?:\s+[A-Za-z][A-Za-z.'\-]{1,40}){0,3})\s*[.?!]?\s*$
    """,
)

_NAME_STOPWORDS = frozenset({
    "hey", "hi", "hello", "please", "show", "list", "find", "get", "display", "give",
    "all", "any", "every", "the", "a", "an", "me", "us", "my", "our",
    "cases", "case", "crimes", "crime", "related", "involving", "linked",
    "to", "for", "of", "by", "against", "with", "about", "and", "or",
    "history", "accused", "victim", "person", "fir", "firs", "offence", "offences",
    "those", "these", "that", "this", "what", "who", "when", "where",
    "tell", "details", "detail", "info", "information", "regarding", "explain",
    "describe", "ok", "okay", "in", "at", "from", "near", "on", "into",
    "robbery", "theft", "murder", "kidnapping", "assault", "cheating", "fraud",
    "handled", "registered", "investigated", "investigating", "officer", "io",
    "sho", "asi", "psi", "inspector", "worked", "dealt",
    "early", "warning", "warnings", "trend", "trends", "hotspot", "hotspots",
    "relation", "relations", "relationship", "relationships", "network", "associates",
    "are", "is", "was", "were", "can", "could", "would", "should",
    "record", "records", "profile", "profiles", "file", "files", "dossier",
})


def _clean_person_name(raw: str) -> str | None:
    # Drop trailing junk often glued onto names: "Santosh Naik records"
    cleaned = re.sub(
        r"(?i)\s+(?:records?|cases?|crimes?|firs?|history|details?|"
        r"info(?:rmation)?|profile|profiles|file|files|dossier)\s*$",
        "",
        (raw or "").strip(" .,"),
    )
    parts = [
        p for p in re.split(r"\s+", cleaned)
        if p and p.lower() not in _NAME_STOPWORDS and not p.isdigit()
    ]
    if not parts:
        return None
    name = " ".join(parts)
    if len(name) < 3 or name.lower() in _NAME_STOPWORDS:
        return None
    # Reject leftover filler phrases like "hey all"
    if all(p.lower() in _NAME_STOPWORDS for p in parts):
        return None
    # Never treat a known crime-head label as a person name.
    lowered = name.lower()
    if lowered in {t.lower() for t in _crime_terms_from_db()}:
        return None
    return name


def _case_by_crime_no_sql(crime_no: str) -> str:
    safe = crime_no.strip().replace("'", "''")
    return f"""
SELECT cm.crime_no, cm.case_no, cm.brief_facts, u.unit_name, csh.crime_head_name,
       ch.crime_group_name, csm.case_status_name, d.district_name,
       a.accused_name, v.victim_name, e.first_name AS officer_name
FROM case_master cm
JOIN unit u ON u.unit_id = cm.police_station_id
LEFT JOIN district d ON d.district_id = u.district_id
JOIN crime_sub_head csh ON csh.crime_sub_head_id = cm.crime_minor_head_id
LEFT JOIN crime_head ch ON ch.crime_head_id = csh.crime_head_id
LEFT JOIN case_status_master csm ON csm.case_status_id = cm.case_status_id
LEFT JOIN accused a ON a.case_master_id = cm.case_master_id
LEFT JOIN victim v ON v.case_master_id = cm.case_master_id
LEFT JOIN employee e ON e.employee_id = cm.police_person_id
WHERE cm.crime_no::text = '{safe}' OR cm.case_no::text = '{safe}'
   OR cm.crime_no::text ILIKE '%{safe}%'
LIMIT 25
""".strip()


def _case_relations_sql(crime_no: str) -> str:
    """People linked to a FIR: accused, victim, complainant, officer, co-accused."""
    safe = crime_no.strip().replace("'", "''")
    return f"""
SELECT cm.crime_no, cm.case_no, cm.brief_facts, u.unit_name, csh.crime_head_name,
       person_name, role, ch.crime_group_name, csm.case_status_name
FROM (
  SELECT cm.case_master_id, cm.crime_no, cm.case_no, cm.brief_facts,
         cm.police_station_id, cm.crime_minor_head_id, cm.case_status_id,
         a.accused_name AS person_name, 'accused' AS role
  FROM case_master cm
  JOIN accused a ON a.case_master_id = cm.case_master_id
  WHERE cm.crime_no::text ILIKE '%{safe}%' OR cm.case_no::text = '{safe}'
  UNION ALL
  SELECT cm.case_master_id, cm.crime_no, cm.case_no, cm.brief_facts,
         cm.police_station_id, cm.crime_minor_head_id, cm.case_status_id,
         v.victim_name, 'victim'
  FROM case_master cm
  JOIN victim v ON v.case_master_id = cm.case_master_id
  WHERE cm.crime_no::text ILIKE '%{safe}%' OR cm.case_no::text = '{safe}'
  UNION ALL
  SELECT cm.case_master_id, cm.crime_no, cm.case_no, cm.brief_facts,
         cm.police_station_id, cm.crime_minor_head_id, cm.case_status_id,
         cd.complainant_name, 'complainant'
  FROM case_master cm
  JOIN complainant_details cd ON cd.case_master_id = cm.case_master_id
  WHERE cm.crime_no::text ILIKE '%{safe}%' OR cm.case_no::text = '{safe}'
  UNION ALL
  SELECT cm.case_master_id, cm.crime_no, cm.case_no, cm.brief_facts,
         cm.police_station_id, cm.crime_minor_head_id, cm.case_status_id,
         e.first_name, 'officer'
  FROM case_master cm
  JOIN employee e ON e.employee_id = cm.police_person_id
  WHERE cm.crime_no::text ILIKE '%{safe}%' OR cm.case_no::text = '{safe}'
) x
JOIN case_master cm ON cm.case_master_id = x.case_master_id
JOIN unit u ON u.unit_id = x.police_station_id
JOIN crime_sub_head csh ON csh.crime_sub_head_id = x.crime_minor_head_id
LEFT JOIN crime_head ch ON ch.crime_head_id = csh.crime_head_id
LEFT JOIN case_status_master csm ON csm.case_status_id = x.case_status_id
WHERE x.person_name IS NOT NULL
LIMIT 25
""".strip()


def _extract_crime_no(question: str) -> str | None:
    m = re.search(
        r"(?i)\b(?:crime\s*(?:no\.?|number|#)?\s*)?(\d{10,})\b",
        question or "",
    )
    return m.group(1) if m else None


def _person_related_sql(name: str) -> str:
    """Cases where the person appears as accused, victim, complainant, or registering officer."""
    safe = name.strip().replace("'", "''")
    return f"""
SELECT DISTINCT x.crime_no, x.case_no, x.brief_facts, u.unit_name, csh.crime_head_name,
  ch.crime_group_name, csm.case_status_name, d.district_name,
  x.person_name, x.role
FROM (
  SELECT cm.case_master_id, cm.crime_no, cm.case_no, cm.brief_facts, cm.police_station_id,
         cm.crime_minor_head_id, cm.case_status_id,
         COALESCE(a.accused_name, v.victim_name, cd.complainant_name) AS person_name,
         CASE
           WHEN a.accused_master_id IS NOT NULL THEN 'accused'
           WHEN v.victim_master_id IS NOT NULL THEN 'victim'
           ELSE 'complainant'
         END AS role
  FROM case_master cm
  LEFT JOIN accused a
    ON a.case_master_id = cm.case_master_id AND a.accused_name ILIKE '%{safe}%'
  LEFT JOIN victim v
    ON v.case_master_id = cm.case_master_id AND v.victim_name ILIKE '%{safe}%'
  LEFT JOIN complainant_details cd
    ON cd.case_master_id = cm.case_master_id AND cd.complainant_name ILIKE '%{safe}%'
  WHERE a.accused_master_id IS NOT NULL
     OR v.victim_master_id IS NOT NULL
     OR cd.complainant_id IS NOT NULL

  UNION ALL

  SELECT cm.case_master_id, cm.crime_no, cm.case_no, cm.brief_facts, cm.police_station_id,
         cm.crime_minor_head_id, cm.case_status_id,
         e.first_name AS person_name,
         'officer' AS role
  FROM case_master cm
  JOIN employee e ON e.employee_id = cm.police_person_id
  WHERE e.first_name ILIKE '%{safe}%'
) x
JOIN unit u ON u.unit_id = x.police_station_id
LEFT JOIN district d ON d.district_id = u.district_id
JOIN crime_sub_head csh ON csh.crime_sub_head_id = x.crime_minor_head_id
LEFT JOIN crime_head ch ON ch.crime_head_id = csh.crime_head_id
LEFT JOIN case_status_master csm ON csm.case_status_id = x.case_status_id
LIMIT 25
""".strip()


def _officer_cases_sql(name: str) -> str:
    """FIRs registered / handled by an officer (employee.first_name)."""
    safe = name.strip().replace("'", "''")
    return f"""
SELECT cm.crime_no, cm.case_no, cm.brief_facts, u.unit_name, csh.crime_head_name,
       e.first_name AS person_name, e.first_name AS officer_name, 'officer' AS role,
       ch.crime_group_name, csm.case_status_name, d.district_name
FROM case_master cm
JOIN employee e ON e.employee_id = cm.police_person_id
JOIN unit u ON u.unit_id = cm.police_station_id
LEFT JOIN district d ON d.district_id = u.district_id
JOIN crime_sub_head csh ON csh.crime_sub_head_id = cm.crime_minor_head_id
LEFT JOIN crime_head ch ON ch.crime_head_id = csh.crime_head_id
LEFT JOIN case_status_master csm ON csm.case_status_id = cm.case_status_id
WHERE e.first_name ILIKE '%{safe}%'
LIMIT 25
""".strip()


def _accused_history_sql(name: str) -> str:
    return _person_related_sql(name)


# Kannada script → English terms used only as bilingual aliases (not crime catalogue).
_KN_CRIME_MAP = {
    "ದರೋಡೆ": "robbery",
    "ಕಳ್ಳತನ": "theft",
    "ಕೊಲೆ": "murder",
    "ಅಪಹರಣ": "kidnapping",
    "ದೌರ್ಜನ್ಯ": "assault",
    "ವಂಚನೆ": "cheating",
    "ಸೈಬರ್": "cybercrime",
    "ಚೈನ್": "chain snatching",
    "ಗೃಹಹಿಂಸೆ": "domestic violence",
}

_KN_PLACE_MAP = {
    "ಹುಬ್ಬಳ್ಳಿ": "Hubballi",
    "ಹುಬ್ಲಿ": "Hubballi",
    "ಬೆಂಗಳೂರು": "Bengaluru",
    "ಮೈಸೂರು": "Mysuru",
    "ಬೆಳಗಾವಿ": "Belagavi",
    "ಧಾರವಾಡ": "Dharwad",
    "ಕಲಬುರಗಿ": "Kalaburagi",
    "ಶಿವಮೊಗ್ಗ": "Shivamogga",
    "ತುಮಕೂರು": "Tumakuru",
}

# Spoken / alternate spellings → token that matches unit_name or district_name via ILIKE.
_PLACE_ALIASES = {
    "bengaluru": "Bengaluru",
    "bangalore": "Bengaluru",
    "blr": "Bengaluru",
    "mysore": "Mysuru",
    "mysuru": "Mysuru",
    "belgaum": "Belagavi",
    "belagavi": "Belagavi",
    "hubli": "Hubballi",
    "hubballi": "Hubballi",
    "mangalore": "Mangaluru",
    "mangaluru": "Mangaluru",
    "shimoga": "Shivamogga",
    "shivamogga": "Shivamogga",
    "tumkur": "Tumakuru",
    "tumakuru": "Tumakuru",
    "gulbarga": "Kalaburagi",
    "kalaburagi": "Kalaburagi",
    "dharwad": "Dharwad",
    "ballari": "Ballari",
    "bellary": "Ballari",
}

_db_crime_terms: list[str] | None = None
_db_place_terms: list[str] | None = None


def _crime_terms_from_db() -> list[str]:
    """Crime head names from Postgres (cached for the process lifetime)."""
    global _db_crime_terms
    if _db_crime_terms is not None:
        return _db_crime_terms
    try:
        rows = run_read_only_query(
            """
            SELECT DISTINCT crime_head_name
            FROM crime_sub_head
            WHERE crime_head_name IS NOT NULL AND length(trim(crime_head_name)) >= 3
            ORDER BY crime_head_name
            """
        )
        terms = [str(r["crime_head_name"]).strip() for r in rows if r.get("crime_head_name")]
        # Prefer longer names first so "domestic violence" beats "violence".
        terms.sort(key=len, reverse=True)
        _db_crime_terms = terms or [
            "robbery", "theft", "murder", "kidnapping", "assault", "cheating", "fraud",
        ]
    except Exception:
        logger.warning("Could not load crime heads from DB; using minimal fallback")
        _db_crime_terms = [
            "robbery", "theft", "murder", "kidnapping", "assault", "cheating", "fraud",
        ]
    return _db_crime_terms


def _place_terms_from_db() -> list[str]:
    """Station / district tokens from Postgres for place matching."""
    global _db_place_terms
    if _db_place_terms is not None:
        return _db_place_terms
    try:
        rows = run_read_only_query(
            """
            SELECT name FROM (
              SELECT DISTINCT regexp_replace(unit_name, '\\s+(Rural|City|Town|Camp|Market|Central|North|West|East|South)?\\s*PS$', '', 'i') AS name
              FROM unit
              WHERE unit_name IS NOT NULL
              UNION
              SELECT DISTINCT unit_name FROM unit WHERE unit_name IS NOT NULL
              UNION
              SELECT DISTINCT district_name FROM district WHERE district_name IS NOT NULL
            ) t
            WHERE length(trim(name)) >= 3
            ORDER BY length(name) DESC
            LIMIT 300
            """
        )
        _db_place_terms = [str(r["name"]).strip() for r in rows if r.get("name")]
    except Exception:
        logger.warning("Could not load places from DB", exc_info=True)
        _db_place_terms = []
    return _db_place_terms


# Investigator wording -> case_status_master.case_status_name
_STATUS_ALIASES: dict[str, tuple[str, ...]] = {
    "Under Investigation": (
        "under investigation", "pending", "open case", "open cases", "ongoing",
        "unsolved", "not solved", "still investigating", "ಬಾಕಿ", "ತನಿಖೆಯಲ್ಲಿ",
    ),
    "Charge Sheeted": (
        "charge sheeted", "chargesheeted", "charge sheet", "chargesheet",
        "charge-sheet", "filed in court", "ಆರೋಪಪತ್ರ",
    ),
    "Closed": ("closed", "disposed", "solved", "completed", "ಮುಕ್ತಾಯ"),
}

# Investigator wording -> crime_sub_head.crime_head_name (DB labels differ slightly)
_CRIME_ALIASES: dict[str, tuple[str, ...]] = {
    "Cheating/Fraud": ("cheating", "fraud", "cheating/fraud", "scam", "forgery"),
    "Chain Snatching": ("chain snatching", "chain snatch", "snatching", "snatch"),
    "Cybercrime": ("cybercrime", "cyber crime", "cyber", "online fraud", "phishing"),
    "Domestic Violence": ("domestic violence", "dowry", "domestic abuse"),
    "Kidnapping": ("kidnapping", "kidnap", "abduction", "missing person"),
}


def _extract_status(question: str) -> str | None:
    q = (question or "").lower()
    for status, aliases in _STATUS_ALIASES.items():
        for alias in aliases:
            if alias in q:
                return status
    return None


def _extract_year(question: str) -> int | None:
    m = re.search(r"(?i)\b(?:in|of|during|for|year)\s+(20\d{2})\b", question or "")
    if not m:
        m = re.search(r"\b(20\d{2})\b", question or "")
    if not m:
        return None
    # Do not read a year out of a 18-digit crime number.
    if re.search(rf"\d{{4,}}{m.group(1)}|{m.group(1)}\d{{4,}}", question or ""):
        return None
    return int(m.group(1))


def _extract_place(question: str) -> str | None:
    """Longest station/district token from the DB that appears in the question."""
    import difflib

    q = question or ""
    for kn, en in _KN_PLACE_MAP.items():
        if kn in q:
            return en
    lowered = q.lower()
    for alias, canonical in sorted(_PLACE_ALIASES.items(), key=lambda kv: len(kv[0]), reverse=True):
        if re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", lowered):
            return canonical
    candidates = sorted(_place_terms_from_db(), key=len, reverse=True)
    for place in candidates:
        if len(place) < 4:
            continue
        if re.search(rf"(?i)(?<![A-Za-z]){re.escape(place)}(?![A-Za-z])", q):
            return place
    # Typos: "Hubali" -> "Hubballi"
    vocabulary = {p.lower(): p for p in candidates if len(p) >= 5}
    vocabulary.update({a: c for a, c in _PLACE_ALIASES.items()})
    for word in re.findall(r"[A-Za-z]{5,}", q):
        if word.lower() in {
            "cases", "crime", "crimes", "show", "list", "pending", "closed",
            "murder", "robbery", "theft", "assault", "station", "district",
            "police", "charge", "sheeted", "registered", "profile",
        }:
            continue
        match = difflib.get_close_matches(word.lower(), list(vocabulary), n=1, cutoff=0.78)
        if match:
            return vocabulary[match[0]]
    return None


def _extract_crime_term(question: str) -> str | None:
    """DB crime head named in the question, tolerant of aliases and typos."""
    import difflib

    q = (question or "").strip()
    if not q:
        return None
    heads = _crime_terms_from_db()
    for head in sorted(heads, key=len, reverse=True):
        if re.search(rf"(?i)(?<![A-Za-z]){re.escape(head)}(?![A-Za-z])", q):
            return head
    for kn, en in _KN_CRIME_MAP.items():
        if kn in q:
            return en
    lowered = q.lower()
    for head, aliases in _CRIME_ALIASES.items():
        if any(a in lowered for a in aliases):
            return head
    # Typos: compare each word against DB labels ("kidnaping" -> "Kidnapping").
    vocabulary = {h.lower(): h for h in heads}
    for alias_head, aliases in _CRIME_ALIASES.items():
        for alias in aliases:
            vocabulary.setdefault(alias, alias_head)
    for word in re.findall(r"[A-Za-z]{5,}", lowered):
        match = difflib.get_close_matches(word, list(vocabulary), n=1, cutoff=0.82)
        if match:
            return vocabulary[match[0]]
    return None


def extract_filters(question: str) -> dict:
    """Structured filters for the generic case query (all optional)."""
    q = (question or "").strip()
    return {
        "crime": _extract_crime_term(q),
        "place": _extract_place(q),
        "status": _extract_status(q),
        "year": _extract_year(q),
    }


def _where_from_filters(filters: dict, person: str | None = None) -> list[str]:
    clauses: list[str] = []
    if filters.get("crime"):
        clauses.append(f"csh.crime_head_name ILIKE '%{str(filters['crime']).replace(chr(39), chr(39) * 2)}%'")
    if filters.get("place"):
        p = str(filters["place"]).replace("'", "''")
        clauses.append(f"(u.unit_name ILIKE '%{p}%' OR d.district_name ILIKE '%{p}%')")
    if filters.get("status"):
        s = str(filters["status"]).replace("'", "''")
        clauses.append(f"csm.case_status_name ILIKE '%{s}%'")
    if filters.get("year"):
        clauses.append(f"EXTRACT(YEAR FROM cm.crime_registered_date) = {int(filters['year'])}")
    if person:
        n = person.replace("'", "''")
        clauses.append(
            "(EXISTS (SELECT 1 FROM accused a WHERE a.case_master_id = cm.case_master_id "
            f"AND a.accused_name ILIKE '%{n}%')"
            " OR EXISTS (SELECT 1 FROM victim v WHERE v.case_master_id = cm.case_master_id "
            f"AND v.victim_name ILIKE '%{n}%')"
            " OR EXISTS (SELECT 1 FROM complainant_details cd WHERE cd.case_master_id = cm.case_master_id "
            f"AND cd.complainant_name ILIKE '%{n}%')"
            " OR EXISTS (SELECT 1 FROM employee e WHERE e.employee_id = cm.police_person_id "
            f"AND e.first_name ILIKE '%{n}%'))"
        )
    return clauses


_CASE_FROM = """
FROM case_master cm
JOIN unit u ON u.unit_id = cm.police_station_id
LEFT JOIN district d ON d.district_id = u.district_id
JOIN crime_sub_head csh ON csh.crime_sub_head_id = cm.crime_minor_head_id
LEFT JOIN crime_head ch ON ch.crime_head_id = csh.crime_head_id
LEFT JOIN case_status_master csm ON csm.case_status_id = cm.case_status_id
""".strip()


def filtered_cases_sql(filters: dict, person: str | None = None, limit: int = 25) -> str:
    clauses = _where_from_filters(filters, person)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    person_col = ""
    if person:
        n = person.replace("'", "''")
        person_col = (
            ", (SELECT a.accused_name FROM accused a WHERE a.case_master_id = cm.case_master_id "
            f"AND a.accused_name ILIKE '%{n}%' LIMIT 1) AS person_name"
        )
    return f"""
SELECT DISTINCT cm.crime_no, cm.case_no, cm.brief_facts, u.unit_name, csh.crime_head_name,
       ch.crime_group_name, csm.case_status_name, d.district_name,
       cm.crime_registered_date{person_col}
{_CASE_FROM}
{where}
ORDER BY cm.crime_registered_date DESC NULLS LAST
LIMIT {int(limit)}
""".strip()


_GROUP_DIMENSIONS: dict[str, tuple[str, str]] = {
    # key -> (sql expression, output column name)
    "station": ("u.unit_name", "unit_name"),
    "district": ("d.district_name", "district_name"),
    "crime_type": ("csh.crime_head_name", "crime_head_name"),
    "crime_group": ("ch.crime_group_name", "crime_group_name"),
    "status": ("csm.case_status_name", "case_status_name"),
    "officer": (
        "(SELECT e.first_name FROM employee e WHERE e.employee_id = cm.police_person_id)",
        "officer_name",
    ),
    "year": ("EXTRACT(YEAR FROM cm.crime_registered_date)::int::text", "year"),
    "month": ("to_char(cm.crime_registered_date, 'YYYY-MM')", "month"),
}


def group_count_sql(dimension: str, filters: dict, person: str | None = None, limit: int = 10) -> str:
    expr, label = _GROUP_DIMENSIONS[dimension]
    clauses = _where_from_filters(filters, person)
    clauses.append(f"{expr} IS NOT NULL")
    where = "WHERE " + " AND ".join(clauses)
    order = "1 ASC" if dimension in ("year", "month") else "case_count DESC"
    return f"""
SELECT {expr} AS {label}, COUNT(DISTINCT cm.case_master_id)::int AS case_count
{_CASE_FROM}
{where}
GROUP BY 1
ORDER BY {order}
LIMIT {int(limit)}
""".strip()


def total_count_sql(filters: dict, person: str | None = None) -> str:
    clauses = _where_from_filters(filters, person)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    return f"""
SELECT COUNT(DISTINCT cm.case_master_id)::int AS case_count
{_CASE_FROM}
{where}
""".strip()


def person_exists(name: str) -> bool:
    """True when the name matches a real accused / victim / complainant / officer.

    Keeps loose name regexes from turning stray words ("Hubballi", "records")
    into person filters that silently return nothing.
    """
    safe = (name or "").strip().replace("'", "''")
    if len(safe) < 3:
        return False
    try:
        rows = run_read_only_query(
            f"""
            SELECT 1 AS hit FROM accused WHERE accused_name ILIKE '%{safe}%'
            UNION ALL SELECT 1 FROM victim WHERE victim_name ILIKE '%{safe}%'
            UNION ALL SELECT 1 FROM complainant_details WHERE complainant_name ILIKE '%{safe}%'
            UNION ALL SELECT 1 FROM employee WHERE first_name ILIKE '%{safe}%'
            LIMIT 1
            """
        )
        return bool(rows)
    except Exception:
        logger.warning("person_exists check failed for %r", name, exc_info=True)
        return True


def _extract_officer_name(question: str) -> str | None:
    q = question.strip().rstrip(".?!")
    m = _OFFICER_HANDLED_RE.search(q)
    if m:
        return _clean_person_name(m.group("name"))
    return None


def _extract_accused_name(question: str) -> str | None:
    q = question.strip().rstrip(".?!")

    for pattern in (_RELATED_PERSON_RE, _ABOUT_PERSON_RE, _ACCUSED_HISTORY_RE, _NAME_FIRST_RE):
        m = pattern.search(q)
        if not m:
            continue
        name = _clean_person_name(m.group("name"))
        if name:
            return name

    m = _SHORT_PERSON_RE.search(q)
    if m:
        name = _clean_person_name(m.group("name1") or m.group("name2") or "")
        if name:
            return name

    m = re.match(
        r"(?i)^\s*(?:hey|hi|hello|please|ok|okay)[,!\s]+(.+)$",
        q,
    )
    if m:
        return _extract_accused_name(m.group(1))

    m = re.match(
        r"(?i)^\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b.*\b(crimes?|cases?|history|did)\b",
        q,
    )
    if m:
        return _clean_person_name(m.group(1))

    # Bare name typed alone (e.g. "Santosh Naik") — search people + officers.
    m = _BARE_NAME_RE.match(q)
    if m:
        name = _clean_person_name(m.group("name"))
        if name and name.lower() not in ("hi", "hello", "hey", "thanks", "thank you"):
            return name
    return None


def _resolved_person_name(question: str, filters: dict) -> str | None:
    """Person name from the question, rejected when it is really a place/crime word."""
    name = _extract_accused_name(question)
    if not name:
        return None
    lowered = name.lower()
    for key in ("place", "crime", "status"):
        value = filters.get(key)
        if value and (lowered in str(value).lower() or str(value).lower() in lowered):
            return None
    if any(re.fullmatch(rf"(?i){re.escape(p)}", name) for p in _place_terms_from_db()):
        return None
    return name if person_exists(name) else None


def _template_sql(question: str) -> str | None:
    """Deterministic SQL for common investigator phrasings (demo-reliable)."""
    q = (question or "").strip()

    # Relations / people linked to a specific FIR.
    if re.search(r"(?i)\b(relation|relations|network|linked|associates|co-accused|connected)\b", q):
        crime_no = _extract_crime_no(q)
        if crime_no:
            return _case_relations_sql(crime_no)

    crime_no = _extract_crime_no(q)
    if crime_no and re.search(r"(?i)\b(explain|detail|about|show|case|fir|crime)\b", q):
        return _case_by_crime_no_sql(crime_no)
    if crime_no and re.fullmatch(rf"(?i)\s*(?:crime\s*(?:no\.?|number|#)?\s*)?{re.escape(crime_no)}\s*", q):
        return _case_by_crime_no_sql(crime_no)

    officer = _extract_officer_name(q)
    if officer:
        return _officer_cases_sql(officer)

    filters = extract_filters(q)
    name = _resolved_person_name(q, filters)

    # Crime type / station / district / status / year (any combination), optionally
    # narrowed to a person: "pending robbery cases in Hubballi in 2025".
    if any(filters.values()):
        return filtered_cases_sql(filters, name)

    if name:
        if re.search(r"(?i)\b(handled|registered|investigated)\s+by\b|\bofficer\b|\bio\b", q):
            return _officer_cases_sql(name)
        return _accused_history_sql(name)
    # brief-facts keyword search
    m = re.search(r"(?i)\b(gold chain|railway|motorcycle|knifepoint)\b", q)
    if m:
        kw = m.group(1).replace("'", "''")
        return f"""
SELECT cm.crime_no, cm.case_no, cm.brief_facts, u.unit_name, csh.crime_head_name
FROM case_master cm
JOIN unit u ON u.unit_id = cm.police_station_id
JOIN crime_sub_head csh ON csh.crime_sub_head_id = cm.crime_minor_head_id
WHERE cm.brief_facts ILIKE '%{kw}%'
LIMIT 25
""".strip()
    return None


def _clean_sql(text: str) -> str:
    sql = (text or "").strip()
    fenced = re.search(r"```(?:sql)?\s*([\s\S]*?)```", sql, re.I)
    if fenced:
        sql = fenced.group(1).strip()
    sql = sql.strip().strip("`").strip()
    if sql.lower().startswith("sql"):
        sql = sql[3:].strip()
    m = re.search(r"(?is)\b(SELECT\b[\s\S]+)", sql)
    if m:
        sql = m.group(1).strip()
    if ";" in sql:
        sql = sql.split(";", 1)[0].strip()
    return sql


def generate_sql(question: str, conversation_context: str = "") -> str:
    llm = get_llm()
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=f"Conversation so far:\n{conversation_context}\n\nQuestion: {question}"),
    ]
    response = llm.invoke(messages)
    sql = _clean_sql(response.content if isinstance(response.content, str) else str(response.content))
    if not sql.lower().startswith("select"):
        raise ValueError(f"Refusing to run non-SELECT statement generated by LLM: {sql!r}")
    return sql


def run_sql_question(question: str, conversation_context: str = "") -> dict:
    """Prefer a template for well-known demo queries; otherwise LLM SQL with template fallback.

    Templates always win when the question itself is fully specified (place+crime,
    accused history, brief-facts keyword) — even if chat history is present. That
    avoids Gemini / Neon races on demo questions like "Show robbery cases in Hubballi."
    Follow-ups that need history ("only pending ones") do not match templates and
    still go through the LLM path.
    """
    from core.db import reset_pool

    template = _template_sql(question)
    if template:
        try:
            rows = run_read_only_query(template)
            return {"sql": template, "rows": rows}
        except Exception as exc:
            logger.warning("Template SQL failed (%s); resetting pool and retrying", exc)
            reset_pool()
            try:
                rows = run_read_only_query(template)
                return {"sql": template, "rows": rows}
            except Exception:
                pass  # fall through to LLM only if template truly cannot run

    try:
        sql = generate_sql(question, conversation_context)
        rows = run_read_only_query(sql)
        return {"sql": sql, "rows": rows}
    except Exception as exc:
        logger.warning("LLM SQL failed (%s); trying template fallback", exc)
        reset_pool()
        if template is None:
            template = _template_sql(question)
        if template:
            rows = run_read_only_query(template)
            return {"sql": template, "rows": rows}
        raise
