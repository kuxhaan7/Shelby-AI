# ── Stage 1: build ───────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc g++ libffi-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.11-slim

# ffmpeg needed to convert ElevenLabs MP3 → OGG Opus for Telegram voice messages
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

WORKDIR /app
COPY . .

# FastAPI serves the web UI AND launches the Telegram bot as a background
# task inside the same event loop (see shelby/api/main.py lifespan). So the
# right entrypoint on both Railway and Cloud Run is uvicorn, not the bot
# module. PORT is set by Cloud Run and Railway; default to 8000 for local.
CMD ["sh", "-c", "uvicorn shelby.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
