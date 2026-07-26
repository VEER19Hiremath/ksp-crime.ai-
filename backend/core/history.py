"""Persist and reload investigator chat/voice turns in Neon Postgres."""
from __future__ import annotations

import json
import logging
from typing import Any

from core.db import get_pool

logger = logging.getLogger(__name__)


def ensure_history_table() -> None:
    """Create chat_history if missing (safe to call on every boot)."""
    try:
        with get_pool().connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_history (
                    id           BIGSERIAL PRIMARY KEY,
                    session_id   VARCHAR(120) NOT NULL,
                    username     VARCHAR(100),
                    question     TEXT NOT NULL,
                    answer       TEXT NOT NULL,
                    tool         VARCHAR(80),
                    query_text   TEXT,
                    rows_json    JSONB,
                    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            # Older DBs created before rows_json — add column if missing.
            conn.execute(
                """
                ALTER TABLE chat_history
                ADD COLUMN IF NOT EXISTS rows_json JSONB
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chat_history_session
                    ON chat_history (session_id, created_at)
                """
            )
            conn.commit()
    except Exception:
        logger.exception("Could not ensure chat_history table")


def _serialize_rows(rows: list | None) -> str | None:
    if not rows:
        return None
    try:
        # Cap size so a huge analytics dump does not blow the row.
        return json.dumps(rows[:40], default=str)
    except Exception:
        return None


def _parse_rows(raw: Any) -> list:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def save_turn(
    session_id: str,
    question: str,
    answer: str,
    *,
    tool: str | None = None,
    query: str | None = None,
    username: str | None = None,
    rows: list | None = None,
) -> None:
    last_err: Exception | None = None
    rows_payload = _serialize_rows(rows)
    for attempt in range(2):
        try:
            with get_pool().connection() as conn:
                conn.execute(
                    """
                    INSERT INTO chat_history
                        (session_id, username, question, answer, tool, query_text, rows_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (session_id, username, question, answer, tool, query, rows_payload),
                )
                conn.commit()
            logger.info(
                "Saved chat turn session=%s user=%s tool=%s rows=%s",
                session_id,
                username,
                tool,
                len(rows or []),
            )
            return
        except Exception as exc:
            last_err = exc
            logger.warning("save_turn attempt %s failed: %s", attempt + 1, exc)
            from core.db import reset_pool

            reset_pool()
            ensure_history_table()
    logger.exception("Failed to save chat turn for session %s: %s", session_id, last_err)


def load_turns(session_id: str, limit: int = 100) -> list[dict]:
    try:
        with get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT question, answer, tool, query_text AS query, rows_json, created_at
                    FROM chat_history
                    WHERE session_id = %s
                    ORDER BY created_at ASC
                    LIMIT %s
                    """,
                    (session_id, limit),
                )
                rows = cur.fetchall()
                # dict_row factory may already be set on the pool
                if rows and isinstance(rows[0], dict):
                    return [
                        {
                            "question": r["question"],
                            "answer": r["answer"],
                            "tool": r.get("tool"),
                            "query": r.get("query"),
                            "rows": _parse_rows(r.get("rows_json")),
                        }
                        for r in rows
                    ]
                return [
                    {
                        "question": r[0],
                        "answer": r[1],
                        "tool": r[2],
                        "query": r[3],
                        "rows": _parse_rows(r[4]),
                    }
                    for r in rows
                ]
    except Exception:
        logger.exception("Failed to load chat history for session %s", session_id)
        return []


def list_sessions(*, username: str | None = None, limit: int = 40) -> list[dict]:
    """Recent chat sessions with a title from the first question."""
    try:
        with get_pool().connection() as conn:
            with conn.cursor() as cur:
                if username:
                    cur.execute(
                        """
                        WITH sessions AS (
                          SELECT session_id,
                                 MIN(created_at) AS started_at,
                                 MAX(created_at) AS updated_at,
                                 COUNT(*)::int AS turn_count
                          FROM chat_history
                          WHERE username = %s
                             OR session_id ILIKE %s
                          GROUP BY session_id
                          ORDER BY MAX(created_at) DESC
                          LIMIT %s
                        )
                        SELECT s.session_id, s.started_at, s.updated_at, s.turn_count,
                               COALESCE(
                                 (SELECT question FROM chat_history h
                                  WHERE h.session_id = s.session_id
                                  ORDER BY h.created_at ASC LIMIT 1),
                                 'Untitled chat'
                               ) AS title
                        FROM sessions s
                        ORDER BY s.updated_at DESC
                        """,
                        (username, f"sess-{username.lower()}%", limit),
                    )
                else:
                    cur.execute(
                        """
                        WITH sessions AS (
                          SELECT session_id,
                                 MIN(created_at) AS started_at,
                                 MAX(created_at) AS updated_at,
                                 COUNT(*)::int AS turn_count
                          FROM chat_history
                          GROUP BY session_id
                          ORDER BY MAX(created_at) DESC
                          LIMIT %s
                        )
                        SELECT s.session_id, s.started_at, s.updated_at, s.turn_count,
                               COALESCE(
                                 (SELECT question FROM chat_history h
                                  WHERE h.session_id = s.session_id
                                  ORDER BY h.created_at ASC LIMIT 1),
                                 'Untitled chat'
                               ) AS title
                        FROM sessions s
                        ORDER BY s.updated_at DESC
                        """,
                        (limit,),
                    )
                rows = cur.fetchall()
                out: list[dict] = []
                for r in rows:
                    if isinstance(r, dict):
                        out.append(
                            {
                                "session_id": r["session_id"],
                                "title": (r.get("title") or "Untitled chat")[:80],
                                "turn_count": r.get("turn_count") or 0,
                                "updated_at": str(r.get("updated_at") or ""),
                                "started_at": str(r.get("started_at") or ""),
                            }
                        )
                    else:
                        out.append(
                            {
                                "session_id": r[0],
                                "title": (r[4] or "Untitled chat")[:80],
                                "turn_count": r[3] or 0,
                                "updated_at": str(r[2] or ""),
                                "started_at": str(r[1] or ""),
                            }
                        )
                return out
    except Exception:
        logger.exception("Failed to list chat sessions")
        return []
