#!/usr/bin/env bash
#
# Start the FastAPI backend from the project virtual environment.
#
# Invoked by `npm run dev` (via the `dev:api` script). Host and port come from
# backend/app/config.py so they are not duplicated here.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="$ROOT_DIR/backend/.venv/bin/python"

if [ ! -x "$VENV_PYTHON" ]; then
  echo "Backend virtual environment not found at backend/.venv" >&2
  echo "Create it with:" >&2
  echo "  python3 -m venv backend/.venv" >&2
  echo "  backend/.venv/bin/python -m pip install --upgrade pip" >&2
  echo "  backend/.venv/bin/python -m pip install -r backend/requirements.txt" >&2
  exit 1
fi

cd "$ROOT_DIR/backend"
exec "$VENV_PYTHON" -m app.main