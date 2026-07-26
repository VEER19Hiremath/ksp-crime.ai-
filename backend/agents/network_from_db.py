"""Build a force-graph network payload directly from Neon Postgres.

Neo4j is optional for viz — Postgres is the source of truth and carries full
case properties (station, crime type, brief facts, roles).
"""
from __future__ import annotations

from core.db import run_read_only_query


def _nid(kind: str, key) -> str:
    return f"{kind}:{key}"


def fetch_network_from_postgres(
    name: str | None = None,
    crime_no: str | None = None,
    crime_nos: list[str] | None = None,
    limit: int = 60,
) -> dict:
    """Return {nodes, links, source: 'postgres'} with rich props for the UI."""
    nodes: dict[str, dict] = {}
    links: list[dict] = []
    seen: set[tuple] = set()

    def add_node(nid: str, label: str, ntype: str, props: dict):
        if nid not in nodes:
            nodes[nid] = {
                "id": nid,
                "label": label,
                "type": ntype,
                "props": {k: v for k, v in props.items() if v is not None},
            }
        else:
            # Merge extra properties when the same node is seen again.
            nodes[nid]["props"].update({k: v for k, v in props.items() if v is not None})

    def add_link(source: str | None, target: str | None, rel: str):
        if not source or not target or source == target:
            return
        key = (source, target, rel)
        rev = (target, source, rel)
        if key in seen or rev in seen:
            return
        seen.add(key)
        links.append({"source": source, "target": target, "type": rel})

    needle = (name or "").strip()
    # Long numeric tokens are FIR numbers, not person names.
    if needle and needle.isdigit() and len(needle) >= 10:
        crime_no = crime_no or needle
        needle = ""

    nos: list[str] = []
    for n in list(crime_nos or []) + ([crime_no] if crime_no else []):
        t = str(n or "").strip()
        if t and t not in nos:
            nos.append(t)
    nos = nos[:25]

    _CASE_SELECT = f"""
            SELECT
              cm.case_master_id, cm.crime_no, cm.case_no, cm.brief_facts,
              u.unit_id, u.unit_name,
              d.district_name,
              csh.crime_head_name,
              ch.crime_group_name,
              csm.case_status_name,
              a.accused_master_id, a.accused_name, a.age_year AS accused_age,
              v.victim_master_id, v.victim_name,
              e.employee_id, e.first_name AS officer_name
            FROM case_master cm
            JOIN unit u ON u.unit_id = cm.police_station_id
            LEFT JOIN district d ON d.district_id = u.district_id
            JOIN crime_sub_head csh ON csh.crime_sub_head_id = cm.crime_minor_head_id
            LEFT JOIN crime_head ch ON ch.crime_head_id = csh.crime_head_id
            LEFT JOIN case_status_master csm ON csm.case_status_id = cm.case_status_id
            LEFT JOIN accused a ON a.case_master_id = cm.case_master_id
            LEFT JOIN victim v ON v.case_master_id = cm.case_master_id
            LEFT JOIN employee e ON e.employee_id = cm.police_person_id
    """

    if nos:
        in_list = ", ".join("'" + n.replace("'", "''") + "'" for n in nos)
        rows = run_read_only_query(
            f"""
            {_CASE_SELECT}
            WHERE cm.crime_no::text IN ({in_list})
               OR cm.case_no::text IN ({in_list})
               OR cm.crime_no::text ILIKE ANY (ARRAY[{", ".join("'%" + n.replace("'", "''") + "%'" for n in nos)}])
            LIMIT {int(limit) * 4}
            """
        )
    elif needle:
        safe = needle.replace("'", "''")
        rows = run_read_only_query(
            f"""
            WITH matched_cases AS (
              SELECT DISTINCT cm.case_master_id
              FROM case_master cm
              LEFT JOIN accused a ON a.case_master_id = cm.case_master_id
              LEFT JOIN victim v ON v.case_master_id = cm.case_master_id
              LEFT JOIN complainant_details cd ON cd.case_master_id = cm.case_master_id
              LEFT JOIN employee e ON e.employee_id = cm.police_person_id
              WHERE a.accused_name ILIKE '%{safe}%'
                 OR v.victim_name ILIKE '%{safe}%'
                 OR cd.complainant_name ILIKE '%{safe}%'
                 OR e.first_name ILIKE '%{safe}%'
              LIMIT {int(limit)}
            )
            {_CASE_SELECT.replace("FROM case_master cm", "FROM matched_cases mc JOIN case_master cm ON cm.case_master_id = mc.case_master_id", 1)}
            """
        )
    else:
        # Default: densest co-accused / multi-party cases from live data.
        rows = run_read_only_query(
            f"""
            WITH busy AS (
              SELECT case_master_id, COUNT(*) AS n
              FROM accused
              GROUP BY case_master_id
              HAVING COUNT(*) >= 2
              ORDER BY COUNT(*) DESC
              LIMIT {max(8, int(limit) // 4)}
            )
            SELECT
              cm.case_master_id, cm.crime_no, cm.case_no, cm.brief_facts,
              u.unit_id, u.unit_name,
              d.district_name,
              csh.crime_head_name,
              ch.crime_group_name,
              csm.case_status_name,
              a.accused_master_id, a.accused_name, a.age_year AS accused_age,
              v.victim_master_id, v.victim_name,
              e.employee_id, e.first_name AS officer_name
            FROM busy b
            JOIN case_master cm ON cm.case_master_id = b.case_master_id
            JOIN unit u ON u.unit_id = cm.police_station_id
            LEFT JOIN district d ON d.district_id = u.district_id
            JOIN crime_sub_head csh ON csh.crime_sub_head_id = cm.crime_minor_head_id
            LEFT JOIN crime_head ch ON ch.crime_head_id = csh.crime_head_id
            LEFT JOIN case_status_master csm ON csm.case_status_id = cm.case_status_id
            LEFT JOIN accused a ON a.case_master_id = cm.case_master_id
            LEFT JOIN victim v ON v.case_master_id = cm.case_master_id
            LEFT JOIN employee e ON e.employee_id = cm.police_person_id
            LIMIT {int(limit) * 3}
            """
        )

    accused_by_case: dict[int, list[str]] = {}

    for row in rows:
        cid = row.get("case_master_id")
        if cid is None:
            continue
        case_id = _nid("Case", cid)
        add_node(
            case_id,
            str(row.get("crime_no") or f"Case {cid}"),
            "Case",
            {
                "case_master_id": cid,
                "crime_no": row.get("crime_no"),
                "case_no": row.get("case_no"),
                "brief_facts": row.get("brief_facts"),
                "crime_head_name": row.get("crime_head_name"),
                "crime_group_name": row.get("crime_group_name"),
                "case_status_name": row.get("case_status_name"),
                "unit_name": row.get("unit_name"),
                "district_name": row.get("district_name"),
            },
        )

        uid = row.get("unit_id")
        if uid is not None:
            unit_id = _nid("Unit", uid)
            add_node(
                unit_id,
                str(row.get("unit_name") or f"Unit {uid}"),
                "Unit",
                {
                    "unit_id": uid,
                    "unit_name": row.get("unit_name"),
                    "district_name": row.get("district_name"),
                },
            )
            add_link(case_id, unit_id, "REGISTERED_AT")

        aid = row.get("accused_master_id")
        aname = row.get("accused_name")
        if aid is not None and aname:
            accused_id = _nid("Accused", aid)
            add_node(
                accused_id,
                str(aname),
                "Accused",
                {
                    "accused_master_id": aid,
                    "name": aname,
                    "age_year": row.get("accused_age"),
                },
            )
            add_link(accused_id, case_id, "INVOLVED_IN")
            accused_by_case.setdefault(int(cid), []).append(accused_id)

        vid = row.get("victim_master_id")
        vname = row.get("victim_name")
        if vid is not None and vname:
            victim_id = _nid("Victim", vid)
            add_node(
                victim_id,
                str(vname),
                "Victim",
                {"victim_master_id": vid, "name": vname},
            )
            add_link(victim_id, case_id, "VICTIM_OF")

        oid = row.get("employee_id")
        oname = row.get("officer_name")
        if oid is not None and oname:
            officer_id = _nid("Officer", oid)
            add_node(
                officer_id,
                str(oname),
                "Officer",
                {"employee_id": oid, "name": oname},
            )
            add_link(officer_id, case_id, "INVESTIGATED")

    # Co-accused edges within each case.
    for _cid, aids in accused_by_case.items():
        uniq = list(dict.fromkeys(aids))
        for i, a1 in enumerate(uniq):
            for a2 in uniq[i + 1 :]:
                add_link(a1, a2, "CO_ACCUSED_WITH")

    # Cap node count for the force layout.
    node_list = list(nodes.values())
    if len(node_list) > limit * 2:
        # Prefer keeping Cases + Officers + Units connected to the search hit.
        node_list = node_list[: limit * 2]
        keep = {n["id"] for n in node_list}
        links = [l for l in links if l["source"] in keep and l["target"] in keep]

    return {
        "nodes": node_list,
        "links": links,
        "source": "postgres",
    }


def suggest_network_names(limit: int = 12) -> list[str]:
    """Live names from DB for the network search placeholder / hints."""
    rows = run_read_only_query(
        f"""
        SELECT name FROM (
          SELECT e.first_name AS name, COUNT(*) AS n
          FROM employee e
          JOIN case_master cm ON cm.police_person_id = e.employee_id
          GROUP BY e.first_name
          UNION ALL
          SELECT a.accused_name, COUNT(*)
          FROM accused a
          GROUP BY a.accused_name
          HAVING COUNT(*) >= 2
        ) t
        WHERE name IS NOT NULL AND length(trim(name)) >= 3
        ORDER BY n DESC
        LIMIT {int(limit)}
        """
    )
    out: list[str] = []
    seen: set[str] = set()
    for r in rows:
        n = str(r.get("name") or "").strip()
        if n and n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return out
