# End-to-end deployment

## Why two hosts?

| Piece | Host | Why |
|---|---|---|
| Static Next.js UI | **Zoho Catalyst** Web Client (`/app`) | Already set up; good for static export |
| FastAPI + **live voice WebSocket** | **Render** (Docker) | Catalyst AppSail does **not** support WebSocket upgrades; Render does |

The browser talks to Render for REST + `/voice/realtime` (WSS). CORS on Render is
enabled (`ENABLE_CORS=1`) and restricted via `ALLOWED_ORIGINS` to the Catalyst
origin.

## 1. Backend → Render

1. Push this repo to GitHub (private recommended).
2. [Render Dashboard](https://dashboard.render.com) → **New → Blueprint**.
3. Select the repo. Render reads [`render.yaml`](../render.yaml).
4. When prompted, set secrets (do **not** put these in git):

| Variable | Notes |
|---|---|
| `ALLOWED_ORIGINS` | `https://<your-catalyst-app>.development.catalystserverless.com` |
| `JWT_SECRET` | Long random string |
| `DATABASE_URL` | Neon connection string (`sslmode=require`) |
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` | Aura credentials |
| `GEMINI_API_KEY` | Google AI Studio |
| `SARVAM_API_KEY` | Sarvam AI |
| `ELEVENLABS_API_KEY` | Optional but used for English TTS |

Non-secret defaults (STT/TTS models, speaker) are already in the Blueprint.

5. Wait for the deploy. Health check: `https://<service>.onrender.com/health`
6. Free tier **spins down after ~15 min idle** — first request after sleep can take
   ~30–60s. Use a paid plan for always-warm demos.

Docker entrypoint (see `backend/Dockerfile`):
```text
uvicorn main:app --host 0.0.0.0 --port $PORT --ws websockets --proxy-headers
```

## 2. Frontend → Catalyst

Build with the **Render** API URL baked in (Next.js public env is compile-time):

```powershell
$api = "https://<your-render-service>.onrender.com"
$env:CATALYST_HOSTING = "1"
$env:NEXT_PUBLIC_API_BASE_URL = $api
$env:NEXT_PUBLIC_BASE_PATH = "/app"

Set-Location frontend
npm run build
Set-Location ..

# Refresh Catalyst client package from the static export
Get-ChildItem client -Force |
  Where-Object { $_.Name -ne "client-package.json" } |
  Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
robocopy frontend\out client /E /NFL /NDL /NJH /NJS /NC /NS /NP | Out-Null

$pkg = '{"name":"crime-ai","version":"0.0.6","homepage":"index.html"}'
[System.IO.File]::WriteAllText(
  "$PWD\client\client-package.json",
  $pkg,
  (New-Object System.Text.UTF8Encoding $false)
)

catalyst deploy --only client -ni
```

Live UI: `https://<project>.development.catalystserverless.com/app/`

`next.config.ts` sets `output: "export"`, `basePath` / `assetPrefix` to `/app` when
`CATALYST_HOSTING=1`, so `_next/static` assets resolve under `/app`.

## 3. Verify end-to-end

1. Open the Catalyst `/app/` URL → log in.
2. Chat a text turn — should hit Render REST.
3. Start a **voice call** — browser opens `wss://<render>/voice/realtime?...`.
4. Speak; you should get streaming TTS back (same path as local).

If the call fails with a network / WS error, confirm:
- Render service is awake (`/health`)
- `ALLOWED_ORIGINS` matches the Catalyst origin exactly (scheme + host, no path)
- Frontend was rebuilt **after** changing `NEXT_PUBLIC_API_BASE_URL`

## 4. Optional: Catalyst AppSail (REST only)

`catalyst.json` can still point at `backend/appsail-backend` for a REST-only API
on Catalyst. **Do not rely on AppSail for live voice** — WS upgrades return 404.
Prefer Render for the single production API.

Keep secrets out of git: `backend/appsail-backend/app-config.json` is gitignored.
Copy from your local secrets or Catalyst console env when deploying AppSail.

## 5. Local tunnel (dev only)

For temporary testing without Render, you can expose local `:8000` with
Cloudflare Tunnel / ngrok and point `NEXT_PUBLIC_API_BASE_URL` at that URL.
That requires your PC to stay online — not an end-to-end deployment.
