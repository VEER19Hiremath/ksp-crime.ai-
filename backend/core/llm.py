"""Gemini 2.5 Flash client, shared by every LangGraph agent node."""
from functools import lru_cache

from langchain_google_genai import ChatGoogleGenerativeAI

from core.config import get_settings


@lru_cache
def get_llm(temperature: float = 0.0) -> ChatGoogleGenerativeAI:
    settings = get_settings()
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not set — copy backend/.env.example to backend/.env")
    return ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        google_api_key=settings.gemini_api_key,
        temperature=temperature,
        # Every call in this app (route classification, NL->SQL/Cypher, answer
        # synthesis) is a straightforward pattern-matching task, not something
        # that benefits from extended chain-of-thought. Measured 2-4x latency
        # reduction per call with thinking disabled (e.g. routing: 1.58s -> 0.68s)
        # and no observed drop in output quality — a chat turn makes 3 sequential
        # calls, so this compounds across the whole request.
        thinking_budget=0,
    )
