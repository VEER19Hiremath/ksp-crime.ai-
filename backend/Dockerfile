# WebSocket-capable backend for Crime AI (Render / any container host).
# Unlike Catalyst AppSail, this serves /voice/realtime WebSocket streaming.
FROM python:3.13-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

# Slim deps (no pandas / livekit-agents); includes websockets + sarvamai + livekit-api.
COPY requirements-deploy.txt .
RUN pip install --no-cache-dir -r requirements-deploy.txt

COPY . .

ENV PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    ENABLE_CORS=1

EXPOSE 8000

# Render provides $PORT. --ws websockets enables the /voice/realtime upgrade.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --ws websockets --proxy-headers --forwarded-allow-ips=*"]
