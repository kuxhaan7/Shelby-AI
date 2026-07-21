#!/usr/bin/env bash
set -euo pipefail

source .venv/bin/activate 2>/dev/null || true

export $(grep -v '^#' .env | xargs) 2>/dev/null || true

echo "==> Starting Shelby API on http://localhost:8000"
echo "    Docs: http://localhost:8000/docs"
echo ""

uvicorn shelby.api.main:app --host 0.0.0.0 --port 8000 --reload
