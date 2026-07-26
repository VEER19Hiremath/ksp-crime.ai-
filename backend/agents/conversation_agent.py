"""Conversation Agent — the LangGraph orchestrator. Routes each investigator
turn to the SQL, Graph, or Analytics agent, keeps per-session memory via
LangGraph's checkpointer, and always returns an explainable answer (the
underlying SQL/Cypher travels alongside the natural-language answer)."""
from typing import Literal, TypedDict
import asyncio
import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from agents.analytics_agent import run_analytics_question
from agents.graph_agent import run_graph_question
from agents.sql_agent import run_sql_question
from core.language import (
    CRIME_AI_IDENTITY,
    canned_greeting,
    empty_records_message,
    found_results_header,
    has_kannada,
    language_instruction,
    kn_locative,
    localize_facts,
    localize_label,
    no_results_message,
    normalize_language,
    resolve_reply_language,
    smalltalk_reply,
    strip_vendor_identity,
)
from core.llm import get_llm

Route = Literal["sql", "graph", "analytics", "chitchat"]


class ConversationState(TypedDict):
    question: str
    language_code: str  # en-IN | kn-IN — reply language for this turn
    route: Route
    tool: str
    query: str
    rows: list
    answer: str
    history: list  # LangChain messages, kept across turns by the checkpointer


ROUTER_PROMPT = """Classify the investigator's question into exactly one category:
- "sql": needs case/FIR/accused/victim/officer/court data lookup or filtering
- "graph": needs criminal network / relationship / "who else is linked to" analysis
- "analytics": needs trends, counts over time, hotspots, dashboard-style aggregates
- "chitchat": greeting or anything not about the crime database

Reply with exactly one word: sql, graph, analytics, or chitchat."""


def _history_context(history: list) -> str:
    """Flatten recent Q&A turns into text for SQL / follow-up grounding."""
    parts: list[str] = []
    for m in (history or [])[-10:]:
        role = "Investigator" if isinstance(m, HumanMessage) else "Assistant"
        content = m.content if hasattr(m, "content") else str(m)
        parts.append(f"{role}: {content}")
    return "\n".join(parts)


def _unique_keep_order(values: list[str], limit: int = 5) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        key = v.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(v.strip())
        if len(out) >= limit:
            break
    return out


def _anchors_from_rows(rows: list | None) -> str:
    """Compact FIR / person / place tags so follow-ups still work after voice TTS truncation."""
    crime_nos: list[str] = []
    people: list[str] = []
    places: list[str] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        for key in ("crime_no", "case_no"):
            val = row.get(key)
            if val and re.fullmatch(r"\d{10,}", str(val).strip()):
                crime_nos.append(str(val).strip())
        for key in ("accused_name", "person_name", "victim_name", "complainant_name", "officer_name"):
            val = row.get(key)
            if val and str(val).strip():
                people.append(str(val).strip())
        for key in ("unit_name", "police_station", "district_name"):
            val = row.get(key)
            if val and str(val).strip():
                places.append(str(val).strip())
    bits: list[str] = []
    crimes = _unique_keep_order(crime_nos, 4)
    if crimes:
        bits.append("crime " + ", ".join(crimes))
    persons = _unique_keep_order(people, 4)
    if persons:
        bits.append("person " + ", ".join(persons))
    locs = _unique_keep_order(places, 4)
    if locs:
        bits.append("place " + ", ".join(locs))
    return "; ".join(bits)


_SKIP_HISTORY_QUESTIONS = {
    "(call started)",
    "(voice)",
    "(voice call)",
}


def _prior_from_turns(turns: list[dict] | None, *, limit: int = 8) -> list:
    """Rebuild chat memory from DB turns, injecting row anchors for follow-ups."""
    prior: list = []
    for turn in (turns or [])[-limit:]:
        question = (turn.get("question") or "").strip()
        answer = (turn.get("answer") or "").strip()
        if question.lower() in _SKIP_HISTORY_QUESTIONS:
            continue
        if not question and not answer:
            continue
        anchors = _anchors_from_rows(turn.get("rows") or [])
        if anchors:
            answer = f"{answer}\n[context: {anchors}]".strip()
        prior.append(HumanMessage(content=question or "(follow-up)"))
        prior.append(AIMessage(content=answer or "(no answer)"))
    return prior


def _is_analytics_question(question: str) -> bool:
    q = (question or "").strip().lower()
    if any(
        w in q
        for w in (
            "hotspot", "trend", "dashboard", "over time", "by month", "aggregate",
            "demographic", "socio", "gender", "occupation", "age band", "age group",
            "early warning", "early warnings", "warning", "warnings", "spike",
            "proactive", "prevention", "watchlist", "predict", "forecast",
            "pattern", "patterns", "profiling", "behavioral", "behavioural",
            "profile of", "offender profile",
            # Kannada analytics cues
            "ಹಾಟ್", "ಟ್ರೆಂಡ್", "ಎಚ್ಚರಿಕೆ", "ಮಾದರಿ", "ಪ್ರವೃತ್ತಿ",
        )
    ):
        return True
    return _is_aggregate_question(question)


def _is_aggregate_question(question: str) -> bool:
    """Counting / ranking questions ("how many", "which station has most")."""
    from agents.analytics_agent import _COUNT_RE, _GROUP_RE
    from agents.sql_agent import _extract_crime_no

    q = (question or "").strip()
    if not q or _extract_crime_no(q):
        return False
    return bool(_COUNT_RE.search(q) or _GROUP_RE.search(q))


def _last_crime_no_from_history(history: list) -> str | None:
    """Pull the active FIR number — prefer the investigator's last mention."""
    import re

    for m in reversed(history or []):
        content = m.content if hasattr(m, "content") else str(m)
        # Prefer structured anchors saved from rows_json.
        tagged = re.search(r"\[context:[^\]]*?\bcrime\s+(\d{10,})", content or "", re.I)
        if tagged:
            return tagged.group(1)
    for m in reversed(history or []):
        if isinstance(m, HumanMessage):
            found = re.findall(r"\b(\d{10,})\b", m.content or "")
            if found:
                return found[-1]
    for m in reversed(history or []):
        if isinstance(m, AIMessage):
            found = re.findall(r"\b(\d{10,})\b", m.content or "")
            if found:
                # First number is usually the subject FIR (lists put others later).
                return found[0]
    return None


def _last_person_from_history(history: list) -> str | None:
    """Best-effort person name from recent turns (repeat-offender / network follow-ups)."""
    import re

    for m in reversed(history or []):
        content = m.content if hasattr(m, "content") else str(m)
        tagged = re.search(r"\[context:[^\]]*?\bperson\s+([^;\]]+)", content or "", re.I)
        if tagged:
            name = tagged.group(1).split(",")[0].strip()
            if name and name.lower() not in ("crime", "case", "fir"):
                return name
        # "Is NAME a repeat offender?"
        hit = re.search(
            r"(?i)(?:is\s+)?([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s+(?:a\s+)?repeat\s+offender",
            content or "",
        )
        if hit:
            return hit.group(1).strip()
        hit = re.search(
            r"(?i)(?:related to|involving|network for|profile of|tell about|about)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)",
            content or "",
        )
        if hit:
            name = hit.group(1).strip()
            if name.lower() not in ("crime", "case", "fir"):
                return name
    return None


def _last_place_from_history(history: list) -> str | None:
    """Last station / district mentioned — for follow-ups like 'only pending ones'."""
    import re

    for m in reversed(history or []):
        content = m.content if hasattr(m, "content") else str(m)
        tagged = re.search(r"\[context:[^\]]*?\bplace\s+([^;\]]+)", content or "", re.I)
        if tagged:
            return tagged.group(1).split(",")[0].strip()
        hit = re.search(
            r"(?i)\bin\s+([A-Za-z][A-Za-z .-]{2,}?)(?:\s+(?:cases?|firs?|robbery|murder|theft|station))?\s*[.?]?\s*$",
            content or "",
        )
        if hit:
            place = hit.group(1).strip(" .")
            if place.lower() not in {"the", "this", "that", "total", "all"}:
                return place
    return None


def _resolve_followup_question(question: str, history: list) -> str:
    """Rewrite vague follow-ups using the last crime number / person / place in context."""
    q = (question or "").strip()
    crime_no = _last_crime_no_from_history(history)
    person = _last_person_from_history(history)
    place = _last_place_from_history(history)

    # Already has an explicit crime number — keep as-is (briefing layer will use it).
    has_crime = bool(re.search(r"\b\d{10,}\b", q))

    # Export / investigate-next / summary / accused / victims / timeline / similar / network
    if re.search(r"(?i)\b(export\s+(?:investigation\s+)?report|generate\s+(?:investigation\s+)?report)\b|ತನಿಖಾ\s*ವರದಿ|ವರದಿ\s*ರಚಿಸಿ", q):
        return q
    if re.search(r"(?i)\brepeat\s+offender\b|ಪುನರಾವರ್ತಿತ", q):
        if person and not re.search(re.escape(person), q, re.I):
            return f"Is {person} a repeat offender?"
        return q

    if re.search(r"(?i)\b(his|her)\s+(criminal\s+)?network\b", q) and not has_crime:
        if person:
            return f"Show criminal network for {person}"

    # Status filters referring to the last search place.
    status_hit = re.search(
        r"(?i)\b(only |just )?(pending|closed|charge\s*sheeted|under investigation)\b",
        q,
    )
    if status_hit and place and not has_crime and not re.search(r"(?i)\bin\s+[A-Za-z]", q):
        status = re.sub(r"\s+", " ", status_hit.group(2).strip().lower())
        if status == "pending":
            status = "under investigation"
        return f"Show {status} cases in {place}"

    if crime_no and not has_crime:
        if re.search(
            r"(?i)\b(more details?|tell me more|explain|details?|this case|that case|the case|"
            r"what about (?:it|this|that)|full (?:details?|info)|brief)\b|"
            r"ವಿವರ|ಹೆಚ್ಚು\s*ಹೇಳಿ",
            q,
        ):
            return f"Explain crime {crime_no}"
        if re.search(r"(?i)\b(case summary|summar(?:y|ise|ize))\b|ಪ್ರಕರಣದ\s*ಸಾರಾಂಶ", q):
            return f"Case summary for crime {crime_no}"
        if re.search(r"(?i)^(show|list|get|display)?\s*accused\b|ಆರೋಪಿತ", q) and not re.search(
            r"(?i)\brelated to|involving\b", q
        ):
            return f"Show accused for crime {crime_no}"
        if re.search(r"(?i)^(show|list|get|display)?\s*victims?\b|ಪೀಡಿತ", q):
            return f"Show victims for crime {crime_no}"
        if re.search(r"(?i)\b(timeline|stages)\b|ತನಿಖೆಯ\s*ಹಂತ", q):
            return f"Show timeline for crime {crime_no}"
        if re.search(r"(?i)\b(similar cases?)\b|ಇದೇ\s*ರೀತಿಯ\s*ಪ್ರಕರಣ", q):
            return f"Find similar cases for crime {crime_no}"
        if re.search(
            r"(?i)\b(what should i investigate|investigate next|next steps?)\b|ಮುಂದಿನ\s*ತನಿಖ|ನಾನು\s*ಏನು\s*ಮಾಡಬೇಕು",
            q,
        ):
            return f"What should I investigate next for crime {crime_no}"
        if re.search(
            r"(?i)\b(his|her)\s+(criminal\s+)?network\b|"
            r"\b(criminal network|network analysis|show (?:his |her )?network)\b|ಅಪರಾಧ\s*ಜಾಲ",
            q,
        ):
            if person and re.search(r"(?i)\b(his|her)\s+(criminal\s+)?network\b", q):
                return f"Show criminal network for {person}"
            return f"Show criminal network for crime {crime_no}"
        if re.search(r"(?i)\b(this case|that case|the case|relations?|linked|associates|connected)\b", q):
            if re.search(r"(?i)\b(relation|relations|network|linked|associates|connected|co-accused)\b", q):
                return f"Show relations for crime {crime_no}"
            if re.search(r"(?i)\b(this case|that case|the case|explain|details)\b", q):
                return f"Explain crime {crime_no}"
        if re.search(r"(?i)\b(status|case status)\b|ಸ್ಥಿತಿ", q):
            return f"What is the status of crime {crime_no}"

    if person and not has_crime and re.search(
        r"(?i)^(tell|show|what).{0,20}\b(about|him|her|them)\b|^(his|her)\s+(cases?|history|profile)\b",
        q,
    ):
        return f"Tell about {person}"

    if not re.search(r"(?i)\b(this case|that case|the case|relations?|network|linked|associates|connected)\b", q):
        return q
    if has_crime:
        return q
    if not crime_no:
        return q
    if re.search(r"(?i)\b(relation|relations|network|linked|associates|connected|co-accused)\b", q):
        return f"Show relations for crime {crime_no}"
    if re.search(r"(?i)\b(this case|that case|the case|explain|details)\b", q):
        return f"Explain crime {crime_no}"
    return q


def _looks_like_followup(question: str) -> bool:
    q = (question or "").strip()
    if not q:
        return False
    if re.search(r"\b\d{10,}\b", q):
        return False
    return bool(
        re.search(
            r"(?i)\b(this|that|those|these|it|them|him|her|same|previous|above|"
            r"more details?|tell me more|only|just|pending|accused|victims?|"
            r"timeline|status|similar|network|relations?)\b|"
            r"ಆರೋಪಿತ|ಪೀಡಿತ|ವಿವರ|ಸ್ಥಿತಿ",
            q,
        )
    )


def _heuristic_route(question: str) -> Route | None:
    """Skip the LLM router for clear demo phrasings (faster + more reliable)."""
    from agents.sql_agent import _template_sql

    q = (question or "").strip().lower()
    if not q:
        return None

    # Greetings, identity, capability, thanks, off-topic — never a data lookup.
    if smalltalk_reply(question, "en-IN") is not None:
        return "chitchat"

    # Analytics must win over fragile person-name SQL templates.
    if _is_analytics_question(question):
        return "analytics"

    if any(w in q for w in ("network", "linked to", "co-accused", "associates of", "who else", "relations")):
        return "graph"

    # Template-matched crime lookups → SQL (no Gemini router).
    if _template_sql(question):
        return "sql"

    # Kannada script questions about crime data → SQL by default.
    if has_kannada(question) and any(
        w in question
        for w in (
            "ಕೇಸ್", "ಕೇಸು", "ಪ್ರಕರಣ", "ದರೋಡೆ", "ಕೊಲೆ", "ಕಳ್ಳತನ",
            "ಆರೋಪಿ", "ಹಾಟ್", "ಟ್ರೆಂಡ್", "ಸ್ಟೇಷನ್", "ಫಿರ್", "FIR",
            "ಎಚ್ಚರಿಕೆ", "ಠಾಣೆ", "ಜಿಲ್ಲೆ", "ಒಟ್ಟು", "ಎಷ್ಟು",
        )
    ):
        if any(w in question for w in ("ಹಾಟ್", "ಟ್ರೆಂಡ್", "ಎಚ್ಚರಿಕೆ", "hotspot", "trend")):
            return "analytics"
        if any(w in question for w in ("ನೆಟ್‌ವರ್ಕ್", "network", "ಸಂಬಂಧ", "ಜಾಲ")):
            return "graph"
        return "sql"
    crime_words = (
        "robbery", "theft", "murder", "kidnapping", "assault", "cheating", "fraud",
        "fir", "case", "accused", "victim", "station", "crime no", "pending",
        "tell about", "about ", "related", "involving",
    )
    if any(w in q for w in crime_words) or " in " in q:
        return "sql"
    return None


def route_node(state: ConversationState) -> ConversationState:
    # The checkpointer persists state across turns in the same session, so any
    # "answer"/"tool"/"query"/"rows" left over from the previous turn must be
    # cleared here — otherwise a later turn whose node doesn't set "answer"
    # (sql/graph) inherits the prior turn's answer and synthesize_node skips
    # regenerating it (it treats a present "answer" as already-final).
    # Preserve history from the checkpointer; do not wipe it on each turn.
    history = list(state.get("history") or [])
    state = {**state, "answer": "", "tool": "", "query": "", "rows": [], "history": history}
    heuristic = _heuristic_route(state["question"])
    if heuristic:
        return {**state, "route": heuristic}
    llm = get_llm()
    # Include recent history so follow-ups ("show only pending ones") still route to sql.
    context = _history_context(history)
    router_input = state["question"]
    if context:
        router_input = f"Recent conversation:\n{context}\n\nCurrent question: {state['question']}"
    resp = llm.invoke([SystemMessage(content=ROUTER_PROMPT), HumanMessage(content=router_input)])
    route = resp.content.strip().lower()
    if route not in ("sql", "graph", "analytics", "chitchat"):
        route = "sql"
    return {**state, "route": route}


def sql_node(state: ConversationState) -> ConversationState:
    import logging
    log = logging.getLogger(__name__)
    context = _history_context(state.get("history", []))
    try:
        result = run_sql_question(state["question"], context)
    except Exception as exc:
        log.exception("sql_agent failed for %r", state["question"])
        # Neon idle disconnects used to surface here as a fake "couldn't form SQL" message.
        err = str(exc).lower()
        if "ssl" in err or "connection" in err or "operational" in err or "closed" in err:
            answer = (
                "The crime database connection dropped — please try that question again in a moment."
            )
        else:
            answer = (
                "I couldn't turn that into a database query — could you name a specific "
                "case number, station, crime type, or person?"
            )
        return {
            **state, "tool": "sql_agent", "query": "", "rows": [],
            "answer": answer,
        }
    return {**state, "tool": "sql_agent", "query": result["sql"], "rows": result["rows"]}


def graph_node(state: ConversationState) -> ConversationState:
    try:
        result = run_graph_question(state["question"])
    except Exception:
        return {
            **state, "tool": "graph_agent", "query": "", "rows": [],
            "answer": "I couldn't resolve that in the network graph — could you name a specific "
                      "accused, case number, or officer to search from?",
        }
    return {**state, "tool": "graph_agent", "query": result["cypher"], "rows": result["rows"]}


def analytics_node(state: ConversationState) -> ConversationState:
    try:
        result = run_analytics_question(state["question"])
    except Exception:
        return {
            **state,
            "tool": "analytics_agent",
            "query": "",
            "rows": [],
            "answer": "I couldn't compute that aggregate — try asking for crime trends, hotspots, or status counts.",
        }
    # Leave answer empty so synthesize_node turns rows into plain language,
    # except for aggregates which already carry their own phrasing.
    return {
        **state,
        "tool": "analytics_agent",
        "query": result["query"],
        "rows": result["rows"],
        "answer": result.get("answer") or "",
    }


async def chitchat_node(state: ConversationState) -> ConversationState:
    # async + astream (rather than invoke) so stream_mode="messages" can forward
    # tokens for this node to the SSE endpoint — see routers/chat.py's /chat/stream.
    lang = resolve_reply_language(state["question"], state.get("language_code") or "en-IN")
    canned = smalltalk_reply(state["question"], lang)
    if canned:
        return {
            **state,
            "tool": "chitchat",
            "query": "",
            "rows": [],
            "answer": canned["answer"],
            "language_code": lang,
        }

    # Never invent crime facts from unclear / off-domain chatter.
    from core.language import didnt_catch_reply, is_usable_voice_transcript

    q = (state.get("question") or "").strip()
    if not is_usable_voice_transcript(q) or len(q.split()) < 3:
        answer = didnt_catch_reply(lang)
        return {
            **state,
            "tool": "chitchat",
            "query": "",
            "rows": [],
            "answer": answer,
            "language_code": lang,
        }

    llm = get_llm()
    prompt = (
        f"{CRIME_AI_IDENTITY}\n\n"
        f"{language_instruction(lang)}\n\n"
        "Keep the reply short (1-2 sentences). Stay in character as Crime AI.\n"
        "Do NOT invent FIRs, case numbers, people, or crime statistics.\n"
        "If the message is unclear audio noise or not about crime data, ask them to "
        "repeat a clear crime-data question (FIR, place, person, hotspot).\n\n"
        f"Investigator: {state['question']}"
    )
    chunks = [chunk.content async for chunk in llm.astream([HumanMessage(content=prompt)])]
    answer = strip_vendor_identity(_sanitize_answer("".join(chunks)), lang)
    return {**state, "tool": "chitchat", "query": "", "rows": [], "answer": answer, "language_code": lang}


SYNTHESIZE_RULES = f"""{CRIME_AI_IDENTITY}

You are briefing a Karnataka Police investigator over chat/voice.

Format rules (strict):
- Start with one short summary line (e.g. English: "Found 2 robbery FIRs in Hubballi." /
  casual Kannada mix: "Hubballi ನಲ್ಲಿ 2 robbery cases ಸಿಕ್ಕಿವೆ.").
- Then a short numbered list — one line per case. Prefer at most 5 items for voice.
- Each case line: police station - short crime/case hint - brief facts in plain words.
- Prefer spoken language: do not read long digit strings aloud.
- Use plain ASCII hyphen (-) only. Never use em-dash, en-dash, or special unicode punctuation.
- Do NOT dump database column labels (never write "Crime No:", "Case No:", "Brief Facts:",
  "Police Station:", "Crime Type:", "unit_name:", etc.).
- Do NOT use markdown bold (**...**) or bullet dumps of every field.
- Keep it scannable and spoken-friendly (this answer may be read aloud).
- If rows is empty, say no matching records were found (in the reply language).
- Always answer from the rows when rows are present — never invent a refusal.
"""


def _sanitize_answer(text: str) -> str:
    """Normalize model output so the UI doesn't show garbled characters."""
    if not text:
        return text
    replacements = {
        "\u2014": "-",  # em dash
        "\u2013": "-",  # en dash
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2026": "...",
        "\ufffd": "-",  # replacement char often seen as garbled dash
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return text.strip()


def _no_results_answer(question: str, language_code: str) -> str:
    """Empty-result reply that names searches which will actually return data."""
    lang = resolve_reply_language(question, language_code)
    kn = lang == "kn-IN"
    hints: list[str] = []
    try:
        from agents.sql_agent import extract_filters, _crime_terms_from_db, _place_terms_from_db

        filters = extract_filters(question)
        crime, place, status, year = (
            filters.get("crime"), filters.get("place"), filters.get("status"), filters.get("year"),
        )
        if crime and place:
            hints.append(
                f"{crime} ಪ್ರಕರಣಗಳು (ಎಲ್ಲಾ ಜಿಲ್ಲೆ)" if kn else f"{crime} cases across all districts"
            )
            hints.append(f"{place} ಎಲ್ಲಾ ಪ್ರಕರಣಗಳು" if kn else f"All cases in {place}")
        elif crime:
            hints.append(
                f"{crime} ಪ್ರಕರಣಗಳು ಎಷ್ಟು?" if kn else f"How many {crime} cases are there?"
            )
        if year:
            other = 2025 if int(year) != 2025 else 2026
            hints.append(f"{other} ರ ಪ್ರಕರಣಗಳು" if kn else f"Cases registered in {other}")
        if status:
            hints.append("ಎಲ್ಲಾ ಪ್ರಕರಣಗಳ ಸ್ಥಿತಿ" if kn else "Cases by status")
        if not hints:
            crimes = _crime_terms_from_db()[:3]
            places = [p for p in _place_terms_from_db() if len(p) > 4][:2]
            if crimes:
                hints.append(
                    f"{crimes[0]} ಪ್ರಕರಣಗಳನ್ನು ತೋರಿಸಿ" if kn else f"Show {crimes[0]} cases"
                )
            if places:
                hints.append(
                    f"{places[0]} ನಲ್ಲಿ ಪ್ರಕರಣಗಳು" if kn else f"Cases in {places[0]}"
                )
            hints.append("ಒಟ್ಟು ಎಷ್ಟು ಪ್ರಕರಣಗಳಿವೆ?" if kn else "How many cases are there in total?")
    except Exception:
        pass
    return no_results_message(lang, hints)


def _format_rows_fallback(question: str, rows: list, language_code: str = "en-IN") -> str:
    """Deterministic briefing when LLM synthesis fails or returns garbage."""
    lang = resolve_reply_language(question, language_code)
    if not rows:
        return _no_results_answer(question, lang)
    q = (question or "").lower()

    # Early warnings
    if rows and "recommendation" in (rows[0] or {}):
        if lang == "kn-IN":
            lines = [f"Early warnings: {len(rows)} rising hotspots."]
            for i, row in enumerate(rows[:12], 1):
                crime_kn = localize_label(row.get("crime_head_name"), lang)
                lines.append(
                    f"{i}. {localize_label(row.get('unit_name'), lang)} "
                    f"({localize_label(row.get('district_name'), lang)}) - "
                    f"{crime_kn}: {row.get('previous_count')} ಇಂದ {row.get('current_count')} ಕ್ಕೆ rise. "
                    f"Patrolling ಹೆಚ್ಚಿಸಿ."
                )
            return "\n".join(lines)
        lines = [f"Early warnings: {len(rows)} rising hotspot(s)."]
        for i, row in enumerate(rows[:12], 1):
            lines.append(
                f"{i}. {row.get('unit_name')} ({row.get('district_name')}) - "
                f"{row.get('crime_head_name')}: {row.get('previous_count')}->{row.get('current_count')} "
                f"(+{row.get('delta')}). {row.get('recommendation')}"
            )
        return "\n".join(lines)

    # Socio segments
    if rows and str((rows[0] or {}).get("segment") or "").startswith(("accused", "victim", "complainant")):
        seg_kn = {
            "accused_age": "ಆರೋಪಿ ವಯಸ್ಸು",
            "accused_gender": "ಆರೋಪಿ ಲಿಂಗ",
            "victim_age": "ಪೀಡಿತ ವಯಸ್ಸು",
            "victim_gender": "ಪೀಡಿತ ಲಿಂಗ",
            "complainant_occupation": "ದೂರುದಾರರ ಉದ್ಯೋಗ",
        }
        header = (
            "Socio-demographic insights:"
            if lang == "kn-IN"
            else "Socio-demographic insights from the crime database:"
        )
        lines = [header]
        for row in rows[:25]:
            seg = str(row.get("segment") or "")
            label = seg_kn.get(seg, seg) if lang == "kn-IN" else seg
            value = row.get("age_band") if "age_band" in row else row.get("label")
            lines.append(f"- {label}: {value} -> {row.get('count')}")
        return "\n".join(lines)

    # Pattern clusters
    if rows and (rows[0] or {}).get("pattern"):
        if lang == "kn-IN":
            lines = [f"Crime patterns ({len(rows)}):"]
            for i, row in enumerate(rows[:12], 1):
                crime_kn = localize_label(row.get("crime_head_name"), lang)
                lines.append(
                    f"{i}. {localize_label(row.get('unit_name'), lang)} "
                    f"repeat {crime_kn} - {row.get('case_count')} cases"
                )
            return "\n".join(lines)
        lines = [f"Crime patterns found ({len(rows)} clusters):"]
        for i, row in enumerate(rows[:12], 1):
            lines.append(
                f"{i}. {row.get('pattern')} - {row.get('case_count')} cases "
                f"({row.get('first_seen')} to {row.get('last_seen')})"
            )
        return "\n".join(lines)

    kn = lang == "kn-IN"
    lines = [found_results_header(lang, len(rows), question)]
    for i, row in enumerate(rows[:20], 1):
        station = localize_label(row.get("unit_name") or row.get("police_station") or "", lang)
        crime = localize_label(row.get("crime_head_name") or row.get("crime_type") or "", lang)
        crime_no = row.get("crime_no") or row.get("case_no") or ""
        facts = localize_facts(row.get("brief_facts") or row.get("name") or "", lang)
        accused = row.get("accused_name") or row.get("person_name") or ""
        officer = row.get("officer_name") or ""
        role = row.get("role") or ""
        district = localize_label(row.get("district_name") or "", lang)
        count = row.get("case_count") or row.get("count")
        group = localize_label(row.get("crime_group_name") or "", lang)
        status = localize_label(row.get("case_status_name") or "", lang)
        if count is not None and not crime_no:
            label = (
                station
                or district
                or crime
                or status
                or officer
                or row.get("year")
                or row.get("month")
                or ""
            )
            suffix = f" ({district})" if district and station else ""
            lines.append(f"{i}. {label}{suffix} - {count} cases")
        else:
            who = accused or officer
            if who and role:
                shown = str(role).lower() if kn else role
                who = f"{who} ({shown})"
            crime_label = f"Crime {crime_no}" if crime_no else ""
            parts = [
                p
                for p in [station, crime, group, crime_label, who, status, facts]
                if p
            ]
            lines.append(f"{i}. " + " - ".join(str(p) for p in parts))
    return "\n".join(lines)


async def synthesize_node(state: ConversationState) -> ConversationState:
    lang = resolve_reply_language(state["question"], state.get("language_code") or "en-IN")
    lang_line = language_instruction(lang)
    rows = state.get("rows") or []
    tool = state.get("tool") or ""

    if state.get("answer"):
        answer = _sanitize_answer(state["answer"])
    elif tool in ("sql_agent", "analytics_agent", "graph_agent"):
        # Fast path: skip Gemini synthesis for normal SQL/analytics/graph rows.
        # Deterministic briefing is clearer for investigators and ~1–3s faster.
        if rows or tool == "sql_agent":
            answer = _format_rows_fallback(state["question"], rows, lang)
        else:
            llm = get_llm()
            prompt = (
                f"{SYNTHESIZE_RULES}\n\n{lang_line}\n\n"
                f"Investigator asked: {state['question']}\n"
                f"Query executed: {state['query']}\n"
                f"Rows returned (0): []\n\n"
                "Write the briefing now."
            )
            try:
                chunks = [chunk.content async for chunk in llm.astream([HumanMessage(content=prompt)])]
                answer = _sanitize_answer("".join(chunks)) or empty_records_message(lang)
            except Exception:
                answer = empty_records_message(lang)
    else:
        answer = empty_records_message(lang)

    history = list(state.get("history") or [])
    history.append(HumanMessage(content=state["question"]))
    history.append(AIMessage(content=answer))
    history = history[-16:]
    answer = strip_vendor_identity(_sanitize_answer(answer), lang)
    return {**state, "answer": answer, "history": history, "language_code": lang}


def build_graph():
    graph = StateGraph(ConversationState)
    graph.add_node("route", route_node)
    graph.add_node("sql", sql_node)
    graph.add_node("graph", graph_node)
    graph.add_node("analytics", analytics_node)
    graph.add_node("chitchat", chitchat_node)
    graph.add_node("synthesize", synthesize_node)

    graph.set_entry_point("route")
    graph.add_conditional_edges("route", lambda s: s["route"], {
        "sql": "sql", "graph": "graph", "analytics": "analytics", "chitchat": "chitchat",
    })
    for node in ("sql", "graph", "analytics", "chitchat"):
        graph.add_edge(node, "synthesize")
    graph.add_edge("synthesize", END)

    return graph.compile(checkpointer=MemorySaver())


_compiled_graph = None


def get_conversation_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


def reset_conversation_graph() -> None:
    """Drop the cached graph (useful after prompt/identity changes under --reload)."""
    global _compiled_graph
    _compiled_graph = None


def _merge_input(question: str, prior_history: list | None, language_code: str = "en-IN") -> dict:
    """Start each turn with the new question but keep checkpointer history.
    Passing history: [] on every invoke was wiping memory — load prior from
    the snapshot when available; otherwise start fresh."""
    return {
        "question": question,
        "language_code": normalize_language(language_code),
        "history": list(prior_history or []),
    }


async def ask(
    session_id: str,
    question: str,
    *,
    username: str | None = None,
    officer_name: str | None = None,
    language_code: str = "en-IN",
) -> dict:
    # synthesize_node/chitchat_node are async (they use llm.astream() so the SSE
    # endpoint can forward tokens), so the graph must be run via ainvoke — plain
    # .invoke() errors with "No synchronous function provided" on those nodes.
    from core.history import load_turns, save_turn

    lang = resolve_reply_language(question, language_code)
    canned = smalltalk_reply(question, lang, officer_name=officer_name)
    if canned:
        save_turn(
            session_id, question, canned["answer"], tool="chitchat", query=None,
            username=username, rows=[],
        )
        return {
            "answer": canned["answer"],
            "tool": "chitchat",
            "query": "",
            "rows": [],
            "suggestions": canned.get("suggestions") or [],
            "language_code": lang,
        }

    graph = get_conversation_graph()
    config = {"configurable": {"thread_id": session_id}}
    snapshot = await graph.aget_state(config)
    prior = (snapshot.values or {}).get("history") if snapshot and snapshot.values else []
    db_prior = _prior_from_turns(load_turns(session_id), limit=10)
    if db_prior:
        prior = db_prior
    elif not prior:
        prior = []
    resolved = _resolve_followup_question(question, prior or [])

    # Fast paths first. Empty rows after a template/analytics hit usually means a
    # typo (e.g. "kidnaping") — let the LLM rewrite against DB vocabulary, then retry.
    # Investigation briefings may intentionally have empty rows (e.g. export checklist).
    fast = _try_fast_paths(resolved, language_code)
    if fast is not None and (
        fast.get("rows") or fast.get("tool") == "investigation_briefing"
    ):
        save_turn(
            session_id,
            question,
            fast["answer"],
            tool=fast.get("tool"),
            query=fast.get("query"),
            username=username,
            rows=fast.get("rows") or [],
        )
        return fast

    if fast is not None and not fast.get("rows"):
        rewritten = _rewrite_question_with_llm(resolved)
        if rewritten != resolved:
            fast2 = _try_fast_paths(rewritten, language_code)
            if fast2 is not None and (
                fast2.get("rows") or fast2.get("tool") == "investigation_briefing"
            ):
                save_turn(
                    session_id,
                    question,
                    fast2["answer"],
                    tool=fast2.get("tool"),
                    query=fast2.get("query"),
                    username=username,
                    rows=fast2.get("rows") or [],
                )
                return fast2
            resolved = rewritten

    # Full graph — LLM SQL generation also corrects typos against the schema.
    result = await graph.ainvoke(_merge_input(resolved, prior, language_code), config=config)
    answer = _sanitize_answer(result["answer"])
    save_turn(
        session_id,
        question,
        answer,
        tool=result.get("tool"),
        query=result.get("query"),
        username=username,
        rows=result.get("rows") or [],
    )
    return {
        "answer": answer,
        "tool": result.get("tool"),
        "query": result.get("query"),
        "rows": result.get("rows", []),
        "suggestions": _default_suggestions_for(resolved, result.get("rows") or [], lang),
        "language_code": result.get("language_code") or resolve_reply_language(question, language_code),
    }


# Nodes that actually produce the final answer text — only their token stream
# should reach the client (route_node's one-word classification, and the raw
# sql/graph tool calls, must not leak into the SSE stream).
_ANSWER_NODES = {"synthesize", "chitchat"}


def _chunk_text(text: str, size: int = 48):
    for i in range(0, len(text), size):
        yield text[i : i + size]


def _rewrite_question_with_llm(question: str) -> str:
    """Ask Gemini to fix spelling / clarify intent using live DB vocabulary.

    Used when templates miss or return empty — typos like "kidnaping" are
    corrected by the model against real crime_head / place labels, not a
    hardcoded dictionary.
    """
    from agents.sql_agent import _crime_terms_from_db, _place_terms_from_db

    q = (question or "").strip()
    if not q or len(q) < 3:
        return q
    try:
        crimes = ", ".join(_crime_terms_from_db()[:40])
        places = ", ".join(_place_terms_from_db()[:40])
    except Exception:
        crimes, places = "Robbery, Theft, Murder, Kidnapping, Assault", "Hubballi, Bengaluru, Mysuru"
    prompt = (
        "You rewrite Karnataka Police investigator questions for a crime database search.\n"
        "Rules:\n"
        "- Fix spelling typos (e.g. kidnaping -> Kidnapping, robery -> Robbery).\n"
        "- Prefer official crime labels from this list when close: "
        f"{crimes}.\n"
        "- Prefer station/district spellings from this list when close: "
        f"{places}.\n"
        "- Keep the investigator's meaning; do not add new filters.\n"
        "- Reply with ONLY the corrected question text — no quotes, no explanation.\n\n"
        f"Question: {q}"
    )
    try:
        llm = get_llm()
        resp = llm.invoke([HumanMessage(content=prompt)])
        text = (resp.content if isinstance(resp.content, str) else str(resp.content)).strip()
        text = text.strip().strip('"').strip("'")
        # Reject runaway rewrites.
        if not text or len(text) > max(200, len(q) * 3):
            return q
        return text
    except Exception:
        return q


def _try_fast_paths(question: str, language_code: str) -> dict | None:
    """Investigation briefing, analytics, or template SQL without full LangGraph."""
    from agents.analytics_agent import run_analytics_question
    from agents.investigation_briefing import run_investigation_briefing
    from agents.sql_agent import _template_sql, run_sql_question

    lang = resolve_reply_language(question, language_code)

    briefing = run_investigation_briefing(question, language_code=lang)
    if briefing is not None:
        return {
            "answer": briefing["answer"],
            "tool": briefing.get("tool") or "investigation_briefing",
            "query": briefing.get("query") or "",
            "rows": briefing.get("rows") or [],
            "suggestions": briefing.get("suggestions") or [],
            "language_code": briefing.get("language_code") or lang,
        }

    if _is_analytics_question(question):
        try:
            result = run_analytics_question(question)
            rows = result.get("rows") or []
            return {
                "answer": result.get("answer") or _format_rows_fallback(question, rows, lang),
                "tool": "analytics_agent",
                "query": result.get("query") or "",
                "rows": rows,
                "suggestions": _default_suggestions_for(question, rows, lang),
                "language_code": lang,
            }
        except Exception:
            return None

    if _template_sql(question):
        try:
            result = run_sql_question(question)
            rows = result.get("rows") or []
            # Promote single-FIR template hits to a structured card when possible.
            if len({str(r.get("crime_no")) for r in rows if r.get("crime_no")}) == 1:
                cn = str(rows[0].get("crime_no"))
                card = run_investigation_briefing(
                    f"Show FIR {cn}", language_code=lang, crime_no=cn
                )
                if card and card.get("answer"):
                    return {
                        "answer": card["answer"],
                        "tool": "investigation_briefing",
                        "query": result.get("sql") or card.get("query") or "",
                        "rows": card.get("rows") or rows,
                        "suggestions": card.get("suggestions") or [],
                        "language_code": lang,
                    }
            return {
                "answer": _format_rows_fallback(question, rows, lang),
                "tool": "sql_agent",
                "query": result.get("sql") or "",
                "rows": rows,
                "suggestions": _default_suggestions_for(question, rows, lang),
                "language_code": lang,
            }
        except Exception:
            return None
    return None


def _default_suggestions_for(question: str, rows: list, language_code: str) -> list[dict]:
    """Contextual next-action chips when not using investigation_briefing."""
    kn = resolve_reply_language(question, language_code) == "kn-IN"
    crime_nos = []
    for r in rows or []:
        cn = r.get("crime_no")
        if cn and str(cn) not in crime_nos:
            crime_nos.append(str(cn))
    person = None
    for r in rows or []:
        for k in ("person_name", "accused_name", "officer_name"):
            if r.get(k):
                person = str(r[k])
                break
        if person:
            break
    if crime_nos:
        cn = crime_nos[0]
        if kn:
            return [
                {"label": "ಸಾರಾಂಶ", "message": f"ಪ್ರಕರಣದ ಸಾರಾಂಶ crime {cn}"},
                {"label": "ಆರೋಪಿತರು", "message": f"ಆರೋಪಿತರನ್ನು ತೋರಿಸಿ crime {cn}"},
                {"label": "ಜಾಲ", "message": f"ಅಪರಾಧ ಜಾಲ crime {cn}"},
            ]
        return [
            {"label": "Case summary", "message": f"Case summary for crime {cn}"},
            {"label": "Show accused", "message": f"Show accused for crime {cn}"},
            {"label": "Criminal network", "message": f"Show criminal network for crime {cn}"},
        ]
    if person:
        if kn:
            return [
                {"label": "ಪುನರಾವರ್ತಿತ?", "message": f"{person} ಪುನರಾವರ್ತಿತ ಆರೋಪಿಯೇ?"},
                {"label": "ಜಾಲ", "message": f"{person} ಅಪರಾಧ ಜಾಲವನ್ನು ತೋರಿಸಿ"},
            ]
        return [
            {"label": "Repeat offender?", "message": f"Is {person} a repeat offender?"},
            {"label": "Criminal network", "message": f"Show criminal network for {person}"},
        ]
    if kn:
        return [
            {"label": "ಆರಂಭಿಕ ಎಚ್ಚರಿಕೆ", "message": "ಆರಂಭಿಕ ಎಚ್ಚರಿಕೆಗಳು ಏನು?"},
            {"label": "ಹಾಟ್‌ಸ್ಪಾಟ್", "message": "ಅಪರಾಧ ಹಾಟ್‌ಸ್ಪಾಟ್‌ಗಳು ಎಲ್ಲಿವೆ?"},
        ]
    return [
        {"label": "Early warnings", "message": "What are the early warnings?"},
        {"label": "Hotspots", "message": "Where are the crime hotspots?"},
    ]


async def astream_ask(
    session_id: str,
    question: str,
    *,
    username: str | None = None,
    officer_name: str | None = None,
    language_code: str = "en-IN",
):
    """Yields answer text chunks as they're generated, then a final dict with
    the audit-trail metadata (tool/query/rows) once the graph run completes."""
    from core.history import load_turns, save_turn

    lang = resolve_reply_language(question, language_code)
    canned = smalltalk_reply(question, lang, officer_name=officer_name)
    if canned:
        for piece in _chunk_text(canned["answer"]):
            yield {"token": piece}
        save_turn(
            session_id, question, canned["answer"], tool="chitchat", query=None,
            username=username, rows=[],
        )
        yield {
            "done": True,
            "answer": canned["answer"],
            "tool": "chitchat",
            "query": "",
            "rows": [],
            "suggestions": canned.get("suggestions") or [],
            "language_code": lang,
        }
        return

    graph = get_conversation_graph()
    config = {"configurable": {"thread_id": session_id}}
    snapshot = await graph.aget_state(config)
    prior = (snapshot.values or {}).get("history") if snapshot and snapshot.values else []
    if not prior:
        # Recover context after process restart from persisted chat_history.
        prior = _prior_from_turns(load_turns(session_id), limit=10)
    else:
        # Prefer DB anchors (FIR/person/place) when checkpointer text lacks them.
        db_prior = _prior_from_turns(load_turns(session_id), limit=10)
        if db_prior:
            prior = db_prior

    resolved = _resolve_followup_question(question, prior or [])

    fast = _try_fast_paths(resolved, language_code)
    if fast is not None and (
        fast.get("rows") or fast.get("tool") == "investigation_briefing"
    ):
        for piece in _chunk_text(fast["answer"]):
            yield {"token": piece}
        save_turn(
            session_id,
            question,
            fast["answer"],
            tool=fast.get("tool"),
            query=fast.get("query"),
            username=username,
            rows=fast.get("rows") or [],
        )
        yield {
            "done": True,
            "answer": fast["answer"],
            "tool": fast.get("tool"),
            "query": fast.get("query") or "",
            "rows": fast.get("rows") or [],
            "suggestions": fast.get("suggestions") or [],
            "language_code": fast.get("language_code") or lang,
        }
        return

    if fast is not None and not fast.get("rows"):
        rewritten = _rewrite_question_with_llm(resolved)
        if rewritten != resolved:
            fast2 = _try_fast_paths(rewritten, language_code)
            if fast2 is not None and (
                fast2.get("rows") or fast2.get("tool") == "investigation_briefing"
            ):
                for piece in _chunk_text(fast2["answer"]):
                    yield {"token": piece}
                save_turn(
                    session_id,
                    question,
                    fast2["answer"],
                    tool=fast2.get("tool"),
                    query=fast2.get("query"),
                    username=username,
                    rows=fast2.get("rows") or [],
                )
                yield {
                    "done": True,
                    "answer": fast2["answer"],
                    "tool": fast2.get("tool"),
                    "query": fast2.get("query") or "",
                    "rows": fast2.get("rows") or [],
                    "suggestions": fast2.get("suggestions") or [],
                    "language_code": fast2.get("language_code") or lang,
                }
                return
            resolved = rewritten

    streamed = False
    async for msg, metadata in graph.astream(
        _merge_input(resolved, prior, language_code), config=config, stream_mode="messages"
    ):
        if metadata.get("langgraph_node") in _ANSWER_NODES and msg.content:
            streamed = True
            # Sanitize each token so em-dashes don't become garbled in the UI.
            yield {
                "token": _sanitize_answer(msg.content)
                if len(msg.content) > 20
                else msg.content.replace("\u2014", "-").replace("\u2013", "-")
            }
    snapshot = await graph.aget_state(config)
    values = snapshot.values
    answer = _sanitize_answer(values.get("answer") or "")
    # Deterministic synthesize has no LLM tokens — push the full answer now.
    if answer and not streamed:
        for piece in _chunk_text(answer):
            yield {"token": piece}
    save_turn(
        session_id,
        question,
        answer,
        tool=values.get("tool"),
        query=values.get("query"),
        username=username,
        rows=values.get("rows") or [],
    )
    yield {
        "done": True,
        "answer": answer,
        "tool": values.get("tool"),
        "query": values.get("query"),
        "rows": values.get("rows", []),
        "suggestions": _default_suggestions_for(
            resolved, values.get("rows") or [], lang
        ),
        "language_code": values.get("language_code")
        or resolve_reply_language(question, language_code),
    }


def _voice_spoken_brief(question: str, rows: list, language_code: str) -> str:
    """Very short briefing for TTS (keeps voice latency low)."""
    lang = resolve_reply_language(question, language_code)
    if not rows:
        return empty_records_message(lang)
    n = len(rows)
    bits: list[str] = []
    for row in rows[:2]:
        station = localize_label(row.get("unit_name") or row.get("police_station") or "", lang)
        crime = localize_label(row.get("crime_head_name") or row.get("crime_type") or "", lang)
        person = row.get("person_name") or row.get("accused_name") or ""
        count = row.get("case_count") or row.get("count")
        if count is not None and (station or crime):
            bits.append(f"{station or crime} {count}")
        else:
            bits.append(" ".join(p for p in [station, crime, person] if p))
    detail = "; ".join(b for b in bits if b)
    if lang == "kn-IN":
        head = f"{n} cases ಸಿಕ್ಕಿವೆ."
        return f"{head} {detail}".strip()[:160]
    return f"Found {n} cases. {detail}".strip()[:160]


async def fast_voice_ask(
    session_id: str,
    question: str,
    *,
    username: str | None = None,
    officer_name: str | None = None,
    language_code: str = "en-IN",
) -> dict:
    """Low-latency voice path: briefing/templates first — skip Gemini when possible."""
    from core.history import load_turns, save_turn
    from core.language import (
        didnt_catch_reply,
        is_answerable_voice_question,
        is_usable_voice_transcript,
        no_results_message,
    )
    from langchain_core.messages import AIMessage, HumanMessage

    lang = resolve_reply_language(question, language_code)
    if not is_usable_voice_transcript(question) or not is_answerable_voice_question(question):
        answer = didnt_catch_reply(lang)
        return {
            "answer": answer,
            "speak": answer,
            "tool": "chitchat",
            "query": "",
            "rows": [],
            "suggestions": [],
            "language_code": lang,
        }

    canned = smalltalk_reply(question, lang, officer_name=officer_name)
    if canned:
        answer = canned["answer"]
        save_turn(
            session_id, question, answer, tool="chitchat", query=None, username=username, rows=[]
        )
        speak = answer.split("\n\n")[0] if len(answer) > 160 else answer
        if len(speak) > 140:
            speak = speak[:120].rsplit(" ", 1)[0] + "..."
        return {
            "answer": answer,
            "speak": speak,
            "tool": "chitchat",
            "query": "",
            "rows": [],
            "suggestions": canned.get("suggestions") or [],
            "language_code": lang,
        }

    # Resolve follow-ups from recent voice/chat turns (include row anchors).
    prior = _prior_from_turns(load_turns(session_id), limit=10)
    resolved = _resolve_followup_question(question, prior)
    context = _history_context(prior)

    fast = _try_fast_paths(resolved, language_code)
    if fast is not None:
        tool = fast.get("tool") or ""
        rows = fast.get("rows") or []
        answer = fast.get("answer") or ""
        # Only trust data answers when we actually have rows (or a briefing card).
        if tool in ("sql_agent", "analytics_agent", "graph_agent") and not rows:
            answer = no_results_message(lang)
            speak = answer.split("\n\n")[0]
            save_turn(
                session_id, question, answer, tool=tool, query=fast.get("query"),
                username=username, rows=[],
            )
            return {
                "answer": answer,
                "speak": speak[:140],
                "tool": tool,
                "query": fast.get("query") or "",
                "rows": [],
                "suggestions": fast.get("suggestions") or [],
                "language_code": lang,
            }
        if rows or tool == "investigation_briefing" or (answer and tool == "chitchat"):
            speak = _voice_spoken_brief(resolved, rows, lang) if rows else answer
            if len(speak) > 140:
                speak = speak[:120].rsplit(" ", 1)[0] + "..."
            save_turn(
                session_id,
                question,
                answer,
                tool=tool,
                query=fast.get("query"),
                username=username,
                rows=rows,
            )
            return {
                "answer": answer,
                "speak": speak,
                "tool": tool,
                "query": fast.get("query") or "",
                "rows": rows,
                "suggestions": fast.get("suggestions") or [],
                "language_code": fast.get("language_code") or lang,
            }

    # Contextual SQL for follow-ups that templates miss ("only pending", "more details").
    if prior and (_looks_like_followup(question) or resolved != question):
        try:
            from agents.sql_agent import run_sql_question

            sql_result = run_sql_question(resolved, context)
            rows = sql_result.get("rows") or []
            answer = (
                _format_rows_fallback(resolved, rows, lang)
                if rows
                else no_results_message(lang)
            )
            speak = _voice_spoken_brief(resolved, rows, lang) if rows else answer.split("\n\n")[0]
            if len(speak) > 140:
                speak = speak[:120].rsplit(" ", 1)[0] + "..."
            save_turn(
                session_id,
                question,
                answer,
                tool="sql_agent",
                query=sql_result.get("sql"),
                username=username,
                rows=rows,
            )
            return {
                "answer": answer,
                "speak": speak,
                "tool": "sql_agent",
                "query": sql_result.get("sql") or "",
                "rows": rows,
                "suggestions": _default_suggestions_for(resolved, rows, lang),
                "language_code": lang,
            }
        except Exception:
            pass

    # Last resort — full ask with a hard timeout so voice stays snappy.
    try:
        result = await asyncio.wait_for(
            ask(
                session_id,
                resolved,
                username=username,
                officer_name=officer_name,
                language_code=language_code,
            ),
            timeout=9.0,
        )
    except asyncio.TimeoutError:
        answer = (
            "Taking too long Officer. Try a shorter FIR or place question."
            if lang != "kn-IN"
            else "Time ಜಾಸ್ತಿ ಆಯ್ತು Officer. Short FIR ಅಥವಾ place ಹೇಳಿ."
        )
        return {
            "answer": answer,
            "speak": answer,
            "tool": "chitchat",
            "query": "",
            "rows": [],
            "suggestions": [],
            "language_code": lang,
        }
    answer = result.get("answer") or ""
    rows = result.get("rows") or []
    tool = result.get("tool") or ""
    # Block invented SQL-style answers with no rows.
    if tool in ("sql_agent", "analytics_agent", "graph_agent") and not rows:
        answer = no_results_message(result.get("language_code") or lang)
        speak = answer.split("\n\n")[0][:140]
        return {**result, "answer": answer, "speak": speak, "rows": []}
    speak = _voice_spoken_brief(resolved, rows, result.get("language_code") or lang) if rows else answer
    if len(speak) > 140:
        speak = speak[:120].rsplit(" ", 1)[0] + "..."
    return {**result, "speak": speak}
