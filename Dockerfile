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

# Copy only the compiled packages — no build tools in final image
COPY --from=builder /install /usr/local

WORKDIR /app
COPY . .

CMD ["python", "-m", "shelby.telegram.bot"]
