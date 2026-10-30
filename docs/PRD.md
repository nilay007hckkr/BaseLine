# Hindsight: PRD (INNOVISTA 36-hour edition)

## 0. Current scope: phase 1 is PS 06, Project Brain (decided 2026-10-30)

We build PS 06 first, end to end, and then integrate the rest of this PRD on top of it (phase 2).

| PS 06 item | Feature | Phase 1 |
|---|---|---|
| Project history collection | F1: git history of a public repo (Online Boutique), its public GitHub issues, PRs and comments (read-only, cached), and its in-repo Markdown docs | yes |
| Connected memory | One graph linking commit ↔ PR ↔ issue ↔ discussion ↔ file ↔ service ↔ doc ↔ person, with provenance and evidence on every link | yes |
| Question answering with sources | F2: every claim cites a commit, PR, issue, comment or doc; otherwise "Not found in project history" | yes |
| Impact check with a reason per item | F3: affected files, services (components), docs, tests to re-check and people to ask, each with a reason | yes |
| Bonus: visual map | F8 graph view | yes |
| Bonus: knowledge at risk | F9: code only one person has worked on | yes |
| Bonus: pull request check | **Dropped**: no PRs are raised and there is no GitHub integration | no |

Also in phase 1, from this PRD: bot and sweep filtering with the bot share shown; a history-replay
evaluation of the impact check against baselines (scorecard); instruction-like text in ingested
content is flagged and never followed.

**Users (PS 06):** new team members learning the codebase; developers and tech leads planning
changes and reviews; testers deciding what to re-check after a change.

**Constraints set by the maintainer:**
- Public or sample data only.
- GitHub is used read-only: the public API, no token required, cached on disk and time-boxed.
  Nothing is pushed, no repository is created and no PRs are raised.

**Deferred to phase 2:** F4 SentinelQA, F5 LogSense, F6 incident → change correlation, F7 document
upload, and the kind cluster. Sections 1–8 below describe the full product.

## 1. One-liner

**Given a code change: what it will break, which tests to run, and which change caused the incident.**
Every answer is backed by evidence you can click, and every capability is measured against a baseline.

## 2. Problem

- **Context is scattered.** Commits, PRs, issues, docs, tests and logs live in different tools. When seniors leave, the "why" leaves with them (PS 06).
- **Blast radius is guessed.** Reviewers can't see the logical coupling that static analysis misses.
- **UI tests are fragile.** They break on cosmetic changes, and naive self-healing hides real bugs (PS 08).
- **Incident triage blames the wrong service.** The service that logs the most errors is usually a victim (PS 04).

All of these need the same linked history of change, so we build **one graph** and answer every question from it.

## 3. Users

| User | Job to be done |
|---|---|
| PR reviewer (primary) | "Before I approve, what could this break, and did the right tests run?" |
| New contributor | "Why is this code like this? Show me the sources." |
| On-call engineer | "Which service is the culprit, which change likely caused it, and what's the runbook?" |
| QA engineer | "Turn acceptance criteria into tests; heal cosmetic breaks; never hide real bugs." |

## 4. Coverage of the three problem statements

| PS requirement | Our feature |
|---|---|
| **PS 06:** collect project history | F1 history ingest (commits, PRs, issues, reviews) + in-repo and uploaded docs |
| PS 06: connected memory | One graph; provenance + evidence on every edge |
| PS 06: "why" Q&A with sources; say "not found" | F2 grounded answers with a citation validator and abstention |
| PS 06: impact check with a reason per item | F3 blast radius (services, files, tests, docs) with reason strings |
| PS 06 bonus: visual graph, knowledge at risk | F8 graph view; F9 bus-factor badge (P1) |
| **PS 04:** parse and cluster logs | F5 Drain templates per service |
| PS 04: spikes and new error types on a timeline | F5 detectors + incident timeline |
| PS 04: LLM summary + root-cause service, with evidence | F5 culprit-vs-victim ranking; summary cites template IDs and log lines |
| PS 04 bonus: live broken service, alerts | Live faults on Online Boutique; Discord alert |
| **PS 08:** ticket input (mock OK) | F4 YAML tickets (Jira optional) |
| PS 08: generate, run, record | F4 Playwright generation, validated by running |
| PS 08: self-heal on locator change; log every repair | F4 scored healer + repair records with diffs and screenshots |
| PS 08: report passed / failed / repaired | F4 test run report |
| PS 08 bonus: flaky detection | F4 rerun-based flaky label (P1) |

## 5. Features

Priority: **P0** = required for the demo, **P1** = if on schedule, **P2** = future-ideas slide.

**F1. History ingest (P0)**
- Clone the target repo and parse the git log with renames.
- Fetch PRs, issues and reviews once and cache them.
- Link commit→PR (title suffix `(#N)`, confirmed via API), PR→issue (closing keywords) and file→service (path mapping).
- Classify bot, sweep, generated and merge commits.
- *Accept:* stats are printed from data: commit count, bot share, link counts by provenance.

**F2. Grounded "why" answers (P0)**
- Retrieve along the blame path, then hybrid search (FTS5 + embeddings), to build an evidence pack of at most 12 spans.
- The LLM returns JSON claims with citations.
- The validator drops invalid or unsupported claims, or answers "Not found in project history" plus the 3 nearest items.
- *Accept:* one answerable and one unanswerable question shown live; citation validity reported (EVALUATION §3).

**F3. Blast radius (P0)**
- Score = co-change (ROSE-style, filtered) + static gRPC call graph from `protos/demo.proto` + same-service + test coverage links + doc mentions.
- Output: services, files, tests to run, and docs that may need updating, each with a reason built from edge evidence.
- *Accept:* history-replay P@5 / R@10 vs. baselines (EVALUATION §2).

**F4. SentinelQA (P0)**
- Generate a Playwright test from YAML acceptance criteria; keep it only if it passes 3/3 on a clean build.
- Save element fingerprints from the passing run.
- Healer: rerun to rule out flakiness → Similo-style weighted score → accept only above threshold θ and margin δ → re-run the original assertions.
- **Refuse** (genuine failure) when an assertion fails, network calls return 5xx or gRPC errors, or an incident is open on a covered service.
- Every repair is logged with old and new locator, the score breakdown, screenshots and a patch.
- *Accept:* heal rate and **false heals = 0** on the mutation harness (EVALUATION §4).

**F5. LogSense (P0)**
- The Go collector normalises logs, and Drain3 mines templates per service.
- Detectors: new error template, and error-rate spike (robust z-score).
- Culprit ranking downweights victims: callee named in the error, call-graph direction, template novelty.
- Incident summary cites template IDs and sample lines; Discord webhook alert (P1).
- *Accept:* top-1 / top-3 culprit accuracy and time to detect over injected faults (EVALUATION §5).

**F6. Incident → change correlation (P0)**
- Candidate commits are taken from deploys before the incident.
- Each is scored on time proximity, whether it touches the culprit service, whether the culprit was in the commit's **stored** impact prediction, and code overlap.
- Always labelled "candidate".

**F7. Docs RAG-lite (P1)**
- In-repo Markdown plus PDF/Markdown upload.
- Heading-based chunks with section path; exact-name `MENTIONS` links to services and symbols.
- Runbook sections are shown on incidents; "docs that may need updating" appear in impact reports.
- Uploaded docs are untrusted: instruction-like text is flagged.

**F8. Dashboard (P0)**
- Views: Change (impact + tests + docs), Ask, Tests (runs + repair review), Incidents (timeline + culprit + candidate change + runbook), Scorecard, Graph (P1).

**F9. Knowledge at risk (P1).** Files dominated by one author who is no longer active.

**Out of scope:** learned ranker, per-test coverage instrumentation, GitHub App, multi-tenant access control, Confluence/Notion connectors, gRPC/protobuf, Postgres/ClickHouse. These go on the future-ideas slide.

## 6. The demo journey (one commit drives everything)

1. Commit "Speed up checkout" renames the Place Order button's id (cosmetic) **and** plants a real bug in checkoutservice (env-gated in our fork).
2. **Impact:** checkoutservice, paymentservice and frontend; 3 tests to run; Checkout runbook section flagged.
3. **SentinelQA:** the rename is **healed** (score breakdown + diff); place-order is a **GENUINE FAILURE, healing refused**.
4. **LogSense:** a new error template and a spike → incident; **checkoutservice = culprit, frontend = victim**; runbook shown; Discord alert.
5. **Correlation:** the incident is linked to the commit as the top candidate, with evidence.
6. **Ask:** "Why does checkout charge in USD?" → cited answer. An unanswerable question → "Not found".
7. **Scorecard:** every number has a baseline, n, and a reproduce command.

## 7. Success metrics (computed by `make eval`; targets are hypotheses)

| Metric | Target |
|---|---|
| Blast radius R@10 vs. same-directory baseline | Beats the baseline |
| Citation validity / abstention accuracy | ≥ 95% / ≥ 90% |
| Heal rate / **false heals** | ≥ 80% / **0** |
| Culprit top-3 accuracy / median time to detect | ≥ 80% / ≤ 60 s |

## 8. Risks

| Risk | Mitigation |
|---|---|
| The live cluster misbehaves on stage | `make demo-replay` from recorded logs and test runs; backup video at H30 |
| LLM quota or outage | Gemini → Groq → Ollama chain + SQLite record/replay cache |
| Scope overrun (three PSs in 36 h) | Hard checkpoints and cut lines in PLAN.md |
| Healer hides a real bug | Assertions untouchable; evidence-based refusal; false-heal metric |
| Noisy history (bots) | Classification filters; bot share shown openly |
