# Crime AI — KSP Hackathon

Conversational AI for Karnataka State Crime Records Bureau (SCRB).
Officers can **chat or speak** in English / Kannada (code-mixed), run analytics
against Neon Postgres + Neo4j, and export PDF reports by role.

Full architecture: [docs/END_TO_END_PLAN.md](docs/END_TO_END_PLAN.md) · Deploy: [docs/DEPLOY.md](docs/DEPLOY.md)

| Layer | Tech |
|---|---|
| API | FastAPI + LangGraph (Gemini 2.5 Flash) |
| Data | Neon PostgreSQL · Neo4j Aura |
| Voice | WebSocket `/voice/realtime` · Sarvam STT/TTS · ElevenLabs (EN) |
| UI | Next.js (static export) + Tailwind |
| Hosting | **Frontend → Zoho Catalyst** · **Backend + live voice → Render** |

> Zoho Catalyst AppSail does **not** support WebSockets. Live voice therefore
> runs on Render (or any WS-capable host). The Catalyst frontend points at that
> API URL via `NEXT_PUBLIC_API_BASE_URL`.
>
> **Keep-alive:** the UI pings `/health` every 5 min while open; see
> [`scripts/keepalive.py`](scripts/keepalive.py) and
> [`.github/workflows/keepalive.yml`](.github/workflows/keepalive.yml) to keep
> Render awake when nobody has the app open.

## Demo logins

| Username | Password | Role | PDF export? |
|---|---|---|---|
| investigator | investigator123 | Investigator | No |
| sho | sho123456 | SHO | Yes |
| dsp | dsp1234567 | DSP | Yes |
| analyst | analyst12345 | Analyst | Yes |
| admin | admin1234567 | Administrator | Yes |

## Prerequisites
- Python 3.11+ (tested 3.13), Node 20+ (tested 22)
- Keys: Neon, Neo4j Aura, Gemini, Sarvam, ElevenLabs (optional LiveKit)

## Local quick start
```bash
./.start.sh   # from crime-ai/, Git Bash — backend :8000 + frontend :3000
```
Open http://localhost:3000/login.

### Backend
```bash
cd backend
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# Git Bash:          source .venv/Scripts/activate
pip install -r requirements.txt
cp .env.example .env          # fill DATABASE_URL, NEO4J_*, GEMINI_*, SARVAM_*, …
uvicorn main:app --reload --port 8000 --ws websockets
```
Health: http://localhost:8000/health · Docs: http://localhost:8000/docs

### Frontend
```bash
cd frontend
npm install
cp .env.local.example .env.local   # NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
npm run dev                         # http://localhost:3000
```

**Windows note:** `npm run dev` uses `next dev --webpack` (Turbopack can fail on
some Windows / network-drive setups).

### Voice (local)
The call button opens a **WebSocket** to `/voice/realtime` — mic PCM streams up,
STT → LangGraph → TTS streams back. This is the same path used in production on
Render. One-shot `/voice/ask` remains for debugging.

Optional LiveKit worker (`python -m voice_agent.worker dev`) is experimental and
not required for the Catalyst + Render deployment.

## Production (end-to-end)

```
Browser ──► Catalyst Web Client (/app) ──HTTPS/WSS──► Render Docker (FastAPI)
                                                          │
                                                          ├── Neon Postgres
                                                          ├── Neo4j Aura
                                                          ├── Sarvam (STT/TTS)
                                                          └── Gemini
```

1. **Backend on Render** — Blueprint uses [`render.yaml`](render.yaml).
   New → Blueprint → this repo → fill secret env vars when prompted.
2. **Frontend on Catalyst** — static export with `basePath=/app`, rebuilt with:
   ```bash
   $env:CATALYST_HOSTING="1"
   $env:NEXT_PUBLIC_API_BASE_URL="https://<your-render-service>.onrender.com"
   $env:NEXT_PUBLIC_BASE_PATH="/app"
   cd frontend; npm run build
   # copy frontend/out → client/ then: catalyst deploy --only client
   ```
3. Details, env checklist, and CORS notes: **[docs/DEPLOY.md](docs/DEPLOY.md)**

## Database / Neo4j (once)
```bash
psql "$DATABASE_URL" -f database/schema.sql
psql "$DATABASE_URL" -f database/02_pgvector.sql
psql "$DATABASE_URL" -f database/seed.sql
psql "$DATABASE_URL" -f database/03_auth.sql
python database/generate_test_data.py
python database/seed_users.py

cypher-shell -a "$NEO4J_URI" -u "$NEO4J_USER" -p "$NEO4J_PASSWORD" -f neo4j/schema.cypher
python neo4j/load_from_postgres.py
```

## Project layout
```
crime-ai/
  backend/          FastAPI + LangGraph + /voice/realtime
  frontend/         Next.js app (static export for Catalyst)
  database/         SQL schema, seeds, user bootstrap
  neo4j/            Cypher schema + Postgres → Neo4j loader
  client/           Catalyst static hosting build output (gitignored)
  docs/             Architecture + deploy guides
  render.yaml       Render Blueprint for the API
```

## Security
Never commit `.env`, `backend/appsail-backend/app-config.json`, or filled
`render.yaml` secrets. Blueprint uses `sync: false` so keys stay in the Render
dashboard only.
