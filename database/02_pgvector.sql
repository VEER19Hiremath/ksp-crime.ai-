-- Enables semantic search over CaseMaster.brief_facts (MO similarity, pattern discovery)
-- Neon supports pgvector natively: https://neon.tech/docs/extensions/pgvector

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS case_embedding (
    case_master_id  INT PRIMARY KEY REFERENCES case_master(case_master_id),
    embedding       VECTOR(768),   -- match the embedding model's output dimension
    updated_at      TIMESTAMP NOT NULL DEFAULT now()
);

-- IVFFlat index for approximate nearest-neighbor search once there's enough data
-- (skip until case_embedding has a few thousand rows; on a small hackathon
-- dataset a sequential scan is fine and this index adds overhead for nothing).
-- CREATE INDEX idx_case_embedding_ivfflat ON case_embedding
--   USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
