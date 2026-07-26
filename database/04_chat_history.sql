-- Conversation history for Crime AI chat/voice sessions.
-- Run once: psql "$DATABASE_URL" -f database/04_chat_history.sql

CREATE TABLE IF NOT EXISTS chat_history (
    id           BIGSERIAL PRIMARY KEY,
    session_id   VARCHAR(120) NOT NULL,
    username     VARCHAR(100),
    question     TEXT NOT NULL,
    answer       TEXT NOT NULL,
    tool         VARCHAR(80),
    query_text   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_history_session
    ON chat_history (session_id, created_at);
