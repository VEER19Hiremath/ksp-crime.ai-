"""Neon Postgres access via a connection pool. Opening a fresh TCP+TLS+auth
connection to a remote Postgres on every request measured ~3s by itself
(Neon is a network hop away) — pooling reuses warm connections instead.

Neon closes idle SSL connections; without a pool health-check those show up as
`SSL connection has been closed unexpectedly` / BAD connections and make the
SQL agent look like it "couldn't form a query". We check connections on
checkout and retry once after discarding a dead connection.
"""
from contextlib import contextmanager
import logging
import time

from psycopg import OperationalError
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from core.config import get_settings

logger = logging.getLogger(__name__)

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        settings = get_settings()
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL is not set — copy .env.example to .env")
        _pool = ConnectionPool(
            conninfo=settings.database_url,
            min_size=1,
            max_size=8,
            # Drop idle Neon connections before the server does (typical ~5 min).
            max_idle=180,
            # Verify the socket is alive when taking a connection from the pool.
            check=ConnectionPool.check_connection,
            kwargs={
                "row_factory": dict_row,
                # TCP keepalives help detect dead Neon links sooner.
                "keepalives": 1,
                "keepalives_idle": 30,
                "keepalives_interval": 10,
                "keepalives_count": 3,
            },
        )
    return _pool


def reset_pool() -> None:
    """Close and recreate the pool after a hard connection failure."""
    global _pool
    if _pool is not None:
        try:
            _pool.close()
        except Exception:
            logger.exception("Error closing Postgres pool")
        _pool = None


@contextmanager
def read_only_cursor():
    pool = get_pool()
    with pool.connection() as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            with conn.cursor() as cur:
                yield cur


def run_read_only_query(sql: str, params: dict | None = None) -> list[dict]:
    """Run a read-only query; retry a few times if Neon dropped the pooled connection."""
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            with read_only_cursor() as cur:
                # LLM-generated SQL embeds its own literals (including ILIKE '%...%' patterns),
                # so pass params only when the caller actually supplies bind values — otherwise
                # psycopg tries to parse every literal "%" in the query as a placeholder.
                if params:
                    cur.execute(sql, params)
                else:
                    cur.execute(sql)
                return cur.fetchall()
        except OperationalError as exc:
            last_err = exc
            logger.warning("Postgres operational error (attempt %s): %s", attempt + 1, exc)
            # Force a fresh connection on the next try.
            reset_pool()
            time.sleep(0.4 * (attempt + 1))
    assert last_err is not None
    raise last_err
