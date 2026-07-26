#!/usr/bin/env bash
# Starts the Crime AI backend (FastAPI) and frontend (Next.js) dev servers in
# the background, logging to backend/uvicorn.log and frontend/nextdev.log.
# Usage: ./.start.sh   (from crime-ai/, Git Bash / MSYS)
set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"

is_listening() {
  netstat -ano | grep -q ":$1 .*LISTENING"
}

if is_listening "$BACKEND_PORT"; then
  echo "Port $BACKEND_PORT is already in use — backend may already be running. Skipping backend start."
else
  echo "Starting backend on http://localhost:$BACKEND_PORT ..."
  (
    cd "$ROOT/backend"
    if [ ! -d .venv ]; then
      echo "backend/.venv not found — create it first: python -m venv .venv && pip install -r requirements.txt" >&2
      exit 1
    fi
    if [ ! -f .env ]; then
      echo "backend/.env not found — copy backend/.env.example and fill in credentials first." >&2
      exit 1
    fi
    source .venv/Scripts/activate
    nohup uvicorn main:app --reload --port "$BACKEND_PORT" > uvicorn.log 2>&1 &
    echo $! > .backend.pid
  )
fi

if is_listening "$FRONTEND_PORT"; then
  echo "Port $FRONTEND_PORT is already in use — frontend may already be running. Skipping frontend start."
else
  echo "Starting frontend on http://localhost:$FRONTEND_PORT ..."
  (
    cd "$ROOT/frontend"
    if [ ! -d node_modules ]; then
      echo "frontend/node_modules not found — run 'npm install' in frontend/ first." >&2
      exit 1
    fi
    if [ ! -f .env.local ]; then
      cp .env.local.example .env.local
    fi
    nohup npm run dev -- --port "$FRONTEND_PORT" > nextdev.log 2>&1 &
    echo $! > .frontend.pid
  )
fi

sleep 3
echo ""
echo "Backend:  http://localhost:$BACKEND_PORT  (docs: /docs)   logs: backend/uvicorn.log"
echo "Frontend: http://localhost:$FRONTEND_PORT                  logs: frontend/nextdev.log"
echo ""
echo "Stop with:"
echo "  taskkill //PID \$(cat backend/.backend.pid) //F //T"
echo "  taskkill //PID \$(cat frontend/.frontend.pid) //F //T"
