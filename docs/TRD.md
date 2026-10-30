# Hindsight: TRD (36-hour edition)

## 0. Phase 1 (PS 06) technical scope

One FastAPI process plus the web dashboard. Phase 1 differs from the sections below as follows:

| Concern | Phase 1 choice | Why |
|---|---|---|
| Git mining | Plain `git log -M --numstat` and `git blame` through `subprocess`, instead of PyDriller | Fewer dependencies; rename lineage is kept |
| Forge data | GitHub REST, read-only. Unauthenticated by default (60 requests/hour), at most `FORGE_MAX_REQUESTS` per run (default 50), pages cached under `data/forge/`; the next run resumes. `GITHUB_TOKEN` is optional | Maintainer: little time spent on GitHub, nothing pushed |
| Retrieval | FTS5 BM25 plus graph-anchored candidates (blame path and anchor neighbourhood), fused with Reciprocal Rank Fusion. Embeddings are off | §9 "FTS5-only" fallback, made the default |
| Answer check | Citation-ID check plus IDF-weighted keyword coverage; answers report which checker ran | The NLI model is deferred |
| LLM | Optional chain Gemini → Groq → Ollama with the SQLite cache. With no provider configured, answers are extractive: quoted sentences from the evidence, each cited | §9 behaviour for "all providers fail", made the default |
| Not in phase 1 | Go collector, Playwright, kind target, Discord, document upload, PR check or GitHub App | Phase 2 |

**Data model additions** (beyond §4): `commits(sha, ts, author, n_files, is_bot, is_sweep, is_merge, ok)`
and `changes(commit, path, lineage, additions, deletions, generated)`, which back the as-of
statistics. Edges are unique per `(src, dst, kind)`; empty evidence and unknown provenance are
rejected by the database.

**Phase 1 API** (`/api`): `GET /repo/stats`, `POST /ask`, `POST /impact {files[] | commit}`,
`GET /graph?key=&depth=`, `GET /search?q=`, `GET /risk`, `GET /scorecard`, `GET /commits`.
Ingestion runs from the CLI (`make ingest`).

**Version control:** no repository is created and nothing is pushed until the maintainer decides.

### 0.1 Phase 2 additions, as built (2026-10-31)

Phase 2 adds features on top of the same process. Where it differs from §1–§9:

| Concern | As built | Why |
|---|---|---|
| Document upload | `POST /api/docs` (Markdown or PDF, synchronous). PDF headings come from font size (PyMuPDF). Re-uploading the same title makes `@vN+1`, adds `SUPERSEDES` and hides the old version from answers and impact. Runbooks are found by title or path (`runbook`, `playbook`) and linked `RUNBOOK_FOR` to the services they name. Uploaded text goes to an external model only if `DOCS_ALLOW_EXTERNAL=true` | No object store or queue needed at this size |
| Collector | Python, not Go: one `kubectl logs -f` per deployment (load generator skipped), a 3-second reorder buffer, then the engine. Every line is also appended to `data/logs/*.jsonl` for replay | One language for the backend; the throughput target (§8) is far above what the demo shop produces |
| Log engine | Drain3 per service; 10-second windows; new-template detector (after a 600-second warm-up; severity ≥ warn or ≥ 5 lines) and spike detector (robust z ≥ 4 for two windows); incidents merge within 120 s | DESIGN §6 |
| Culprit vs victim | Services named inside error messages (by name, gRPC name, or cluster IP read from `kubectl get services`) count against the named service; a call-graph bonus for services that callers depend on. Kubelet lines in the stream ("failed to create fsnotify watcher") are dropped as noise | DESIGN §6.4 |
| Incident → change | Score = 0.4·exp(−gap/600 s) + 0.3·(change touches the culprit) + 0.2·(culprit was in that change's stored impact prediction) + 0.1·(a changed file appears in the logs) | Rule-based and explained; weights are defaults, not tuned |
| Faults | 10 scripted scenarios (8 scale-to-zero, a crash-loop, a bad config) with ground truth written to `eval/faults/`; `--deploys` records the guilty change plus two decoys as synthetic deploys | Repeatable campaign for `make eval-rca` |
| Target | kind cluster `hs-target` running the Online Boutique release manifests; the shop is on http://localhost:8081 | Local only; nothing is deployed elsewhere |
| Alerts | Optional Discord webhook (`ALERT_WEBHOOK_URL`) | Off when unset |
| SentinelQA | Tickets in `target/tickets/*.yaml` cite their issue, PR or commit; a rule-based generator (fixed Given/When/Then grammar, assertion lint) instead of an LLM; Playwright (Chromium) runner; Similo-style healer (θ 0.75, δ 0.15, 2 re-runs first) with the DESIGN §5.4 refusals; mutations applied through Playwright request routing in the test browser only | Deterministic and auditable; the shop itself is never modified |
| Semantic search | Local embeddings (Ollama `nomic-embed-text`, with its required `search_document:`/`search_query:` prefixes) over all spans, stored in SQLite (`span_vectors`, float32, re-embedded when a span's text changes); cosine over all vectors (numpy). Fused with BM25 and the graph path by RRF. `make embed` builds the index | Nothing leaves the machine; a few thousand spans need no vector database |
| Meaning check | When a model is configured, after the citation and lexical checks one more call asks the model whether each cited text supports its claim; unsupported or unclear → the claim is removed (`ANSWER_SUPPORT_CHECK`) | Replaces the NLI model of §3 for now; it can only remove claims, never add them |
| Local model | Ollama `gemma3:4b` (3.4 GB) as the default local chat model | Fits beside other GPU users on a 6 GB card |

Phase 2 API additions: `POST/GET /docs`, `GET /docs/blob/{name}`, `GET /services/{name}/runbooks`,
`GET /incidents`, `GET /incidents/{id}`, `GET/POST /deploys`. `GET /scorecard` now returns both the
impact replay and the fault campaign, each card marked with its suite and whether it is synthetic.

### 0.2 Graph layer: Neo4j (2026-10-31, ADR 0001)

| Concern | Choice |
|---|---|
| Store of record | SQLite, as before: ingest, uploads, as-of history, FTS5, LogSense |
| Graph database | Neo4j `5.26.32-community` in Docker (`deploy/neo4j/compose.yaml`, `make graph-up`), ports bound to 127.0.0.1, heap 512 MB, page cache 256 MB. Password from `NEO4J_PASSWORD`, else generated once into `data/neo4j/password` (git-ignored, mode 600) |
| Sync | `make graph-sync`: every node and edge, with provenance and evidence, into namespace `<owner>__<name>`; batched `UNWIND … MERGE`, stale items swept; counts checked against SQLite. `make ingest` and uploads re-sync when the repository was synced before |
| Reads | `GraphReader` (six reads) in Cypher and SQL. `GRAPH_BACKEND=auto` (default) uses Neo4j only when reachable and in sync; `sqlite` or `neo4j` force a store. Neo4j serves the Map, the "why" path and impact structure; statistics and evaluation stay on SQLite |
| Python driver | `neo4j` 6.4.1 |
| Verification | `tests/test_graphstore.py` (integration): every read and the Map, impact and "why" outputs are identical on both stores (fixture repository, throwaway namespace) |

API: `GET /graph/status` (which store answers, sync state, counts, Neo4j Browser link);
`GET /graph` responses carry `backend`. Example Cypher queries: `docs/GRAPH.md`.

## 1. Architecture

```
  web (Vite + React) ──REST/JSON──▶  backend (FastAPI, one Python process)
                                       ├─ brain/      ingest · linking · impact · ask · docs
                                       ├─ sentinel/   generate · run · heal   (Playwright)
                                       ├─ logsense/   templates · detectors · culprit · alert
                                       ├─ correlate/  incident → change
                                       ├─ core/       schemas · db (SQLite + FTS5) · llm chain · graph (NetworkX)
                                       └─ eval/       hs-eval CLI
                                            ▲ POST /api/logs (batches)
  collector (Go) ── kubectl logs -f ────────┘   also writes data/logs/*.jsonl (the replay source)
  target: Online Boutique fork on kind ◀── Playwright tests · fault scripts
  Discord ◀── alerts        LLM providers ◀── scrubbed, cached prompts
```

There is one backend process. Modules call each other as plain Python functions; long jobs run as
FastAPI background tasks or as CLI commands. Ingestion and test runs can also be invoked from the CLI.

## 2. Stack

| Concern | Choice |
|---|---|
| Backend | Python 3.12, uv, FastAPI, pydantic v2, uvicorn |
| Storage | SQLite (WAL mode) with FTS5 for keyword search. Embeddings stored as float32 blobs, with cosine similarity in NumPy |
| Graph algorithms | NetworkX, loaded from SQLite on demand |
| Git mining | PyDriller (plus raw `git` for blame) |
| Forge data | GitHub REST/GraphQL via httpx, with on-disk JSON cache. Optional token |
| Docs | PyMuPDF (PDF) and a Markdown parser. Heading-based chunking |
| Log parsing | drain3 |
| UI tests | Playwright (Python) |
| NLI check | A small cross-encoder NLI model on CPU, chosen and verified in B2; an LLM-judge fallback when the model is unavailable |
| LLM | Provider chain Gemini → Groq → Ollama (configurable), with a SQLite cache and modes `off \| record \| replay \| read-through` |
| Collector | Go 1.22+, standard library only, using `kubectl logs -f` |
| Frontend | Vite, React, TypeScript, TanStack Query, Cytoscape.js. Types generated from `/openapi.json` (openapi-typescript) |
| Target | Fork of GoogleCloudPlatform/microservices-demo on kind, built with skaffold |
| Alerts | Discord webhook |

**Not used:** gRPC between our components, protobuf, Postgres, ClickHouse, NATS. They are on the
future-ideas slide.

## 3. Contracts

`backend/hindsight/core/schemas.py` holds the Pydantic models, which are **frozen at H2**. After H2,
additive optional fields only. FastAPI publishes them as OpenAPI, and the web app regenerates its
types with `pnpm gen:api`.

Minimum models:
- `Evidence {id, kind, provenance, url, text?, payload}`
- `ImpactItem {target_kind: service|file|test|doc, target, score, reasons[], evidence[]}`
- `ImpactReport {commit_or_diff, items[], tests_to_run[], docs_to_update[], created_at}`
- `Claim {text, cites[]}`
- `Answer {status: answered|not_found, claims[], evidence[], nearest[]}`
- `TestResult {test, status: passed|failed|healed|genuine_failure|flaky|needs_human, reasons[], repair_id?}`
- `TestRunReport {run_id, results[], started_at, finished_at}`
- `Repair {id, test, old_locator, new_locator, score, breakdown, screenshots, patch, status}`
- `CulpritRank {service, role: culprit|victim|healthy, score, evidence[]}`
- `Incident {id, opened_at, window, ranking[], templates[], summary?, candidates[], runbooks[]}`
- `ChangeCandidate {commit, score, evidence[]}`
- `ScoreCard {metric, value, baseline, n, ci_low, ci_high, command, result_file}`

## 4. Data model (SQLite)

```sql
nodes(id INTEGER PK, kind TEXT, key TEXT UNIQUE, attrs JSON, ts INTEGER)       -- ts = event time (epoch s)
edges(id INTEGER PK, src INTEGER, dst INTEGER, kind TEXT, weight REAL,
      provenance TEXT CHECK(provenance IN ('explicit','api','static','statistical','runtime','heuristic')),
      evidence JSON NOT NULL, ts INTEGER)
spans(id INTEGER PK, node_id INTEGER, span_kind TEXT, text TEXT, url TEXT, ts INTEGER)
spans_fts  -- FTS5 virtual table over spans.text
embeddings(span_id INTEGER PK, model TEXT, dim INTEGER, vec BLOB)
predictions(id INTEGER PK, kind TEXT, input JSON, output JSON, created_at INTEGER)   -- impact predictions are stored for correlation
test_runs, test_results, repairs, log_events, templates, template_counts, incidents, deploys, llm_cache
```

**Node kinds:** `Commit, File, Service, PR, Issue, Review, Comment, Author, Doc, DocChunk, Test,
TestRun, Repair, LogTemplate, Incident, Deploy`.

**No-leakage rule:** replay queries pass `as_of_ts`, and only rows with `ts <= as_of_ts` are used.

## 5. HTTP API (FastAPI, `/api`)

| Method | Path | Purpose |
|---|---|---|
| POST | `/ingest` `{repo_url}` | Start a history ingest (background task) |
| GET | `/repo/stats` | Commits, bot share, link counts |
| POST | `/impact` `{commit_sha \| diff}` | Return an ImpactReport (also stored as a prediction) |
| POST | `/ask` `{question, anchor?}` | Return an Answer |
| POST | `/docs` (multipart) | Upload a document; returns an ingest report |
| GET | `/docs` | List documents with version, age and link counts |
| POST | `/tests/generate` `{ticket_id}` | Generate and validate tests |
| POST | `/tests/run` `{tests?}` | Run tests; returns a TestRunReport |
| POST | `/repairs/{id}/approve` · `/reject` | Human review of a repair |
| POST | `/logs` (batch) | Collector ingest |
| GET | `/incidents` · `/incidents/{id}` | Incidents with ranking, candidates and runbooks |
| POST | `/deploys` | Record a deploy (commit SHA + time) |
| GET | `/graph/{node_key}?depth=` | Neighbourhood for the graph view |
| GET | `/scorecard` | Latest results from `eval/results/` |

## 6. Configuration

All configuration comes from `.env` (see `.env.example`). The key variables are
`LLM_PROVIDER_ORDER`, `LLM_CACHE_MODE`, `LLM_ALLOW_EXTERNAL`, the API keys, `TARGET_URL`,
`KIND_CLUSTER_NAME`, `ALERT_WEBHOOK_URL`, `GITHUB_TOKEN` and `DATA_DIR`.

## 7. Security and safety

- Keys come from `.env` only. Never log key values.
- A scrubber runs before any external LLM call, catching tokens, keys and emails.
- Uploaded docs default to local-only LLM processing (`DOCS_ALLOW_EXTERNAL=false`).
- Ingested text is wrapped as delimited evidence and never treated as instructions. Instruction-like passages are flagged at ingest.
- Tests run only against our own local cluster.

## 8. Non-functional targets (laptop)

| Target | Value |
|---|---|
| Impact report | ≤ 10 s |
| Ask (cloud LLM) | ≤ 15 s |
| Collector throughput | ≥ 5k lines/s |
| Time to detect after fault | ≤ 60 s median |
| Full history ingest of Online Boutique (excluding API fetch) | ≤ 5 min |

## 9. Failure modes and fallbacks

| Failure | Behaviour |
|---|---|
| kind cluster down / slow | `make demo-replay`: recorded `data/logs/*.jsonl` and stored test results flow through the same pipeline |
| All LLM providers fail | Return the evidence list without prose; the incident view still shows ranking and evidence |
| GitHub rate limit | Use the cache; run git-only, marking forge links as unavailable |
| Healer uncertain | `needs_human`, never a guess |
| NLI model can't load | Fall back to an LLM judge; report which checker produced each validity number |
