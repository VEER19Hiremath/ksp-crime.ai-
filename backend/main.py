from contextlib import asynccontextmanager
import asyncio
import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import auth, chat, dashboard, graph, reports, voice

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # AppSail kills instances that don't bind the listen port within ~10s.
    # Run DB bootstrap in the background so uvicorn can accept traffic immediately.
    async def _boot() -> None:
        try:
            from core.history import ensure_history_table

            await asyncio.to_thread(ensure_history_table)
        except Exception:
            logger.exception("history table bootstrap failed (will retry on first write)")

    boot_task = asyncio.create_task(_boot())
    yield
    boot_task.cancel()


app = FastAPI(title="Crime AI — KSP Investigator Assistant", lifespan=lifespan)

# CORS control:
#   - Catalyst AppSail injects its own Access-Control-Allow-Origin, so we must NOT
#     add a second one there (set ENABLE_CORS=0 in app-config.json).
#   - Everywhere else (local dev, Render, any WS-capable host) we add it so the
#     Catalyst-hosted frontend can call this API cross-origin.
_enable_cors = os.getenv("ENABLE_CORS")
if _enable_cors is None:
    _enable_cors = "0" if os.getenv("APP_ENV", "development").lower() == "production" else "1"
if _enable_cors == "1":
    _origins = os.getenv("ALLOWED_ORIGINS", "*")
    origin_list = [o.strip() for o in _origins.split(",") if o.strip()] or ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origin_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(voice.router)
app.include_router(dashboard.router)
app.include_router(graph.router)
app.include_router(reports.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
