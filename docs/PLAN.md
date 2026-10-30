# Hindsight: 36-hour plan

## Phase 1: PS 06 build (target 4–5 hours, decided 2026-10-30)

Build PS 06 end to end first (PRD §0, TRD §0); the blocks further down become phase 2.

| Step | Work | Accept |
|---|---|---|
| 1 | Storage, git ingest (renames, bot/sweep/generated/merge filters), read-only GitHub cache, in-repo docs, linking rules | `hindsight stats` prints counts computed from data, including bot share and links by provenance |
| 2 | Impact check (co-change + static service calls + same service + recency) with reasons; history-replay evaluation against baselines | `make eval` writes `eval/results/impact-*.json` with P@5, R@10, Hit@3, n and CI for every method |
| 3 | "Why" answers: blame path + keyword search, evidence pack, citation check, "not found" | One answerable and one unanswerable question shown with real output |
| 4 | Knowledge at risk; graph neighbourhood API | Single-owner files listed from data; graph returns provenance-labelled edges |
| 5 | Dashboard: Overview, Ask, Impact, Graph, Knowledge at risk, Scorecard | Every view loads against the ingested repo |
| 6 | README, docs, full verification | `make setup lint test` and `make eval` pass; results table generated from `eval/results/` |

Dropped: the pull-request check (no PRs raised, no GitHub integration). No repository is created
and nothing is pushed until the maintainer decides.

### Phase 2 progress (started 2026-10-31)

Built on top of phase 1, in this order (TRD §0.1 lists how each one differs from the blocks below):

| Block | Work | Accept | Status |
|---|---|---|---|
| A | Document upload (Markdown, PDF), versions, superseded docs, runbooks linked to services | Upload twice → `@v2`, old version leaves answers; impact lists the service's runbooks | done |
| B | LogSense on the local shop: collector, templates, detectors, culprit vs victim, incident → change, fault scripts, Incidents view | Injected fault → incident naming the right culprit; `make eval-rca` writes `eval/results/rca-*.json` | in progress |
| C | SentinelQA: tickets (from the repository's issues, PRs and commits) → tests, runner, healer with refusal rules, mutation harness | Cosmetic mutations healed, defect mutations refused, 0 false heals | built; live run pending |
| D | Smarter answers: local model, embeddings, meaning check | Answers still pass the citation check; abstention unchanged on unanswerable questions; `make eval-retrieval` compares keyword, semantic and hybrid search | built; live run pending |
| E | Neo4j graph layer (ADR 0001): sync, Cypher reads with SQLite fallback, Map badge, Neo4j Browser queries | Parity test: identical reads and outputs on both stores | done |
| G | Dashboard for non-technical and technical visitors: guided wireframe-runner tour (three.js) with six feature gates, badges, plain-language results with charts, incident story, 3D map, manual and glossary | Every page loads with real data; tour skippable; web tests pass | done |
| F | Same impact protocol on more public repositories (OpenTelemetry Demo, .NET eShop) | `make try-repo` writes their results into `eval/results/latest.md` | done |

## Phase 2: the 36-hour blocks

The plan assumes a team of 5 (P1–P5). With 4 people, P5's work is split between P2 and P4. With 3,
drop F7 (docs) and F9, and P1 also takes the dashboard.

## Roles

| Person | Owns | Stack |
|---|---|---|
| **P1 Brain** | History ingest, linking, co-change, blast radius, replay eval | Python |
| **P2 Answers + LLM** | LLM chain and cache, why-answers and validator, docs RAG-lite, question set | Python |
| **P3 LogSense** | Go collector, Drain3 templates, detectors, culprit ranking, incident summary, Discord | Go + Python |
| **P4 SentinelQA** | Tickets, generation, runner, healer, refusal rules, mutation harness | Python + Playwright |
| **P5 Integration** | kind + Online Boutique fork, fault scripts, deploy recorder, FastAPI glue, web UI, scorecard, demo | Bash + TS |

## Before the event (environment and data only; confirm the rules with the organisers in writing)

- [ ] Fork `GoogleCloudPlatform/microservices-demo`. Prepare a kind cluster and pre-pull the images on the strongest laptop.
- [ ] Run `make bootstrap` and `make doctor` on every laptop. Pull the Ollama models.
- [ ] Get API keys: Gemini (AI Studio), Groq, and a GitHub fine-grained read-only token. Set up a Discord webhook.
- [ ] Clone the target repo locally and pre-fetch PRs and issues into the cache.
- [ ] Each person reads PRD.md and their sections of DESIGN.md.

## Blocks

### B0 · H0–H2 · Contracts and skeleton (everyone)
- `core/schemas.py` (all TRD §3 models), `core/db.py` (TRD §4 schema), and FastAPI app skeleton with routers returning fixtures.
- `testdata/` fixtures for each schema. Web skeleton generating API types. Go collector skeleton.
- **Accept:** `make setup lint test` passes; `/docs` (OpenAPI) shows every endpoint; each person can build against fixtures. **Schemas freeze at H2.**

### B1 · H2–H12 · Modules standalone (in parallel)
- **P1:** ingest and linking on the real repo; filtering; co-change; scorer; `repo stats`.
- **P2:** provider chain and cache; retrieval (FTS5 + embeddings); evidence pack; validator; "not found".
- **P3:** collector → `/api/logs`; Drain3; detectors on recorded logs; culprit ranking on a hand-made fixture.
- **P4:** 2–3 tickets → generated tests validated 3/3; fingerprints; healer on one id rename.
- **P5:** cluster up with the load generator; fault scenarios in the fork (env-gated checkout error, button id rename); `make fault` and deploy recording; UI pages wired to fixtures.

### ✅ Checkpoint H12
Every module works on real or fixture data. Report what is green, what is red, and which fallback is active.

### B2 · H12–H20 · Integration (the chain)
- The "Speed up checkout" commit flows through the whole system: impact report (stored) → tests run (heal + refusal) → deploy → incident (culprit) → correlation → UI.
- P2: blame-path retrieval, NLI check, and the question set written (20 answerable + 10 unanswerable).
- P3: incident summary with validated citations.

### ✅ Checkpoint H20
**The end-to-end chain works once,** however ugly. If not, cut P1 features (F7, F9, graph view) immediately.

### B3 · H20–H26 · Evaluation + P1 features
- P1: history-replay evaluation with baselines.
- P4: mutation harness (≥ 20 cosmetic + ≥ 5 real-bug); false-heal count.
- P3: fault campaign (≥ 10 runs).
- P2: Q&A evaluation; docs RAG-lite (upload, runbooks, docs-to-update).
- P5: Scorecard page; Discord alert; graph view if time allows.

### ✅ Checkpoint H26 · FEATURE FREEZE
After this, only bug fixes, evaluation runs, UI polish and demo preparation.

### B4 · H26–H30 · Numbers and recordings
- Run `make eval` and freeze the results files.
- Record the demo data (logs, test runs) for `make demo-replay`.
- Cache all demo LLM responses (`LLM_CACHE_MODE=record`, then `replay`).

### ✅ Checkpoint H30
- The **backup demo video** is recorded.
- `make demo-replay` works with the network off.

### B5 · H30–H36 · Rehearse and package
- Rehearse the 3-minute demo three times (DEMO.md). Run the judge Q&A drill.
- README: setup, architecture diagram, results table generated from `eval/results/`, limitations, future ideas.
- Sleep in shifts.

## Cut lines (cut from the top when behind)

1. F9 knowledge at risk
2. Graph view
3. Discord alert
4. Docs upload (keep in-repo docs only)
5. Flaky detection
6. NLI model (use the LLM-judge fallback and say so)
7. **Never cut:** the chain, refusal-to-heal, culprit vs. victim, the evaluation numbers, replay mode

## Fallbacks

| Problem | Fallback |
|---|---|
| Cluster unstable | Run from recorded logs: `make demo-replay` |
| LLM quota exhausted | Replay cache, then Groq, then Ollama |
| Healer flaky on some mutation type | Restrict to the tested types; others return `needs_human` |
| GitHub rate limit | Cached JSON; git-only linking |
| Embeddings too slow locally | FTS5-only retrieval (report it) |
