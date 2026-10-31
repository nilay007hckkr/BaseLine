#!/usr/bin/env bash
# One command, two dashboards (both serve the built web app; Ctrl-C stops both):
#   demo       http://localhost:8000  Online Boutique, as ingested by `make ingest`
#   workspace  http://localhost:8080  starts empty: connect your own GitHub repository
# The workspace keeps everything under data/workspace/ and its tour runs on the demo's API.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${API_HOST:-127.0.0.1}"
DEMO_PORT="${DEMO_PORT:-8000}"
WORKSPACE_PORT="${WORKSPACE_PORT:-8080}"

for port in "$DEMO_PORT" "$WORKSPACE_PORT"; do
  # time-limited: under WSL mirrored networking a just-freed port can hang instead of refusing
  if timeout 1 bash -c "exec 3<>/dev/tcp/$HOST/$port" 2>/dev/null; then
    echo "port $port is already in use; stop that process or set DEMO_PORT / WORKSPACE_PORT" >&2
    exit 1
  fi
done

echo "building the web app"
(cd "$ROOT/web" && pnpm -s build)

cd "$ROOT/backend"
CORS_ORIGINS="http://localhost:$WORKSPACE_PORT,http://127.0.0.1:$WORKSPACE_PORT" \
  uv run uvicorn hindsight.api.app:app --host "$HOST" --port "$DEMO_PORT" &
HINDSIGHT_MODE=workspace DATA_DIR=./data/workspace DB_PATH=./data/workspace/empty/hindsight.db \
  RESULTS_DIR=./data/workspace/empty/results GRAPH_BACKEND=sqlite DEMO_PORT="$DEMO_PORT" \
  uv run uvicorn hindsight.api.app:app --host "$HOST" --port "$WORKSPACE_PORT" &
trap 'kill $(jobs -p) 2>/dev/null || true' EXIT INT TERM

echo
echo "  demo       http://localhost:$DEMO_PORT"
echo "  workspace  http://localhost:$WORKSPACE_PORT   (connect your own repository)"
echo "  Ctrl-C stops both"
wait
