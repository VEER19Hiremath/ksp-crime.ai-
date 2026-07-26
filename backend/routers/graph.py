from fastapi import APIRouter, Depends, Query

from agents.graph_agent import fetch_network_graph
from agents.network_from_db import suggest_network_names
from core.auth import get_current_user

router = APIRouter(
    prefix="/graph",
    tags=["graph"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/suggestions")
def suggestions(limit: int = Query(default=12, ge=1, le=40)) -> dict:
    """Live person/officer names from Postgres for the network UI."""
    try:
        return {"names": suggest_network_names(limit=limit)}
    except Exception as exc:
        return {"names": [], "error": str(exc)}


@router.get("/network")
def network(
    name: str | None = Query(default=None, description="Person/officer name filter (partial)"),
    crime_no: str | None = Query(default=None, description="Single FIR / crime number"),
    crime_nos: str | None = Query(
        default=None, description="Comma-separated FIR / crime numbers"
    ),
    limit: int = Query(default=60, ge=1, le=200),
) -> dict:
    """Force-graph payload: {nodes: [{id,label,type,props}], links: [{source,target,type}]}."""
    nos = [p.strip() for p in (crime_nos or "").split(",") if p.strip()]
    try:
        return fetch_network_graph(
            name=name, crime_no=crime_no, crime_nos=nos or None, limit=limit
        )
    except Exception as exc:
        return {"nodes": [], "links": [], "error": str(exc)}
