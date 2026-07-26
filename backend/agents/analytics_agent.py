"""Analytics Agent — trends, hotspots, socio-demographics, early warnings, patterns.
Pulls raw rows via the (read-only) SQL DB connection, then reshapes in pure Python."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date, datetime

from core.db import run_read_only_query

_GENDER = {1: "Male", 2: "Female", 3: "Transgender"}


def _as_month(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m")
    if isinstance(value, date):
        return value.strftime("%Y-%m")
    text = str(value).strip()
    return text[:7] if len(text) >= 7 else text


def crime_trend(district_id: int | None = None) -> list[dict]:
    """Monthly count of cases per crime head, optionally filtered to a district."""
    rows = run_read_only_query(
        """
        SELECT cm.crime_registered_date, ch.crime_group_name, u.district_id
        FROM case_master cm
        JOIN crime_sub_head csh ON csh.crime_sub_head_id = cm.crime_minor_head_id
        JOIN crime_head ch ON ch.crime_head_id = csh.crime_head_id
        JOIN unit u ON u.unit_id = cm.police_station_id
        """
    )
    if not rows:
        return []
    counts: Counter[tuple[str, str]] = Counter()
    for row in rows:
        if district_id is not None and row.get("district_id") != district_id:
            continue
        month = _as_month(row.get("crime_registered_date"))
        group = str(row.get("crime_group_name") or "")
        if month:
            counts[(month, group)] += 1
    return [
        {"month": month, "crime_group_name": group, "count": count}
        for (month, group), count in sorted(counts.items())
    ]


def hotspots(min_cases: int = 2) -> list[dict]:
    """Crime hotspots by police station / district (with lat/lng for the map)."""
    rows = run_read_only_query(
        """
        SELECT u.unit_name,
               d.district_name,
               cm.latitude,
               cm.longitude
        FROM case_master cm
        JOIN unit u ON u.unit_id = cm.police_station_id
        JOIN district d ON d.district_id = u.district_id
        WHERE cm.latitude IS NOT NULL AND cm.longitude IS NOT NULL
        """
    )
    if not rows:
        return []
    buckets: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (str(row.get("unit_name") or ""), str(row.get("district_name") or ""))
        bucket = buckets.setdefault(
            key, {"case_count": 0, "lat_sum": 0.0, "lng_sum": 0.0}
        )
        bucket["case_count"] += 1
        bucket["lat_sum"] += float(row.get("latitude") or 0)
        bucket["lng_sum"] += float(row.get("longitude") or 0)
    out: list[dict] = []
    for (unit_name, district_name), bucket in buckets.items():
        count = int(bucket["case_count"])
        if count < min_cases:
            continue
        out.append(
            {
                "unit_name": unit_name,
                "district_name": district_name,
                "case_count": count,
                "lat_bucket": round(bucket["lat_sum"] / count, 4),
                "lng_bucket": round(bucket["lng_sum"] / count, 4),
            }
        )
    out.sort(key=lambda r: r["case_count"], reverse=True)
    return out


def dashboard_summary() -> dict:
    rows = run_read_only_query(
        """
        SELECT csm.case_status_name, COUNT(*) AS count
        FROM case_master cm
        JOIN case_status_master csm ON csm.case_status_id = cm.case_status_id
        GROUP BY csm.case_status_name
        """
    )
    return {r["case_status_name"]: r["count"] for r in rows}


def socio_demographics() -> dict:
    """Age / gender / occupation insights from accused, victims, complainants."""
    accused = run_read_only_query(
        """
        SELECT age_year, gender_id FROM accused
        WHERE age_year IS NOT NULL
        """
    )
    victims = run_read_only_query(
        """
        SELECT age_year, gender_id FROM victim
        WHERE age_year IS NOT NULL
        """
    )
    occupations = run_read_only_query(
        """
        SELECT om.occupation_name, COUNT(*) AS count
        FROM complainant_details cd
        JOIN occupation_master om ON om.occupation_id = cd.occupation_id
        GROUP BY om.occupation_name
        ORDER BY count DESC
        LIMIT 15
        """
    )

    def _age_bands(rows: list[dict]) -> list[dict]:
        if not rows:
            return []
        bands = [("<18", 0, 18), ("18-24", 18, 25), ("25-34", 25, 35), ("35-44", 35, 45), ("45-59", 45, 60), ("60+", 60, 200)]
        counts = Counter()
        for row in rows:
            try:
                age = int(row.get("age_year"))
            except (TypeError, ValueError):
                continue
            for label, lo, hi in bands:
                if lo <= age < hi:
                    counts[label] += 1
                    break
        return [{"age_band": label, "count": counts[label]} for label, _, _ in bands if counts[label]]

    def _gender(rows: list[dict]) -> list[dict]:
        if not rows:
            return []
        counts = Counter()
        for row in rows:
            try:
                gid = int(row.get("gender_id"))
            except (TypeError, ValueError):
                continue
            counts[_GENDER.get(gid, f"code:{gid}")] += 1
        return [{"gender": gender, "count": count} for gender, count in counts.items()]

    return {
        "accused_age_bands": _age_bands(accused),
        "accused_gender": _gender(accused),
        "victim_age_bands": _age_bands(victims),
        "victim_gender": _gender(victims),
        "complainant_occupations": occupations,
    }


def early_warnings(limit: int = 10) -> list[dict]:
    """Stations/crime heads with recent elevation — proactive prevention intel.

    Prefers month-over-month spikes; if the demo corpus is thin across months,
    falls back to the busiest station×crime cells in the latest month.
    """
    rows = run_read_only_query(
        """
        SELECT date_trunc('month', cm.crime_registered_date)::date AS month,
               u.unit_name,
               d.district_name,
               csh.crime_head_name,
               COUNT(*)::int AS case_count
        FROM case_master cm
        JOIN unit u ON u.unit_id = cm.police_station_id
        JOIN district d ON d.district_id = u.district_id
        JOIN crime_sub_head csh ON csh.crime_sub_head_id = cm.crime_minor_head_id
        WHERE cm.crime_registered_date IS NOT NULL
        GROUP BY 1, 2, 3, 4
        """
    )
    if not rows:
        return []

    by_month: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        month = _as_month(row.get("month"))
        if month:
            by_month[month].append(row)
    months = sorted(by_month)
    latest = months[-1]
    prev = months[-2] if len(months) >= 2 else None
    cur = by_month[latest]
    warnings: list[dict] = []

    if prev is not None:
        prev_index = {
            (r["unit_name"], r["crime_head_name"]): int(r["case_count"])
            for r in by_month[prev]
        }
        for row in cur:
            key = (row["unit_name"], row["crime_head_name"])
            now = int(row["case_count"])
            prev_count = prev_index.get(key, 0)
            delta = now - prev_count
            if now < 1:
                continue
            if delta >= 1 or (prev_count == 0 and now >= 1):
                warnings.append(
                    {
                        "unit_name": key[0],
                        "district_name": row["district_name"],
                        "crime_head_name": key[1],
                        "current_month": latest,
                        "current_count": now,
                        "previous_count": prev_count,
                        "delta": delta,
                        "severity": "rising" if delta > 0 else "elevated",
                        "recommendation": (
                            f"Increase patrol / surveillance at {key[0]} for {key[1]} "
                            f"({prev_count}->{now} vs prior month)."
                        ),
                    }
                )

    if not warnings:
        # Fallback watchlist: densest cells in the latest month.
        top = sorted(cur, key=lambda r: int(r["case_count"]), reverse=True)[:limit]
        for row in top:
            now = int(row["case_count"])
            warnings.append(
                {
                    "unit_name": row["unit_name"],
                    "district_name": row["district_name"],
                    "crime_head_name": row["crime_head_name"],
                    "current_month": latest,
                    "current_count": now,
                    "previous_count": 0,
                    "delta": now,
                    "severity": "watchlist",
                    "recommendation": (
                        f"Priority watch: {row['unit_name']} shows {now} "
                        f"{row['crime_head_name']} case(s) in the latest month — "
                        f"schedule preventive patrols."
                    ),
                }
            )

    warnings.sort(key=lambda w: (w["delta"], w["current_count"]), reverse=True)
    return warnings[:limit]


def crime_patterns(limit: int = 15) -> list[dict]:
    """Recurring station + crime-type clusters (pattern discovery)."""
    rows = run_read_only_query(
        f"""
        SELECT u.unit_name,
               d.district_name,
               csh.crime_head_name,
               ch.crime_group_name,
               COUNT(*)::int AS case_count,
               MIN(cm.crime_registered_date)::date AS first_seen,
               MAX(cm.crime_registered_date)::date AS last_seen
        FROM case_master cm
        JOIN unit u ON u.unit_id = cm.police_station_id
        JOIN district d ON d.district_id = u.district_id
        JOIN crime_sub_head csh ON csh.crime_sub_head_id = cm.crime_minor_head_id
        JOIN crime_head ch ON ch.crime_head_id = csh.crime_head_id
        GROUP BY u.unit_name, d.district_name, csh.crime_head_name, ch.crime_group_name
        HAVING COUNT(*) >= 2
        ORDER BY case_count DESC
        LIMIT {int(limit)}
        """
    )
    for r in rows:
        r["pattern"] = f"Repeat {r['crime_head_name']} at {r['unit_name']}"
        r["first_seen"] = str(r.get("first_seen") or "")
        r["last_seen"] = str(r.get("last_seen") or "")
    return rows


def behavioral_profile(name: str) -> list[dict]:
    """Behavioral profile card rows for a person (accused / victim / officer)."""
    safe = name.strip().replace("'", "''")
    return run_read_only_query(
        f"""
        WITH hits AS (
          SELECT cm.case_master_id, cm.crime_no, cm.case_no, cm.brief_facts,
                 cm.crime_registered_date::date AS registered_on,
                 u.unit_name, d.district_name,
                 csh.crime_head_name, ch.crime_group_name, csm.case_status_name,
                 COALESCE(a.accused_name, v.victim_name, e.first_name) AS person_name,
                 CASE
                   WHEN a.accused_master_id IS NOT NULL THEN 'accused'
                   WHEN v.victim_master_id IS NOT NULL THEN 'victim'
                   ELSE 'officer'
                 END AS role,
                 a.age_year AS age_year
          FROM case_master cm
          JOIN unit u ON u.unit_id = cm.police_station_id
          LEFT JOIN district d ON d.district_id = u.district_id
          JOIN crime_sub_head csh ON csh.crime_sub_head_id = cm.crime_minor_head_id
          LEFT JOIN crime_head ch ON ch.crime_head_id = csh.crime_head_id
          LEFT JOIN case_status_master csm ON csm.case_status_id = cm.case_status_id
          LEFT JOIN accused a
            ON a.case_master_id = cm.case_master_id AND a.accused_name ILIKE '%{safe}%'
          LEFT JOIN victim v
            ON v.case_master_id = cm.case_master_id AND v.victim_name ILIKE '%{safe}%'
          LEFT JOIN employee e
            ON e.employee_id = cm.police_person_id AND e.first_name ILIKE '%{safe}%'
          WHERE a.accused_master_id IS NOT NULL
             OR v.victim_master_id IS NOT NULL
             OR e.employee_id IS NOT NULL
        )
        SELECT * FROM hits
        ORDER BY registered_on DESC NULLS LAST
        LIMIT 25
        """
    )


_COUNT_RE = re.compile(
    r"(?i)\b(how\s+many|how\s+much|count|counts|total|totals|number\s+of|no\.?\s+of)\b"
    r"|ಎಷ್ಟು|ಒಟ್ಟು"
)

_GROUP_RE = re.compile(
    r"(?i)\b(which|what)\s+(?:police\s+)?(?:station|district|crime|officer|month|year)\b"
    r"|\btop\s+\d*\b|\b(most|highest|maximum|worst|busiest|least|lowest)\b"
    r"|\bbreak\s*down\b|\bbreakdown\b|\bdistribution\b|\brank(?:ing|ed)?\b"
    r"|\b(?:group|split|count)ed?\s+by\b|\bper\s+(?:station|district|month|year|crime)\b"
    r"|\bby\s+(?:station|district|crime\s+type|status|month|year|officer)\b"
    r"|ಯಾವ\s*ಠಾಣೆ|ಯಾವ\s*ಜಿಲ್ಲೆ|ಹೆಚ್ಚು"
)

_DIMENSION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("station", r"(?i)\b(police\s+station|station|stations|ps\b|thana)\b|ಠಾಣೆ"),
    ("district", r"(?i)\bdistricts?\b|ಜಿಲ್ಲೆ"),
    ("crime_type", r"(?i)\bcrime\s+(?:type|head|category)s?\b|\btypes?\s+of\s+crime\b|\bwhich\s+crime\b|ಅಪರಾಧ\s*ಪ್ರಕಾರ"),
    ("status", r"(?i)\b(case\s+)?status(?:es)?\b|ಸ್ಥಿತಿ"),
    ("officer", r"(?i)\b(officer|officers|io|investigating\s+officer)\b|ಅಧಿಕಾರಿ"),
    ("month", r"(?i)\bmonths?\b|\bmonthly\b|ತಿಂಗಳ"),
    ("year", r"(?i)\byears?\b|\byearly\b|\bannual\b|ವರ್ಷ"),
)

_DIMENSION_LABELS_EN = {
    "station": "police station",
    "district": "district",
    "crime_type": "crime type",
    "crime_group": "crime group",
    "status": "case status",
    "officer": "investigating officer",
    "month": "month",
    "year": "year",
}

_DIMENSION_LABELS_KN = {
    "station": "ಠಾಣೆ",
    "district": "ಜಿಲ್ಲೆ",
    "crime_type": "ಅಪರಾಧ ಪ್ರಕಾರ",
    "crime_group": "ಅಪರಾಧ ಗುಂಪು",
    "status": "ಪ್ರಕರಣ ಸ್ಥಿತಿ",
    "officer": "ತನಿಖಾಧಿಕಾರಿ",
    "month": "ತಿಂಗಳು",
    "year": "ವರ್ಷ",
}

# "by <dimension>" reads as a single suffixed word in Kannada, not label + ಪ್ರಕಾರ.
_DIMENSION_BY_KN = {
    "station": "ಠಾಣೆವಾರು",
    "district": "ಜಿಲ್ಲೆವಾರು",
    "crime_type": "ಅಪರಾಧ ಪ್ರಕಾರವಾರು",
    "crime_group": "ಅಪರಾಧ ಗುಂಪುವಾರು",
    "status": "ಸ್ಥಿತಿವಾರು",
    "officer": "ಅಧಿಕಾರಿವಾರು",
    "month": "ತಿಂಗಳುವಾರು",
    "year": "ವರ್ಷವಾರು",
}


def _filter_phrase(filters: dict, kn: bool) -> str:
    if kn:
        from core.language import kn_locative, localize_label

        place = localize_label(filters.get("place"), "kn-IN")
        bits = [
            kn_locative(place),
            localize_label(filters.get("crime"), "kn-IN"),
            localize_label(filters.get("status"), "kn-IN"),
            str(filters.get("year") or ""),
        ]
        return " ".join(b for b in bits if b).strip()
    bits = [
        str(filters.get("crime") or ""),
        f"in {filters['place']}" if filters.get("place") else "",
        str(filters.get("status") or ""),
        str(filters.get("year") or ""),
    ]
    return " ".join(b for b in bits if b).strip()


def _group_dimension(question: str, filters: dict) -> str | None:
    q = question or ""
    for dim, pattern in _DIMENSION_PATTERNS:
        if re.search(pattern, q):
            # "murder cases by district" filters on crime yet groups by district.
            if dim == "crime_type" and filters.get("crime") and not re.search(
                r"(?i)\bby\s+crime|which\s+crime|crime\s+(?:type|head)", q
            ):
                continue
            return dim
    if _GROUP_RE.search(q):
        return "station"
    return None


def run_aggregate_question(question: str, language_code: str = "en-IN") -> dict | None:
    """Answer counting / ranking questions ("how many", "which station has most").

    Returns None when the question is not a count or ranking request.
    """
    from core.db import run_read_only_query as _query
    from core.language import localize_label, resolve_reply_language
    from agents.sql_agent import (
        extract_filters,
        group_count_sql,
        total_count_sql,
        _extract_crime_no,
    )

    q = (question or "").strip()
    if not q or _extract_crime_no(q):
        return None
    wants_count = bool(_COUNT_RE.search(q))
    wants_group = bool(_GROUP_RE.search(q))
    if not (wants_count or wants_group):
        return None

    kn = resolve_reply_language(q, language_code) == "kn-IN"
    filters = extract_filters(q)
    dimension = _group_dimension(q, filters) if wants_group else None
    scope = _filter_phrase(filters, kn)

    if dimension:
        limit = 10
        m = re.search(r"(?i)\btop\s+(\d{1,2})\b", q)
        if m:
            limit = max(1, min(25, int(m.group(1))))
        sql = group_count_sql(dimension, filters, limit=limit)
        rows = _query(sql)
        _, label_col = {
            "station": ("", "unit_name"),
            "district": ("", "district_name"),
            "crime_type": ("", "crime_head_name"),
            "crime_group": ("", "crime_group_name"),
            "status": ("", "case_status_name"),
            "officer": ("", "officer_name"),
            "year": ("", "year"),
            "month": ("", "month"),
        }[dimension]
        dim_label = (_DIMENSION_LABELS_KN if kn else _DIMENSION_LABELS_EN)[dimension]
        if not rows:
            return {
                "kind": "aggregate",
                "query": sql,
                "rows": [],
                "answer": None,
                "dimension": dimension,
            }
        head = (
            f"{scope + ' ' if scope else ''}ಪ್ರಕರಣಗಳು - {_DIMENSION_BY_KN[dimension]}"
            if kn
            else f"Cases by {dim_label}" + (f" ({scope})" if scope else "")
        )
        lines = [head, ""]
        for i, row in enumerate(rows, 1):
            label = localize_label(row.get(label_col), "kn-IN") if kn else row.get(label_col)
            lines.append(f"{i}. {label} - {row.get('case_count')}")
        total = sum(int(r.get("case_count") or 0) for r in rows)
        lines.append("")
        lines.append(f"ಒಟ್ಟು {total} ಪ್ರಕರಣಗಳು" if kn else f"Total across the list: {total}")
        return {
            "kind": "aggregate",
            "query": sql,
            "rows": rows,
            "answer": "\n".join(lines),
            "dimension": dimension,
        }

    sql = total_count_sql(filters)
    rows = _query(sql)
    total = int(rows[0]["case_count"]) if rows else 0
    if total == 0:
        return {"kind": "aggregate", "query": sql, "rows": [], "answer": None, "dimension": None}

    # A bare total is thin on its own — add the breakdown the investigator would ask for next.
    breakdown_dim = "crime_type" if (filters.get("place") or filters.get("status") or filters.get("year") or not filters.get("crime")) else "station"
    breakdown_sql = group_count_sql(breakdown_dim, filters, limit=6)
    try:
        breakdown = _query(breakdown_sql)
    except Exception:
        breakdown = []
    label_col = "crime_head_name" if breakdown_dim == "crime_type" else "unit_name"
    dim_label = (_DIMENSION_LABELS_KN if kn else _DIMENSION_LABELS_EN)[breakdown_dim]

    if kn:
        lines = [f"{scope + ' ' if scope else ''}ಒಟ್ಟು ಪ್ರಕರಣಗಳು: {total}"]
    else:
        lines = [f"Total cases{f' ({scope})' if scope else ''}: {total}"]
    if breakdown:
        lines.append("")
        lines.append(_DIMENSION_BY_KN[breakdown_dim] if kn else f"By {dim_label}")
        for row in breakdown:
            label = localize_label(row.get(label_col), "kn-IN") if kn else row.get(label_col)
            lines.append(f"- {label} - {row.get('case_count')}")
    return {
        "kind": "aggregate",
        "query": sql,
        "rows": breakdown or rows,
        "answer": "\n".join(lines),
        "dimension": None,
    }


def run_analytics_question(question: str) -> dict:
    """Route investigator wording to the matching analytics query."""
    q = question.lower()

    aggregate = run_aggregate_question(question)
    if aggregate is not None:
        return aggregate

    # Behavioral profile — extract a trailing name if present.
    if any(w in q for w in ("behavioral", "behavioural", "profile of", "offender profile", "profiling")):
        from agents.sql_agent import _clean_person_name

        m = re.search(
            r"(?i)(?:behavioral|behavioural|offender|profiling|profile)\s+(?:profile\s+)?"
            r"(?:of\s+|for\s+|on\s+)?(?P<name>[A-Za-z][A-Za-z.'\-\s]{1,40})",
            question,
        )
        name = _clean_person_name(m.group("name")) if m else None
        if not name:
            # Fallback: last capitalized tokens
            parts = [p for p in question.split() if p[:1].isupper() and p.lower() not in ("profile", "behavioral")]
            name = " ".join(parts[-2:]) if parts else ""
        if name:
            rows = behavioral_profile(name)
            return {
                "kind": "behavioral_profile",
                "query": f"behavioral_profile({name!r}) — case timeline / crime mix / stations",
                "rows": rows,
            }

    if any(
        w in q
        for w in (
            "socio", "demographic", "age band", "gender", "occupation",
            "demographics", "age group", "victim age", "accused age",
        )
    ):
        data = socio_demographics()
        rows: list[dict] = []
        for band in data["accused_age_bands"]:
            rows.append({"segment": "accused_age", **band})
        for g in data["accused_gender"]:
            rows.append({"segment": "accused_gender", "label": g["gender"], "count": g["count"]})
        for band in data["victim_age_bands"]:
            rows.append({"segment": "victim_age", **band})
        for g in data["victim_gender"]:
            rows.append({"segment": "victim_gender", "label": g["gender"], "count": g["count"]})
        for o in data["complainant_occupations"]:
            rows.append(
                {
                    "segment": "complainant_occupation",
                    "label": o["occupation_name"],
                    "count": o["count"],
                }
            )
        return {
            "kind": "socio_demographics",
            "query": "socio_demographics() — age/gender/occupation from live DB",
            "rows": rows,
        }

    if any(
        w in q
        for w in (
            "early warning", "warning", "rising", "spike", "proactive",
            "prevention", "watchlist", "alert", "forecast", "predict",
            "ಎಚ್ಚರಿಕೆ",
        )
    ):
        rows = early_warnings()
        return {
            "kind": "early_warnings",
            "query": "early_warnings() — MoM station/crime spikes with patrol recommendations",
            "rows": rows,
        }

    if any(w in q for w in ("pattern", "recurring", "modus", "repeat crime", "crime pattern", "ಮಾದರಿ")):
        rows = crime_patterns()
        return {
            "kind": "crime_patterns",
            "query": "crime_patterns() — recurring station + crime-type clusters",
            "rows": rows,
        }

    if any(w in q for w in ("hotspot", "map", "geo", "location", "cluster", "latitude", "where", "ಹಾಟ್")):
        rows = hotspots(min_cases=1)
        return {
            "kind": "hotspots",
            "query": "hotspots() — case counts by police station and district",
            "rows": rows,
        }
    if any(w in q for w in ("trend", "monthly", "over time", "timeline", "by month", "ಟ್ರೆಂಡ್", "ಪ್ರವೃತ್ತಿ")):
        rows = crime_trend()
        return {
            "kind": "crime_trend",
            "query": "crime_trend() — monthly counts by crime_group_name",
            "rows": rows,
        }

    summary = dashboard_summary()
    rows = [{"case_status_name": k, "count": v} for k, v in summary.items()]
    return {
        "kind": "dashboard_summary",
        "query": "dashboard_summary() — case counts by status",
        "rows": rows,
    }
