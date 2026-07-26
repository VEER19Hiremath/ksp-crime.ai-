from neo4j import GraphDatabase, Driver
from neo4j.graph import Node, Relationship, Path

from core.config import get_settings

_driver: Driver | None = None


def get_driver() -> Driver:
    global _driver
    settings = get_settings()
    if not settings.neo4j_uri:
        raise RuntimeError("NEO4J_URI is not set — copy backend/.env.example to backend/.env")
    if _driver is None:
        _driver = GraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
        )
    return _driver


def _serialize_value(value):
    """Keep Nodes as Nodes (labels preserved). record.data() strips labels to plain dicts,
    which broke network viz typing and made the graph look empty/wrong."""
    if isinstance(value, Node):
        return value
    if isinstance(value, Relationship):
        return value
    if isinstance(value, Path):
        return value
    if isinstance(value, list):
        return [_serialize_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialize_value(v) for k, v in value.items()}
    return value


def run_cypher(query: str, params: dict | None = None) -> list[dict]:
    with get_driver().session() as session:
        result = session.run(query, params or {})
        rows: list[dict] = []
        for record in result:
            rows.append({key: _serialize_value(record[key]) for key in record.keys()})
        return rows
