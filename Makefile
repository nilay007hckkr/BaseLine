# Hindsight. Phase 1 (PS 06, Project Brain) targets; see docs/PLAN.md.
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help
-include .env
export

BE := backend
WEB := web

.PHONY: help
help: ## List targets
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n",$$1,$$2}'

## ---------- environment ----------
.PHONY: bootstrap doctor setup
bootstrap: ## Install / verify toolchain
	@bash scripts/bootstrap.sh
doctor: ## Report toolchain and environment
	@bash scripts/doctor.sh
setup: ## Install backend and web dependencies
	@cd $(BE) && uv sync
	@cd $(WEB) && pnpm install --frozen-lockfile

## ---------- quality ----------
.PHONY: fmt lint test gen-api
fmt: ## Format
	@cd $(BE) && uv run ruff format .
	@cd $(WEB) && pnpm -s format
lint: ## Lint
	@cd $(BE) && uv run ruff check . && uv run ruff format --check .
	@cd $(WEB) && pnpm -s lint
test: ## Unit tests (hermetic: fixture repo, no network, no live LLM)
	@cd $(BE) && LLM_CACHE_MODE=replay uv run pytest -q
	@cd $(WEB) && pnpm -s test
gen-api: ## Regenerate web API types from the backend's OpenAPI schema
	@cd $(BE) && uv run python -c "import json; from hindsight.api.app import app; print(json.dumps(app.openapi(), indent=1))" > ../$(WEB)/openapi.json
	@cd $(WEB) && pnpm -s gen:api && pnpm -s exec prettier --write src/api/schema.d.ts >/dev/null

## ---------- run ----------
.PHONY: ingest embed codegraph stats dev serve run
ingest: ## Ingest history: make ingest [REPO=<github url>] (default TARGET_REPO_URL or Online Boutique)
	@cd $(BE) && uv run hindsight ingest $${REPO:-}
embed: ## Index citable text with the local embedding model (needs Ollama + OLLAMA_EMBED_MODEL)
	@cd $(BE) && uv run hindsight embed
codegraph: ## Functions, classes and calls for the Map (graphify, offline; uv tool install graphifyy==0.9.99)
	@cd $(BE) && uv run hindsight codegraph
stats: ## Print ingest statistics
	@cd $(BE) && uv run hindsight stats
dev: ## API with reload on :8000 + web dev server on :5173
	@bash scripts/dev.sh
run: ## Demo on :8000 + a fresh workspace on :8080 for your own repo (one command, Ctrl-C stops both)
	@bash scripts/run.sh
serve: ## Build the web app and serve everything from the API on :8000
	@cd $(WEB) && pnpm -s build
	@cd $(BE) && uv run uvicorn hindsight.api.app:app --host $${API_HOST:-127.0.0.1} --port $${API_PORT:-8000}

## ---------- SentinelQA (needs the demo shop: make target-up) ----------
.PHONY: qa-generate qa-validate qa-run eval-heal
qa-generate: ## Tickets (target/tickets) → tests, linked to their issue/PR/commit and code
	@cd $(BE) && uv run hindsight qa generate
qa-validate: ## Keep a test only if it passes 3/3 on the clean shop; saves element fingerprints
	@cd $(BE) && uv run hindsight qa validate
qa-run: ## Run the active tests; broken locators are healed or refused, repairs await review
	@cd $(BE) && uv run hindsight qa run
eval-heal: ## Mutation harness (synthetic): heal rate, false heals, refusals → eval/results/
	@cd $(BE) && uv run hs-eval heal

## ---------- graph layer (Neo4j, optional) ----------
.PHONY: graph-up graph-down graph-sync
graph-up: ## Start Neo4j in Docker → browser http://localhost:7474
	@bash scripts/graph.sh up
graph-down: ## Stop Neo4j (keeps its data volume)
	@bash scripts/graph.sh down
graph-sync: ## Copy the SQLite graph into Neo4j (repeat after ingest; uploads re-sync themselves)
	@cd $(BE) && uv run hindsight graph-sync

## ---------- evaluation ----------
.PHONY: eval eval-smoke eval-report eval-retrieval try-repo
eval: ## History-replay evaluation of the impact check → eval/results/
	@cd $(BE) && uv run hs-eval all
eval-smoke: ## The evaluation harness on the fixture repository (fast, hermetic)
	@cd $(BE) && LLM_CACHE_MODE=replay uv run pytest -q -k replay_evaluation
eval-retrieval: ## Can search find the linked issue from a PR title? keyword vs semantic vs hybrid
	@cd $(BE) && uv run hs-eval retrieval
eval-report: ## Rewrite eval/results/latest.md from the result files (runs nothing)
	@cd $(BE) && uv run hs-eval report
try-repo: ## Ingest + evaluate another public repo in data/other/<owner>__<name>: make try-repo REPO=<github url>
	@slug=$$(echo "$${REPO:?set REPO}" | sed -E 's#^https?://github.com/##; s#\.git$$##; s#/$$##; s#/#__#'); \
	cd $(BE) && export DATA_DIR=data/other/$$slug DB_PATH=data/other/$$slug/hindsight.db \
	  FORGE_MAX_REQUESTS=$${FORGE_MAX_REQUESTS:-10}; \
	uv run hindsight ingest "$$REPO" && uv run hs-eval impact

## ---------- target system + LogSense ----------
KUBECONFIG ?= $(HOME)/.kube/config
.PHONY: target-up target-down target-status collect fault fault-revert eval-rca demo-replay
target-up: ## Online Boutique on a local kind cluster → http://localhost:8081
	@bash target/scripts/up.sh
target-down: ## Delete the local cluster
	@bash target/scripts/down.sh
target-status: ## Pods of the local cluster
	@kubectl --context "kind-$${KIND_CLUSTER_NAME:-hs-target}" get pods
collect: ## Stream the shop's logs into LogSense (Ctrl-C to stop)
	@cd $(BE) && uv run hindsight collect
fault: ## Inject a synthetic fault: make fault SCENARIO=payment-down [DEPLOYS=1]
	@cd $(BE) && uv run hindsight fault "$${SCENARIO:?set SCENARIO}" $${DEPLOYS:+--deploys}
fault-revert: ## Revert it: make fault-revert SCENARIO=payment-down
	@cd $(BE) && uv run hindsight fault "$${SCENARIO:?set SCENARIO}" --revert
eval-rca: ## Fault campaign (slow, needs the cluster): make eval-rca [RUNS=10]
	@cd $(BE) && uv run hs-eval rca --runs $${RUNS:-10}
demo-replay: ## Run recorded logs (data/logs/*.jsonl) through LogSense, no cluster needed
	@cd $(BE) && uv run hindsight replay ../data/logs/*.jsonl
