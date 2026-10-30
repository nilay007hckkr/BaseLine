#!/usr/bin/env bash
# API with reload on API_HOST:API_PORT, plus the Vite dev server (which proxies /api to it).
# Ctrl-C stops both.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${API_HOST:-127.0.0.1}"
PORT="${API_PORT:-8000}"

(cd "$ROOT/backend" && exec uv run uvicorn hindsight.api.app:app --reload --host "$HOST" --port "$PORT") &
API_PID=$!
trap 'kill "$API_PID" 2>/dev/null || true' EXIT

echo "API: http://$HOST:$PORT/docs   Dashboard: http://localhost:5173"
cd "$ROOT/web" && pnpm dev
