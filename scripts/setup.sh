#!/usr/bin/env bash
set -euo pipefail

echo "==> Setting up Shelby AI"

# Create venv if missing
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
  echo "  Created .venv"
fi

source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
echo "  Dependencies installed"

# Copy env template if .env doesn't exist
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "  Created .env — add your ANTHROPIC_API_KEY"
fi

echo ""
echo "Done. Next steps:"
echo "  1. Edit .env and set ANTHROPIC_API_KEY"
echo "  2. ./scripts/serve.sh          — start the API server"
echo "  3. ./scripts/demo.sh           — run an interactive demo"
echo "  4. python -m shelby.evals.run  — run LangChain evals"
