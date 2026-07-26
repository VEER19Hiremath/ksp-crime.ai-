import json

from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agents.conversation_agent import ask, astream_ask
from core.auth import get_current_user
from core.history import list_sessions, load_turns

router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(get_current_user)])


class ChatRequest(BaseModel):
    session_id: str
    message: str
    language_code: str = "en-IN"


class ChatResponse(BaseModel):
    answer: str
    tool: str | None = None
    query: str | None = None
    rows: list = []
    suggestions: list = []
    language_code: str | None = None


@router.get("/sessions")
def chat_sessions(user: dict = Depends(get_current_user)) -> dict:
    sessions = list_sessions(username=user.get("username"))
    return {"sessions": sessions}


@router.get("/history")
def chat_history(session_id: str = Query(...), user: dict = Depends(get_current_user)) -> dict:
    turns = load_turns(session_id)
    return {"session_id": session_id, "turns": turns}


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest, user: dict = Depends(get_current_user)) -> ChatResponse:
    result = await ask(
        req.session_id,
        req.message,
        username=user.get("username"),
        officer_name=user.get("full_name"),
        language_code=req.language_code,
    )
    return ChatResponse(**result)


@router.post("/stream")
async def chat_stream(req: ChatRequest, user: dict = Depends(get_current_user)) -> StreamingResponse:
    """Server-Sent Events: a stream of {"token": "..."} chunks as the answer is
    generated, followed by one {"done": true, "tool", "query", "rows"} event
    carrying the audit trail once the graph run finishes."""

    async def event_source():
        async for event in astream_ask(
            req.session_id,
            req.message,
            username=user.get("username"),
            officer_name=user.get("full_name"),
            language_code=req.language_code,
        ):
            yield f"data: {json.dumps(jsonable_encoder(event))}\n\n"

    return StreamingResponse(event_source(), media_type="text/event-stream")
