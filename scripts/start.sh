#!/usr/bin/env bash
# Start the whole of Hindsight with one command (make start, or bash scripts/start.sh):
#   1. tools        uv, pnpm and git must be installed (make bootstrap installs them)
#   2. .env         copied from .env.example the first time; add keys later, all are optional
#   3. dependencies uv sync and pnpm install
#   4. data         ingest Online Boutique's history if this machine has not done it yet
#   5. Neo4j        started when Docker is running; otherwise the graph is read from SQLite
#   6. code layer   functions and calls for the Map, when graphify is installed and the layer is stale
#   7. live shop    streams the local cluster's logs into LogSense when the cluster is running;
#                   --live also creates and starts the cluster (slow the first time)
#   8. dashboards   demo on :8000 and workspace on :8080 (--dev: API with reload + Vite on :5173)
# Optional parts that are missing are skipped with a note, never fatal. Ctrl-C stops what this
# script runs in the foreground and the log stream; Neo4j and the cluster keep running
# (make graph-down / make target-down stop them).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# kind writes ~/.kube/config; some kubectl builds (e.g. k3s) default to another file
export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config}"

usage() {
  cat <<'TXT'
usage: scripts/start.sh [--live] [--dev] [--no-neo4j] [--skip-setup]
  --live        also create/start the local Online Boutique cluster (Docker, kind, kubectl)
  --dev         API with reload + Vite dev server instead of the built dashboards
  --no-neo4j    do not start Neo4j; read the graph from SQLite
  --skip-setup  skip uv sync / pnpm install (faster restarts)
The same switches as environment variables, for make: LIVE=1 DEV=1 NO_NEO4J=1 SKIP_SETUP=1
TXT
}

LIVE="${LIVE:-}" DEV="${DEV:-}" NO_NEO4J="${NO_NEO4J:-}" SKIP_SETUP="${SKIP_SETUP:-}"
for arg in "$@"; do
  case "$arg" in
    --live) LIVE=1 ;;
    --dev) DEV=1 ;;
    --no-neo4j) NO_NEO4J=1 ;;
    --skip-setup) SKIP_SETUP=1 ;;
    -h | --help) usage; exit 0 ;;
    *) echo "unknown option: $arg" >&2; usage >&2; exit 2 ;;
  esac
done

step() { printf '\n==> [%s/8] %s\n' "$1" "$2"; }
note() { printf '    %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }
SUMMARY=()
record() { SUMMARY+=("$(printf '%-12s %s' "$1" "$2")"); }

# A setting from the environment, else from .env (the backend reads .env itself; the shell
# only needs a few names, so .env is never sourced as a script).
envval() {
  local v="${!1:-}"
  if [[ -z "$v" && -f .env ]]; then
    v="$(grep -E "^$1=" .env | tail -n 1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//')"
  fi
  printf '%s' "${v:-${2:-}}"
}

# A one-line check against the backend's own settings and database (exit 0 = yes).
backend_check() { (cd backend && uv run -q python -c "$1"); }

# ---------------------------------------------------------------------------------------------
step 1 "tools"
case "$ROOT" in
  /mnt/*) echo "Hindsight is under $ROOT: move it into the Linux filesystem (e.g. ~/code/hindsight)" >&2; exit 1 ;;
esac
missing=()
for tool in uv pnpm git; do have "$tool" || missing+=("$tool"); done
if ((${#missing[@]})); then
  echo "missing: ${missing[*]}. Run make bootstrap first." >&2
  exit 1
fi
note "uv, pnpm and git found"

step 2 ".env"
if [[ -f .env ]]; then
  note ".env found"
else
  cp .env.example .env
  note "created .env from .env.example (API keys are optional; edit it any time)"
fi

step 3 "dependencies"
if [[ -n "$SKIP_SETUP" ]]; then
  note "skipped (--skip-setup)"
else
  (cd backend && uv sync -q)
  (cd web && pnpm install --frozen-lockfile --silent)
  note "backend and web dependencies are up to date"
fi

step 4 "data"
if backend_check '
import sys
from hindsight.core.config import load_settings
from hindsight.core.db import connect, get_meta
sys.exit(0 if get_meta(connect(load_settings().db_path), "repo") else 1)'; then
  note "history already ingested"
else
  note "first run: ingesting the target repository's history (a few minutes)"
  (cd backend && uv run hindsight ingest)
fi
record "data" "ingested ($(envval TARGET_REPO_URL 'Online Boutique'))"

step 5 "Neo4j"
NEO4J=""
if [[ -n "$NO_NEO4J" || "$(envval GRAPH_BACKEND auto)" == "sqlite" ]]; then
  note "skipped; the graph is read from SQLite"
elif have docker && docker info >/dev/null 2>&1; then
  if bash scripts/graph.sh up >/dev/null; then # errors still reach the terminal
    NEO4J=1
    note "Neo4j is up: http://localhost:7474"
  else
    note "Neo4j did not start; using SQLite"
  fi
else
  note "skipped: Docker is not running; the graph is read from SQLite"
fi

step 6 "code structure layer"
if ! have graphify; then
  note "skipped: graphify is not installed (uv tool install graphifyy==0.9.99)"
  record "code layer" "off (graphify not installed)"
elif backend_check '
import sys
from hindsight.core.config import load_settings
from hindsight.core.db import connect, get_meta
conn = connect(load_settings().db_path)
layer, repo = get_meta(conn, "codegraph") or {}, get_meta(conn, "repo") or {}
sys.exit(0 if layer.get("head") and layer.get("head") == repo.get("head_sha") else 1)'; then
  note "up to date with the ingested HEAD"
  record "code layer" "on"
else
  if (cd backend && uv run hindsight codegraph); then
    record "code layer" "on (rebuilt)"
  else
    note "the code layer could not be built; the Map works without it"
    record "code layer" "off (build failed)"
  fi
fi

if [[ -n "$NEO4J" ]]; then
  if backend_check '
import sys
from hindsight.api.service import graph_status
from hindsight.core.config import load_settings
from hindsight.core.db import connect
s = load_settings()
sys.exit(0 if graph_status(connect(s.db_path), s).in_sync else 1)'; then
    note "Neo4j is in sync with SQLite"
  else
    note "copying the graph into Neo4j"
    (cd backend && uv run hindsight graph-sync >/dev/null) || note "sync failed; reads fall back to SQLite"
  fi
  record "graph" "Neo4j (browser http://localhost:7474)"
else
  record "graph" "SQLite"
fi

step 7 "live shop and log stream"
CLUSTER="$(envval KIND_CLUSTER_NAME hs-target)"
running() { have kubectl && kubectl --context "kind-$CLUSTER" get deploy frontend >/dev/null 2>&1; }
if [[ -n "$LIVE" ]]; then
  if have docker && have kind && have kubectl && docker info >/dev/null 2>&1; then
    bash target/scripts/up.sh || note "the cluster did not come up; continuing without it"
  else
    note "--live needs Docker (running), kind and kubectl; run make bootstrap"
  fi
fi
COLLECTOR=""
if running; then
  mkdir -p data/logs
  (cd backend && PYTHONUNBUFFERED=1 exec uv run hindsight collect) >data/logs/collector.out 2>&1 &
  COLLECTOR=$!
  trap 'kill "$COLLECTOR" 2>/dev/null || true' EXIT
  note "shop http://localhost:8081; its logs stream into LogSense (output: data/logs/collector.out)"
  record "live shop" "on: http://localhost:8081, logs streaming into Incidents"
else
  note "not running: bash scripts/start.sh --live starts it (or use make demo-replay)"
  record "live shop" "off"
fi

if have curl && curl -fsS -m 2 "$(envval OLLAMA_HOST http://localhost:11434)/api/version" >/dev/null 2>&1; then
  record "local model" "Ollama is up"
else
  record "local model" "Ollama not reachable: answers use cloud keys from .env, or quoted evidence (docs/OLLAMA_SETUP.md)"
fi

step 8 "dashboards"
printf '\n'
for line in "${SUMMARY[@]}"; do note "$line"; done
if [[ -n "$DEV" ]]; then
  bash scripts/dev.sh
else
  bash scripts/run.sh
fi
