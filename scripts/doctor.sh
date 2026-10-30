#!/usr/bin/env bash
# Environment report. Never prints secret values.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ -f "$ROOT/.env" ]] && { set -a; source "$ROOT/.env"; set +a; }
export PATH="$(go env GOPATH 2>/dev/null)/bin:$HOME/.local/bin:$PATH"
row(){ printf '  %-20s %s\n' "$1" "$2"; }
v(){ command -v "$1" >/dev/null 2>&1 && { "$@" 2>&1 | head -n1; } || echo MISSING; }
ver_ge(){ [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" = "$2" ]; }

echo "== location"
row repo "$ROOT"; case "$ROOT" in /mnt/*) row warning "under /mnt: move to the Linux filesystem";; esac
echo "== toolchain (minimums: python 3.12, node 20, go 1.22)"
row git "$(v git --version)"; row make "$(v make --version)"; row jq "$(v jq --version)"
row uv "$(v uv --version)"; row python "$(v python3 --version)"
if command -v node >/dev/null; then n=$(node -v|tr -d v); ver_ge "$n" 20.0.0 && row node "$n" || row node "$n  (BELOW MINIMUM)"; else row node MISSING; fi
row pnpm "$(v pnpm --version)"
if command -v go >/dev/null; then g=$(go env GOVERSION|tr -d go); ver_ge "$g" 1.22 && row go "$g" || row go "$g  (BELOW MINIMUM)"; else row go MISSING; fi
row docker "$(docker info >/dev/null 2>&1 && docker version --format '{{.Server.Version}}' || echo 'not reachable')"
row kubectl "$(command -v kubectl >/dev/null && kubectl version --client 2>/dev/null | head -n1 || echo MISSING)"
row kind "$(v kind version)"; row skaffold "$(v skaffold version)"
echo "== gpu / local llm"
row gpu "$(command -v nvidia-smi >/dev/null && nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || echo none)"
row ollama "$(curl -fsS "${OLLAMA_HOST:-http://localhost:11434}/api/version" 2>/dev/null || echo 'not responding')"
echo "== cluster"
row "kind clusters" "$(command -v kind >/dev/null && kind get clusters 2>/dev/null | paste -sd, - || echo -)"
echo "== config (presence only)"
for k in GEMINI_API_KEY GROQ_API_KEY GITHUB_TOKEN ALERT_WEBHOOK_URL TARGET_REPO_URL; do [[ -n "${!k:-}" ]] && row "$k" set || row "$k" unset; done
row LLM_PROVIDER_ORDER "${LLM_PROVIDER_ORDER:-unset}"; row LLM_CACHE_MODE "${LLM_CACHE_MODE:-unset}"
