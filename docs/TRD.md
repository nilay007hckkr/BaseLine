# Hindsight: TRD (36-hour edition)

_Updated 2026-10-31. §1–§9 describe the system as built. §0.x keep the decision history, block by
block. §10 lists where the code and the other docs (DESIGN, EVALUATION) still differ._

## 0. Phase 1 (PS 06) technical scope

One FastAPI process plus the web dashboard. Phase 1 differed from the original plan as follows:

| Concern | Phase 1 choice | Why |
|---|---|---|
| Git mining | Plain `git log -M --numstat` and `git blame` through `subprocess`, instead of PyDriller | Fewer dependencies; rename lineage is kept |
| Forge data | GitHub REST, read-only. Unauthenticated by default (60 requests/hour), at most `FORGE_MAX_REQUESTS` per run (default 50), pages cached under `data/forge/`; the next run resumes. `GITHUB_TOKEN` is optional | Maintainer: little time spent on GitHub, nothing pushed by the product |
| Retrieval | FTS5 BM25 plus graph-anchored candidates (blame path and anchor neighbourhood), fused with Reciprocal Rank Fusion. Embeddings were off in phase 1; block D adds them whenever an index exists | §9 "FTS5-only" fallback, made the default |
| Answer check | Citation-ID check plus keyword coverage: IDF-weighted for the abstention test, unweighted for each claim's support. Answers report which checker ran | The NLI model is deferred |
| LLM | Optional chain Gemini → Groq → Ollama with the SQLite cache. With no provider configured, answers are extractive: quoted sentences from the evidence, each cited | §9 behaviour for "all providers fail", made the default |
| Not in phase 1 | Log collector, Playwright, kind target, Discord, document upload, PR check or GitHub App | Phase 2 |

**Data model additions** (beyond the original plan): `commits(sha, ts, author, n_files, is_bot, is_sweep, is_merge, ok)`
and `changes(commit, path, lineage, additions, deletions, generated)`, which back the as-of
statistics. Edges are unique per `(src, dst, kind)`; empty evidence and unknown provenance are
rejected by the database.

### 0.1 Phase 2 additions, as built (2026-10-31)

| Concern | As built | Why |
|---|---|---|
| Document upload | `POST /api/docs` (Markdown or PDF, up to 20 MB, synchronous). PDF headings come from font size (PyMuPDF). Uploading the same file name again makes `@vN+1`, adds `SUPERSEDES` and hides the old version from answers and impact. Repository docs are runbooks when their path contains `runbook` or `playbook`; uploads when their chosen type is Runbook. Runbooks are linked `RUNBOOK_FOR` to the services they name. Uploaded text goes to an external model only if `DOCS_ALLOW_EXTERNAL=true` | No object store or queue needed at this size |
| Collector | Python, not Go: one `kubectl logs -f` per deployment (load generator skipped), a 3-second reorder buffer, then the engine in the same process. Every line is also appended to `data/logs/*.jsonl` for replay | One language for the backend; the throughput target (§8) is far above what the demo shop produces |
| Log engine | Drain3 per service; 10-second windows; new-template detector (after the warm-up; severity ≥ warn or ≥ 5 lines) and spike detector (robust z ≥ 4 for two windows); incidents merge within 120 s | DESIGN §6 |
| Culprit vs victim | Services named inside error messages (by name, gRPC name, or cluster IP read from `kubectl get services`) count against the named service; a call-graph bonus for services that callers depend on. Kubelet lines in the stream ("failed to create fsnotify watcher") are dropped as noise | DESIGN §6.4 |
| Incident → change | Score = 0.4·exp(−gap/600 s) + 0.3·(change touches the culprit) + 0.2·(culprit was in that change's stored impact prediction) + 0.1·(a changed file appears in the logs) | Rule-based and explained; weights are defaults, not tuned |
| Faults | 10 scripted scenarios (8 scale-to-zero, a crash-loop, a bad config) with ground truth written to `eval/faults/`; `--deploys` records the guilty change plus two decoys as synthetic deploys | Repeatable campaign for `make eval-rca` |
| Target | kind cluster `hs-target` running the Online Boutique release manifests; the shop is on http://localhost:8081 | Local only; nothing is deployed elsewhere |
| Alerts | Optional Discord webhook (`ALERT_WEBHOOK_URL`) | Off when unset |
| SentinelQA | Tickets in `target/tickets/*.yaml` cite their issue, PR or commit; a rule-based generator (fixed Given/When/Then grammar, assertion lint) instead of an LLM; Playwright (Chromium) runner; Similo-style healer (θ 0.75, δ 0.15, 2 re-runs first) with the DESIGN §5.4 refusals; mutations applied through Playwright request routing in the test browser only | Deterministic and auditable; the shop itself is never modified |
| Semantic search | Local embeddings (Ollama `nomic-embed-text`, with its required `search_document:`/`search_query:` prefixes) over all spans, stored in SQLite (`span_vectors`, float32, re-embedded when a span's text changes); cosine over all vectors (NumPy). Fused with BM25 and the graph path by RRF. `make embed` builds the index | Nothing leaves the machine; a few thousand spans need no vector database |
| Meaning check | When a model is configured, after the citation and lexical checks one more call asks the model whether each cited text supports its claim; unsupported or unclear → the claim is removed (`ANSWER_SUPPORT_CHECK`) | Replaces the NLI model for now; it can only remove claims, never add them |
| Local model | Ollama `gemma3:4b` as the default local chat model in `.env.example` | Fits beside other GPU users on a 6 GB card |
| Incident brief | `POST /incidents/{id}/investigate` (optional follow-up question). Code gathers the core evidence; the model may then make up to 4 read-only tool calls (`why`, `impact` of a candidate commit, `pattern_samples`, `past_incidents`) through a JSON protocol that works the same on every provider (no native tool calling). Every argument is checked against the incident; refused calls count against the budget. Claims pass the citation check, and one that names a root cause must name the ranked culprit. Falls back to quoted evidence. Runs on click only | DESIGN §6.5; replayable through the LLM cache because tool results are deterministic for a given database |
| Provider timeouts | Every provider connects within 5 s (`CONNECT_TIMEOUT`) and keeps its full read timeout for generation | A stopped Ollama otherwise made each request wait the whole 120 s before falling back |

`GET /scorecard` returns the impact replay (main repository and other repositories), the fault
campaign and the mutation harness, each card marked with its suite and whether it is synthetic.
Retrieval results appear only in `eval/results/latest.md`.

### 0.2 Graph layer: Neo4j (2026-10-31, ADR 0001)

| Concern | Choice |
|---|---|
| Store of record | SQLite, as before: ingest, uploads, as-of history, FTS5, LogSense |
| Graph database | Neo4j `5.26.32-community` in Docker (`deploy/neo4j/compose.yaml`, `make graph-up`), ports bound to 127.0.0.1, heap 512 MB, page cache 256 MB. Password from `NEO4J_PASSWORD`, else generated once into `data/neo4j/password` (git-ignored, mode 600) |
| Sync | `make graph-sync`: every node and edge, with provenance and evidence, into namespace `<owner>__<name>`; batched `UNWIND … MERGE` (2,000 per batch), stale items swept; counts checked against SQLite. `make ingest`, uploads and `make codegraph` re-sync when the repository was synced before |
| Reads | `GraphReader` (six reads) in Cypher and SQL. `GRAPH_BACKEND=auto` (default) uses Neo4j only when reachable and in sync (same namespace, and node and edge counts and maximum ids equal SQLite's); `sqlite` or `neo4j` force a store, and a forced but unavailable Neo4j returns 503. Neo4j serves the Map, the "why" path and impact structure; statistics and evaluation stay on SQLite |
| Python driver | `neo4j` 6.4.1 |
| Verification | `tests/test_graphstore.py` (integration, skipped without Neo4j): every read and the Map, impact and "why" outputs are identical on both stores |

### 0.3 Workspace instance and model settings (2026-10-31, ADR 0002)

`make run` (`scripts/run.sh`) starts two API processes from one command: the demo on :8000 and a
workspace on :8080 (`HINDSIGHT_MODE=workspace`, `GRAPH_BACKEND=sqlite`). A workspace starts empty;
each connected repository gets its own folder under `data/workspace/repos-by-name/<owner>__<name>/`
(database, clone, GitHub cache, uploads, `results/`), and `active.json` names the one on screen.
Incidents and Tests are hidden in a workspace (they need the demo shop).

| Method | Path | Purpose |
|---|---|---|
| GET | `/config` | `mode` (demo / workspace), the demo's port, the repository, the pages this instance shows |
| POST | `/workspace/connect` `{url}` | Start ingest + impact replay in a background thread (202; 409 while one runs; 422 for a non-GitHub URL; 404 on the demo instance) |
| GET | `/workspace/status` | Repository on screen, job state, stage, progress, whether it was measured |
| GET · PUT | `/settings/llm` | Provider order and model names (non-secret, `<DATA_DIR>/settings.json`); keys are reported as set or not, never returned |
| GET | `/settings/llm/{provider}/models` | Models the configured key can use (Gemini `models.list` filtered to `generateContent`, Groq `/openai/v1/models`, Ollama `/api/tags`) |

**Any repository layout.** Services are recognised as children of `src/`, `services/`,
`microservices/` or `svc/`, and as children of `app/`, `apps/` or `cmd/` that hold their own build
or entry file (Dockerfile, `go.mod`, `main.go`, `package.json`, `pom.xml`, `*.csproj` and the like),
so a Rails `app/models/` stays one folder. Every ingest rebuilds the file → service links and the
service calls, and drops services or folders left without links. On Online Boutique, eShop and the
OpenTelemetry Demo the rules give exactly the same services as before (0 files reassigned); on
go-kratos beer-shop they find 8 services and 6 calls where the old rule found none. The Map starts
at the most linked service (else folder, else file) when no item is chosen, instead of a fixed
Online Boutique service.

The workspace's tour calls the demo's API, so the demo allows the workspace origin through
`CORS_ORIGINS`. Only `https://github.com/<owner>/<name>` URLs are accepted, and `git clone` runs
with `GIT_TERMINAL_PROMPT=0` so a missing or private repository fails at once instead of waiting
for a username.

### 0.4 Code structure layer (2026-10-31, block I)

| Concern | Choice |
|---|---|
| Tool | graphify `0.9.99` (PyPI `graphifyy`), installed as a separate command (`uv tool install graphifyy==0.9.99`) and run like `git`. It is not a Python dependency of the backend |
| Run | `make codegraph` runs `graphify extract <clone> --code-only --no-cluster --out data/codegraph` with `GRAPHIFY_BACKEND=none`: tree-sitter only, no model calls, nothing leaves the machine. About 3 s on Online Boutique |
| Kept | Functions, methods and classes (`_callable`) in files the graph knows at HEAD become `Symbol` nodes (`symbol:<path>#<name>@L<line>`, with a link pinned to the parsed commit). `File -DEFINES-> Symbol` comes from each symbol's own file and line; `Symbol -CODE_CALLS-> Symbol` from call sites marked `EXTRACTED`, with up to 3 sites and the count. Both are `static` |
| Left out | `INFERRED` links, type references, imports (only 6 of 440 resolve inside the repository), comment text, configuration keys, and generated files (`is_generated`: protobuf stubs and the like) |
| Isolation | New kinds only: `CODE_CALLS`, never `CALLS`, because impact reads every `CALLS` edge as a service-to-service call. Impact scoring and the evaluation never read the layer; a test checks that impact output is identical before and after an import |
| Map | `GET /graph?code=true` adds the layer. It is off by default and always on when the centre is a function or class. A re-import replaces the whole layer; Neo4j re-syncs when it was synced before |
| Online Boutique (HEAD `c7cb6d5`) | 307 symbols in 43 files and 148 call links (12 across files); 1,690 symbols in generated files and 77 inferred calls left out |

### 0.5 One-command start (2026-10-31)

| Concern | Choice |
|---|---|
| Entry | `make start` = `scripts/start.sh`. Switches `--live`, `--dev`, `--no-neo4j`, `--skip-setup` (with make: `LIVE=1`, `DEV=1`, `NO_NEO4J=1`, `SKIP_SETUP=1`) |
| Steps | 1 tools (uv, pnpm, git) · 2 `.env` (copied from `.env.example` once) · 3 `uv sync` and `pnpm install` · 4 ingest if the database has no repository · 5 Neo4j when Docker runs · 6 code layer when graphify is installed and the layer is older than the ingested HEAD, then `graph-sync` if Neo4j is not in sync · 7 the log collector when the kind cluster runs (`--live` creates it) · 8 `scripts/run.sh` (or `scripts/dev.sh` with `--dev`) |
| Optional parts | Docker, graphify, the cluster and Ollama are skipped with a note when missing; a summary prints what is on and off |
| Shutdown | Ctrl-C stops the dashboards and the collector; Neo4j and the cluster keep running (`make graph-down`, `make target-down`) |
| Port check | `scripts/run.sh` probes its ports with a 1-second limit: under WSL mirrored networking a just-freed port can hang instead of refusing |

## 1. Architecture (as built)

```
  browser ── web (Vite + React; built into web/dist and served by the API) ──REST/JSON──┐
                                                                                         ▼
  backend: FastAPI (one uvicorn process per instance: demo :8000, workspace :8080)
    ├─ api/        app (routes) · service · workspace · llm_settings
    ├─ brain/      ingest · gitlog · forge cache · static calls · docs/uploads · ask · impact
    │              · risk · graph (neighbourhood) · codegraph
    ├─ correlate/  deploys (incident → change) · investigate (incident brief)
    ├─ logsense/   collector · events · engine (Drain3, detectors, culprit) · incidents · alerts · faults
    ├─ sentinel/   spec (tickets) · generator · store · runner (Playwright) · fingerprint (healer)
    │              · mutations · service
    ├─ core/       config · schemas · db (SQLite + FTS5) · llm chain · embed · text · graphstore · graphsync
    └─ eval/       hs-eval: impact replay · retrieval · rca · heal · latest.md report

  hindsight collect (CLI) ── kubectl logs -f ──▶ engine in that process ──▶ same SQLite database
                                                 └─ every line also to data/logs/*.jsonl (replay source)
  target: Online Boutique release manifests on kind (hs-target), shop :8081 ◀── Playwright · fault scripts
  optional: Neo4j in Docker ◀── graph-sync        graphify CLI (offline) ──▶ code structure layer
  Discord ◀── alerts        Gemini · Groq · Ollama ◀── scrubbed, cached JSON prompts
```

Modules call each other as plain Python functions. The collector, ingest, evaluations and
fault scripts run as CLI commands against the same SQLite database the API reads. A workspace
connect runs ingest and the impact replay in a background thread. Test runs and incident briefs
run synchronously inside their request.

## 2. Stack (as built)

| Concern | Choice |
|---|---|
| Backend | Python 3.12, uv, FastAPI, pydantic v2, uvicorn |
| Storage | SQLite (WAL mode) with FTS5 (`porter unicode61`). Embeddings as float32 blobs in `span_vectors`, cosine in NumPy |
| Graph | SQLite as the store of record; NetworkX for neighbourhoods; optional Neo4j 5.26.32-community (Docker) through the `neo4j` 6.4.1 driver |
| Git mining | Plain `git` through `subprocess`: `log -M --numstat`, `blame --porcelain`, `grep`, `log -S` |
| Forge data | GitHub REST through httpx, read-only, cached JSON pages, optional token |
| Docs | PyMuPDF (PDF) and heading-based Markdown chunking (sections over 450 words split at blank lines) |
| Log parsing | drain3 |
| UI tests | Playwright (Python, Chromium) |
| Claim check | Citation IDs plus keyword coverage; optional model support check. No NLI model |
| LLM | Gemini → Groq → Ollama (configurable), JSON mode, temperature 0, SQLite cache with modes `off \| record \| replay \| read-through` |
| Embeddings | Ollama `nomic-embed-text`, local |
| Code structure | graphify 0.9.99 (external CLI, tree-sitter, offline) |
| Collector | Python, `kubectl logs -f` |
| Frontend | Vite, React, TypeScript (strict), TanStack Query, 3d-force-graph with three.js, lucide-react icons. Types generated from `/openapi.json` with openapi-typescript (`make gen-api`) |
| Target | Online Boutique release manifests (pinned upstream images, nothing is built) on the kind cluster `hs-target` |
| Alerts | Discord webhook (optional) |
| Tooling | ruff, pytest, eslint, tsc, prettier, vitest |

**Not used:** gRPC between our components, protobuf, Postgres, ClickHouse, NATS.

## 3. Contracts

`backend/hindsight/core/schemas.py` holds the Pydantic models, **frozen at H2**; after H2 only
additive changes, with approval (block H added `InvestigateRequest`, `InvestigationStep` and
`IncidentBrief`). FastAPI publishes them as OpenAPI, and `make gen-api` regenerates the web types.

| Area | Models |
|---|---|
| Answers | `AskRequest`, `Answer` (status `answered \| not_found`, mode `llm \| extractive \| none`), `Claim`, `EvidenceItem`, `AnswerCheckRequest`, `AnswerCheck`, `AnswerCheckSummary` |
| Impact | `ImpactRequest`, `ImpactReport`, `ImpactItem`, `Reason` (each with a `Provenance`) |
| Documents | `UploadReport`, `DocInfo`, `RunbookSection` |
| Incidents | `IncidentSummary`, `Incident`, `CulpritRank` (role `culprit \| victim \| affected`), `IncidentTemplate`, `ChangeCandidate`, `TimelinePoint`, `DeployRequest`, `DeployRecord`, `InvestigateRequest`, `InvestigationStep`, `IncidentBrief` |
| Graph | `GraphNode`, `GraphEdge`, `GraphResponse`, `GraphStatus`, `SearchHit` |
| History | `RepoStats`, `CommitSummary`, `RiskFile`, `RiskComponent`, `RiskReport` |
| Tests | `QaTest` (status `active \| rejected \| unvalidated`), `QaRun` (outcome `passed \| healed \| failed \| needs_human \| flaky`), `QaGrounding`, `QaRepair` (status `proposed \| approved \| rejected \| reverted`) |
| Proof | `ScoreCard`, `MethodScore` |
| Instance | `AppConfig` (mode `demo \| workspace`, features), `ConnectRequest`, `WorkspaceStatus`, `LlmProvider`, `LlmSettings`, `LlmSettingsUpdate`, `ModelList` |

## 4. Data model (SQLite, as built)

```
nodes(id, kind, key UNIQUE, title, url, ts, attrs JSON)
edges(id, src, dst, kind, weight, provenance CHECK(...6 values...), evidence JSON NOT NULL non-empty, ts,
      UNIQUE(src, dst, kind))                -- a heuristic link never overwrites a stronger one
spans(node_id, span_kind, ordinal, text, url, ts, flagged)   spans_fts (FTS5)   span_vectors
commits, changes                             -- as-of history statistics
meta                                         -- repo, stats, forge, ingested_at, graph_sync, codegraph, service_addresses …
llm_cache, answer_checks
templates, template_counts, incidents, deploys   -- LogSense; deploys keep the impact prediction stored at deploy time
qa_tests, qa_runs, qa_repairs                -- SentinelQA
```

**Node kinds** on Online Boutique (2026-10-31): PR 2,735 · Commit 2,700 · Issue 1,009 · File 493 ·
Symbol 307 · Author 147 · DocChunk 127 · Doc 33 · Component 14 · Service 12 · Test 5. `Comment`
appears once GitHub comments are fetched.

**Edge kinds:** MODIFIES, AUTHORED_BY, PART_OF, REFERENCES, CO_CHANGES_WITH, BELONGS_TO, DEFINES,
CODE_CALLS, MENTIONS, FIXES, COVERS, CALLS, EXERCISES, DERIVED_FROM; also ON and ABOUT (comments),
RUNBOOK_FOR and SUPERSEDES (runbooks and uploads) when those exist.

**Files under `data/`** (git-ignored): `hindsight.db`, `repos/` (clones), `forge/` (GitHub pages),
`uploads/`, `logs/*.jsonl`, `sentinel/shots/`, `codegraph/`, `neo4j/password`, `settings.json`,
`workspace/`.

**No-leakage rule:** history statistics use only counted commits with `ts < as_of` (strict) when a
past commit is checked or replayed. Structural signals (service calls, file → service) describe
HEAD and are not time-filtered.

## 5. HTTP API (FastAPI, `/api`, as built)

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | `{"status":"ok"}` |
| GET | `/config` | Instance mode, demo port, repository, visible pages |
| GET · POST | `/workspace/status` · `/workspace/connect` | Workspace instance only (§0.3) |
| GET · PUT | `/settings/llm` | Provider order and models; keys reported as set or not |
| GET | `/settings/llm/{provider}/models` | Models the configured key can use |
| GET | `/repo/stats` | Repository, ingest statistics, GitHub cache state |
| GET | `/commits?limit=&include_bots=` | Recent non-merge commits (bots excluded by default) |
| POST | `/ask` `{question, file?, line_start?, line_end?}` | An `Answer` |
| GET · POST | `/answer-checks` | Visitor ratings of answers, and their tally |
| POST | `/impact` `{files[] \| commit}` | An `ImpactReport` (not stored; deploys store their own prediction) |
| GET | `/graph?key=&depth=1\|2&code=&view=around\|services` | Neighbourhood for the Map; without `key` it starts at the most linked service, folder or file (404 for an empty graph); `code=true` adds the code structure layer; `view=services` (the Map's first view) draws every service and the calls between them, falling back to the neighbourhood when there are fewer than two services |
| GET | `/graph/status` | Which store answers, sync state, counts, Neo4j Browser link |
| GET | `/search?q=` | Nodes by key or title (Map search) |
| GET | `/risk` | Knowledge at risk |
| GET | `/scorecard` | Cards from the newest result files |
| POST · GET | `/docs` | Upload a document (multipart) · list documents |
| GET | `/docs/blob/{name}` | The stored file of an upload |
| GET | `/services/{name}/runbooks` | Runbook sections for a service |
| GET | `/qa/tests` | Generated tests with grounding and their last run |
| POST | `/qa/tests/{test_id}/run` | Run one active test now (synchronous) |
| GET | `/qa/repairs` | Repairs, newest 100 |
| POST | `/qa/repairs/{repair_id}/{approve\|reject\|revert}` | Human review of a repair |
| GET | `/qa/shots/{name}` | Before/after screenshot of a repair |
| GET | `/incidents` · `/incidents/{id}` | Newest 50 incidents · one incident with ranking, timeline, candidates, runbooks |
| POST | `/incidents/{id}/investigate` `{question?}` | An `IncidentBrief` |
| GET · POST | `/deploys` | Recorded deploys · record one (computes and stores its impact prediction) |

Ingest, embedding, the code layer, graph sync, log collection, faults and evaluations are CLI
commands (`make ingest`, `make embed`, `make codegraph`, `make graph-sync`, `make collect`,
`make fault`, `make eval*`), not HTTP endpoints. There is no `/logs` endpoint: the collector writes
to the database directly.

## 6. Configuration

All configuration comes from `.env` (see `.env.example`); the backend reads `.env` itself.

| Group | Variables |
|---|---|
| Data and target | `DATA_DIR`, `DB_PATH`, `TARGET_REPO_URL`, `TARGET_URL` (the shop, default :8081), `KIND_CLUSTER_NAME` |
| GitHub | `GITHUB_TOKEN` (optional), `FORGE_MAX_REQUESTS` (default 50) |
| Models | `LLM_PROVIDER_ORDER`, `LLM_CACHE_MODE`, `LLM_ALLOW_EXTERNAL`, `DOCS_ALLOW_EXTERNAL`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `GROQ_API_KEY`, `GROQ_MODEL`, `OLLAMA_HOST`, `OLLAMA_CHAT_MODEL`, `OLLAMA_EMBED_MODEL`, `ANSWER_SUPPORT_CHECK` |
| Graph | `GRAPH_BACKEND` (`auto \| sqlite \| neo4j`), `NEO4J_PASSWORD` (else `data/neo4j/password`) |
| Alerts | `ALERT_WEBHOOK_URL` |
| Server and instance | `API_HOST` (default 127.0.0.1), `API_PORT`, `HINDSIGHT_MODE`, `DEMO_PORT`, `WORKSPACE_PORT`, `CORS_ORIGINS`, `RESULTS_DIR`; for the workspace, `scripts/run.sh` also sets `DATA_DIR`, `DB_PATH` and `GRAPH_BACKEND=sqlite` |
| Start script | `LIVE`, `DEV`, `NO_NEO4J`, `SKIP_SETUP` (§0.5) |

Model choices saved from the dashboard (`<DATA_DIR>/settings.json`, per instance) override
`LLM_PROVIDER_ORDER` and the `*_MODEL` variables; they hold no secrets. `LLM_DAILY_REQUEST_CAP` is
listed in `.env.example` but nothing reads it yet (§10).

## 7. Security and safety (as built)

- Keys come from `.env` only and are never logged. The Settings page shows whether a key is set, never its value, and has no field to type one; a key is sent only in a request header to its own provider.
- Every prompt, local or external, passes a scrubber that replaces GitHub tokens, `sk-` keys, AWS keys, Google API keys and email addresses with `[redacted]` before the cache lookup or the call. Workspace error messages are scrubbed too.
- Ingested text is wrapped as delimited evidence with an instruction never to follow it. Instruction-like passages are flagged at ingest and upload; flagged items are shown with a warning and never sent to a model.
- Uploaded document text is withheld from the model whenever the chain contains an external provider and `DOCS_ALLOW_EXTERNAL=false`.
- The model never decides: rankings, culprits, candidates, heal and refuse decisions come from code. Every claim must cite evidence it was shown; a brief's root-cause claim must name the ranked culprit; the optional support check can only remove claims.
- The healer never edits assertions and refuses on defect evidence.
- graphify runs with `GRAPHIFY_BACKEND=none` and `--code-only`: no model calls.
- Tests and faults run only against the local kind cluster. Mutations change pages in the test browser only.
- Workspace connect accepts only `https://github.com/<owner>/<name>`; `git clone` runs with `GIT_TERMINAL_PROMPT=0`.
- The API has no authentication and binds to 127.0.0.1 by default. CORS is enabled only for the origins in `CORS_ORIGINS`. The answer-rating endpoint accepts any rating (no deduplication), so its tally is a spot check, not a measurement.

## 8. Non-functional targets (laptop)

| Target | Value | Measured |
|---|---|---|
| Impact report | ≤ 10 s | Not measured |
| Ask (cloud LLM) | ≤ 15 s | Not measured |
| Collector throughput | ≥ 5k lines/s | Not measured |
| Time to detect after fault | ≤ 60 s median | 16.5 s median, p90 28 s (`rca-20261030T205813Z.json`) |
| Full history ingest of Online Boutique (excluding API fetch) | ≤ 5 min | Not measured |
| Fallback when a model provider is down | — | 5 s connect timeout (was about 2 min before the fix) |
| `make start` on a prepared machine | — | About 8 s to both dashboards (one run, 2026-10-31) |

## 9. Failure modes and fallbacks (as built)

| Failure | Behaviour |
|---|---|
| kind cluster down or slow | `make demo-replay`: recorded `data/logs/*.jsonl` flow through the same engine; `make start` skips the log stream |
| A model provider is down | It fails to connect within 5 s and the chain moves to the next provider |
| All model providers fail, or none is configured | Answers and briefs quote sentences from the evidence, each cited; incident ranking and evidence are unaffected; follow-up questions return "not found" |
| Model returns malformed JSON or uncited claims | Next provider, or the claims are dropped by the citation check |
| GitHub rate limit or request cap | Stop, keep the cached pages, resume on the next ingest; git-derived links are unaffected |
| Neo4j down or out of sync | `GRAPH_BACKEND=auto` reads SQLite; a forced `neo4j` returns 503 |
| graphify not installed | The code structure layer is off; `make codegraph` says how to install it |
| Healer uncertain | `needs_human`, never a guess |
| Semantic index missing or Ollama unreachable | Keyword and graph search only; when the index exists but Ollama fails, the answer notes "semantic search unavailable … keyword search only" |
| WSL mirrored networking hangs on a just-freed port | `scripts/run.sh` gives its port check a 1-second limit |

## 10. Known differences from the other docs (2026-10-31)

DESIGN.md, EVALUATION.md and AGENTS.md were not rewritten in this pass. Where they disagree with
the code, the code (and this TRD) is current:

- **Collector:** AGENTS and DESIGN §6.1 describe a Go collector posting to `/api/logs`; it is a Python CLI writing to the database.
- **Test generation:** DESIGN §5.2 has an LLM filling a Playwright template; the generator is rule-based with role-and-name locators. The outcome called `genuine_failure` in DESIGN is `failed` in code.
- **Co-change:** DESIGN §2.1 weights each commit by a 365-day decay; the code uses an unweighted ratio plus a separate recency signal. Weights are hardcoded defaults (`weights_tuned: false`); no validation split is used.
- **Evaluation protocol:** EVALUATION §1 says statistics use the oldest 70% of history; the replay uses all counted history before each query commit. The bot/sweep filtering ablation in EVALUATION §2 is not implemented. `make eval` runs only the impact replay and the report.
- **Correlation term:** DESIGN §7 calls the 0.1 term `frame_overlap`; the code matches changed file names in the incident's log samples.
- **Knowledge at risk:** the list includes files whose single owner is still active (marked as such), not only inactive owners.
- **Dashboard:** a light theme is stored but no control switches to it, and the stylesheet is dark only. Levels 5 and 6 cannot be reached with the current achievements.
- **Brief:** a follow-up that a configured model fails to answer gets the note "follow-up questions need a configured model", which is misleading in that case.
- **`make try-repo`** writes its results to `eval/results/` regardless of `RESULTS_DIR`.
- **Alerts** build their own text instead of using the incident summary (DESIGN §6.5).
- **`LLM_DAILY_REQUEST_CAP`** is in `.env.example` but not implemented.
