"""NL -> Cypher over Neo4j (criminal network), plus force-graph payload helpers."""
from __future__ import annotations

import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage

from core.llm import get_llm
from core.neo4j_client import run_cypher

logger = logging.getLogger(__name__)

GRAPH_SCHEMA = """
Node labels (properties):
  Accused(accused_master_id, name, age_year, gender_id)
  Victim(victim_master_id, name, age_year, gender_id, case_master_id)
  Case(case_master_id, crime_no, case_no, brief_facts)
  Officer(employee_id, name)
  Unit(unit_id, name)
  Court(court_id, name)

Relationships:
  (Accused)-[:INVOLVED_IN]->(Case)
  (Victim)-[:VICTIM_OF]->(Case)
  (Officer)-[:INVESTIGATED]->(Case)
  (Case)-[:REGISTERED_AT]->(Unit)
  (Case)-[:FILED_IN]->(Court)
  (Accused)-[:CO_ACCUSED_WITH {case_master_id}]->(Accused)
  (Accused)-[:SAME_PERSON_AS]->(Accused)
"""

SYSTEM_PROMPT = f"""You are a Neo4j Cypher expert for a Karnataka Police crime graph.
Given the investigator's question and the schema below, output ONLY a single read-only
Cypher query (no explanation, no markdown fences). Never write CREATE/MERGE/DELETE/SET.

Prefer CONTAINS / toLower for name filters. LIMIT results to 25 unless counting.

Schema:
{GRAPH_SCHEMA}

Examples:
Q: Who are the co-accused of Yusuf Ali?
MATCH (a:Accused)-[r:CO_ACCUSED_WITH|SAME_PERSON_AS]-(b:Accused)
WHERE toLower(coalesce(a.name,'')) CONTAINS toLower('Yusuf Ali')
RETURN a.name AS person, b.name AS linked, type(r) AS rel
LIMIT 25

Q: Cases linked to accused Kavya
MATCH (a:Accused)-[:INVOLVED_IN]->(c:Case)
WHERE toLower(coalesce(a.name,'')) CONTAINS toLower('Kavya')
OPTIONAL MATCH (o:Officer)-[:INVESTIGATED]->(c)
RETURN a.name AS accused, c.crime_no AS crime_no, c.brief_facts AS brief_facts, o.name AS officer
LIMIT 25
"""


def _strip_fences(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:cypher|CYPHER)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip().rstrip(";")


def generate_cypher(question: str) -> str:
    llm = get_llm()
    resp = llm.invoke(
        [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=question),
        ]
    )
    return _strip_fences(getattr(resp, "content", str(resp)))


def run_graph_question(question: str) -> dict:
    cypher = generate_cypher(question)
    logger.info("graph_agent cypher: %s", cypher)
    rows = run_cypher(cypher)
    # Serialize Neo4j Node/Relationship values to plain dicts for JSON / LLM.
    clean_rows: list[dict] = []
    for row in rows:
        clean: dict = {}
        for k, v in row.items():
            if hasattr(v, "items") and hasattr(v, "labels"):
                clean[k] = dict(v)
            elif hasattr(v, "items") and not isinstance(v, dict):
                try:
                    clean[k] = dict(v)
                except Exception:
                    clean[k] = str(v)
            else:
                clean[k] = v
        clean_rows.append(clean)
    return {"cypher": cypher, "rows": clean_rows}


def _node_id(label: str, props: dict) -> str:
    for key in ("accused_master_id", "case_master_id", "victim_master_id", "employee_id", "unit_id", "court_id"):
        if key in props and props[key] is not None:
            return f"{label}:{props[key]}"
    return f"{label}:{props.get('name') or props.get('crime_no') or id(props)}"


def _node_label(label: str, props: dict) -> str:
    if label == "Case":
        return props.get("crime_no") or f"Case {props.get('case_master_id')}"
    return props.get("name") or str(props.get("unit_id") or props.get("court_id") or label)


def _as_props(node) -> tuple[str, dict]:
    """Normalize a Neo4j Node (or dict) to (primary_label, props)."""
    if node is None:
        return ("Unknown", {})
    if hasattr(node, "labels") and hasattr(node, "items"):
        labels = list(node.labels)
        return (labels[0] if labels else "Unknown", dict(node))
    if isinstance(node, dict):
        if "labels" in node and node["labels"]:
            labels = node["labels"]
            props = {k: v for k, v in node.items() if k != "labels"}
            return (labels[0], props)
        # Infer label from id property when labels were stripped (legacy record.data()).
        if "accused_master_id" in node:
            return ("Accused", node)
        if "case_master_id" in node and "crime_no" in node:
            return ("Case", node)
        if "case_master_id" in node and "name" in node and "victim_master_id" not in node and "accused_master_id" not in node:
            # ambiguous — prefer Victim if victim_master_id
            pass
        if "victim_master_id" in node:
            return ("Victim", node)
        if "employee_id" in node:
            return ("Officer", node)
        if "unit_id" in node:
            return ("Unit", node)
        if "court_id" in node:
            return ("Court", node)
        if "crime_no" in node:
            return ("Case", node)
        return ("Unknown", node)
    return ("Unknown", {})


def fetch_network_graph(
    name: str | None = None,
    crime_no: str | None = None,
    crime_nos: list[str] | None = None,
    limit: int = 60,
) -> dict:
    """Return {nodes, links} for the force-graph UI.

    Prefers a Postgres-built graph (full case properties). Falls back to Neo4j
    if Postgres fails, and merges Neo4j co-accused edges when available.
    """
    from agents.network_from_db import fetch_network_from_postgres

    pg_error = None
    try:
        graph = fetch_network_from_postgres(
            name=name, crime_no=crime_no, crime_nos=crime_nos, limit=limit
        )
        if graph.get("nodes"):
            # Optionally overlay Neo4j SAME_PERSON / CO_ACCUSED edges.
            try:
                _overlay_neo4j_person_edges(graph, name=name, limit=limit)
            except Exception:
                pass
            return graph
    except Exception as exc:
        pg_error = str(exc)

    # Neo4j-only fallback (sparse props).
    try:
        neo = _fetch_network_neo4j(name=name, limit=limit)
        if neo.get("nodes"):
            neo["source"] = "neo4j"
            return neo
        if pg_error:
            neo["error"] = pg_error
        return neo
    except Exception as exc:
        return {
            "nodes": [],
            "links": [],
            "error": pg_error or str(exc),
            "source": "none",
        }


def _overlay_neo4j_person_edges(graph: dict, name: str | None, limit: int) -> None:
    """Add SAME_PERSON_AS / CO_ACCUSED_WITH links from Neo4j when present."""
    nodes_map = {n["id"]: n for n in graph.get("nodes") or []}
    link_seen = {(l["source"], l["target"], l["type"]) for l in graph.get("links") or []}
    if name and name.strip():
        rows = run_cypher(
            """
            MATCH (a:Accused)-[r:SAME_PERSON_AS|CO_ACCUSED_WITH]-(b:Accused)
            WHERE toLower(coalesce(a.name,'')) CONTAINS toLower($name)
               OR toLower(coalesce(b.name,'')) CONTAINS toLower($name)
            RETURN a.accused_master_id AS a_id, b.accused_master_id AS b_id, type(r) AS rel
            LIMIT $limit
            """,
            {"name": name.strip(), "limit": limit},
        )
    else:
        rows = run_cypher(
            """
            MATCH (a:Accused)-[r:SAME_PERSON_AS|CO_ACCUSED_WITH]-(b:Accused)
            RETURN a.accused_master_id AS a_id, b.accused_master_id AS b_id, type(r) AS rel
            LIMIT $limit
            """,
            {"limit": limit},
        )
    for row in rows:
        a_id = f"Accused:{row.get('a_id')}"
        b_id = f"Accused:{row.get('b_id')}"
        rel = row.get("rel") or "RELATED"
        if a_id not in nodes_map or b_id not in nodes_map:
            continue
        key = (a_id, b_id, rel)
        rev = (b_id, a_id, rel)
        if key in link_seen or rev in link_seen:
            continue
        link_seen.add(key)
        graph.setdefault("links", []).append({"source": a_id, "target": b_id, "type": rel})


def _fetch_network_neo4j(name: str | None = None, limit: int = 60) -> dict:
    """Legacy Neo4j path (kept as fallback)."""
    nodes_map: dict[str, dict] = {}
    links_out: list[dict] = []
    link_seen: set[tuple] = set()

    def add_node(node) -> str | None:
        if node is None:
            return None
        label, props = _as_props(node)
        if not props:
            return None
        nid = _node_id(label, props)
        if nid not in nodes_map:
            nodes_map[nid] = {
                "id": nid,
                "label": _node_label(label, props),
                "type": label,
                "props": {k: v for k, v in props.items() if v is not None},
            }
        return nid

    def add_link(source: str | None, target: str | None, rel_type: str):
        if not source or not target or source == target:
            return
        key = (source, target, rel_type)
        rev = (target, source, rel_type)
        if key in link_seen or rev in link_seen:
            return
        link_seen.add(key)
        links_out.append({"source": source, "target": target, "type": rel_type})

    if name and name.strip():
        rows = run_cypher(
            """
            CALL () {
              MATCH (a:Accused)
              WHERE toLower(coalesce(a.name, '')) CONTAINS toLower($name)
              RETURN a AS person, 'Accused' AS kind
              UNION
              MATCH (v:Victim)
              WHERE toLower(coalesce(v.name, '')) CONTAINS toLower($name)
              RETURN v AS person, 'Victim' AS kind
              UNION
              MATCH (o:Officer)
              WHERE toLower(coalesce(o.name, '')) CONTAINS toLower($name)
              RETURN o AS person, 'Officer' AS kind
            }
            WITH person, kind LIMIT 10
            OPTIONAL MATCH (person)-[:INVOLVED_IN|VICTIM_OF|INVESTIGATED]->(c:Case)
            OPTIONAL MATCH (a2:Accused)-[:INVOLVED_IN]->(c)
            OPTIONAL MATCH (v2:Victim)-[:VICTIM_OF]->(c)
            OPTIONAL MATCH (o2:Officer)-[:INVESTIGATED]->(c)
            OPTIONAL MATCH (person)-[r:CO_ACCUSED_WITH|SAME_PERSON_AS]-(other:Accused)
            RETURN person, kind, c, a2, v2, o2, other, type(r) AS rel
            LIMIT $limit
            """,
            {"name": name.strip(), "limit": limit},
        )
        for row in rows:
            p_id = add_node(row.get("person"))
            c_id = add_node(row.get("c"))
            a2_id = add_node(row.get("a2"))
            v2_id = add_node(row.get("v2"))
            o2_id = add_node(row.get("o2"))
            other_id = add_node(row.get("other"))
            kind = row.get("kind") or "Accused"
            if kind == "Accused":
                add_link(p_id, c_id, "INVOLVED_IN")
            elif kind == "Victim":
                add_link(p_id, c_id, "VICTIM_OF")
            elif kind == "Officer":
                add_link(p_id, c_id, "INVESTIGATED")
            add_link(a2_id, c_id, "INVOLVED_IN")
            add_link(v2_id, c_id, "VICTIM_OF")
            add_link(o2_id, c_id, "INVESTIGATED")
            add_link(p_id, other_id, row.get("rel") or "RELATED")
    else:
        rows = run_cypher(
            """
            MATCH (a:Accused)-[r:SAME_PERSON_AS|CO_ACCUSED_WITH]-(b:Accused)
            WITH a, b, type(r) AS rel
            LIMIT $limit
            OPTIONAL MATCH (a)-[:INVOLVED_IN]->(ca:Case)
            OPTIONAL MATCH (b)-[:INVOLVED_IN]->(cb:Case)
            OPTIONAL MATCH (o:Officer)-[:INVESTIGATED]->(ca)
            OPTIONAL MATCH (v:Victim)-[:VICTIM_OF]->(ca)
            RETURN a, b, rel, ca, cb, o, v
            """,
            {"limit": limit},
        )
        for row in rows:
            a_id = add_node(row.get("a"))
            b_id = add_node(row.get("b"))
            ca_id = add_node(row.get("ca"))
            cb_id = add_node(row.get("cb"))
            o_id = add_node(row.get("o"))
            v_id = add_node(row.get("v"))
            add_link(a_id, b_id, row.get("rel") or "RELATED")
            add_link(a_id, ca_id, "INVOLVED_IN")
            add_link(b_id, cb_id, "INVOLVED_IN")
            add_link(o_id, ca_id, "INVESTIGATED")
            add_link(v_id, ca_id, "VICTIM_OF")

        if not nodes_map:
            rows = run_cypher(
                """
                MATCH (a:Accused)-[:INVOLVED_IN]->(c:Case)
                OPTIONAL MATCH (v:Victim)-[:VICTIM_OF]->(c)
                OPTIONAL MATCH (o:Officer)-[:INVESTIGATED]->(c)
                RETURN a, c, v, o
                LIMIT $limit
                """,
                {"limit": limit},
            )
            for row in rows:
                a_id = add_node(row.get("a"))
                c_id = add_node(row.get("c"))
                v_id = add_node(row.get("v"))
                o_id = add_node(row.get("o"))
                add_link(a_id, c_id, "INVOLVED_IN")
                add_link(v_id, c_id, "VICTIM_OF")
                add_link(o_id, c_id, "INVESTIGATED")

    return {"nodes": list(nodes_map.values()), "links": links_out}