from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel

from agents.report_agent import build_investigation_pdf
from core.auth import require_role

router = APIRouter(prefix="/reports", tags=["reports"])


class Turn(BaseModel):
    question: str
    answer: str
    tool: str | None = None
    query: str | None = None
    rows: list[dict] = []


class ReportRequest(BaseModel):
    session_id: str
    turns: list[Turn]


@router.post("/pdf")
def export_pdf(
    req: ReportRequest,
    _user: dict = Depends(require_role("SHO", "DSP", "Analyst", "Administrator")),
):
    pdf_bytes = build_investigation_pdf(req.session_id, [t.model_dump() for t in req.turns])
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=investigation_{req.session_id}.pdf"},
    )
