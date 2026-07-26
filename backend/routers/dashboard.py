from fastapi import APIRouter, Depends, Query

from agents.analytics_agent import (
    behavioral_profile,
    crime_patterns,
    crime_trend,
    dashboard_summary,
    early_warnings,
    hotspots,
    socio_demographics,
)
from core.auth import get_current_user

router = APIRouter(
    prefix="/dashboard",
    tags=["dashboard"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/summary")
def summary() -> dict:
    """Case counts by status for the overview cards."""
    return dashboard_summary()


@router.get("/trend")
def trend(district_id: int | None = Query(default=None)) -> list[dict]:
    """Monthly case counts by crime group."""
    return crime_trend(district_id=district_id)


@router.get("/hotspots")
def crime_hotspots(min_cases: int = Query(default=2, ge=1, le=100)) -> list[dict]:
    """Geo-tagged station hotspots for the map."""
    return hotspots(min_cases=min_cases)


@router.get("/socio")
def socio() -> dict:
    """Age / gender / occupation insights from live Postgres."""
    return socio_demographics()


@router.get("/early-warnings")
def warnings(limit: int = Query(default=10, ge=1, le=50)) -> dict:
    """Rising station/crime spikes for proactive prevention."""
    return {"warnings": early_warnings(limit=limit)}


@router.get("/patterns")
def patterns(limit: int = Query(default=15, ge=1, le=50)) -> dict:
    """Recurring crime-type × station clusters."""
    return {"patterns": crime_patterns(limit=limit)}


@router.get("/profile")
def profile(name: str = Query(..., min_length=2)) -> dict:
    """Behavioral profile for a named person/officer."""
    rows = behavioral_profile(name)
    return {"name": name, "cases": rows, "case_count": len(rows)}
