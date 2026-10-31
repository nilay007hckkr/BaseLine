# Hindsight: PRD (INNOVISTA 36-hour edition)

_Updated 2026-10-31 to match what is built. Every number below names the file in `eval/results/`
it comes from. TRD.md describes how each part works._

## 0. Scope and status

PS 06 (Project Brain) was built first, end to end (phase 1, decided 2026-10-30). PS 04 (LogSense)
and PS 08 (SentinelQA) were then integrated on the same graph (phase 2, PLAN.md blocks A–I).

| Problem statement | What is built | Status |
|---|---|---|
| **PS 06 Project Brain** (base) | History ingest, one connected graph, cited "why" answers, impact check, Map, knowledge at risk | Done; impact measured by history replay |
| **PS 04 LogSense** | Log collector, Drain3 patterns, detectors, culprit vs victim, incident → change, incident brief, Discord alert | Built; one fault campaign run (8 of 10 faults detected); detector fixes pending (BACKLOG, block B) |
| **PS 08 SentinelQA** | Tickets → tests, 3/3 validation, browser runner, scored healer with refusals, repair review, mutation harness | Built; one mutation-harness run |
| Added | Document upload, Neo4j graph layer, semantic search, code structure layer, workspace mode for any public GitHub repository, guided tour, model settings, one-command start | Done |

**Not built, or built differently from the original plan:**
- the pull request check (no PRs are raised, no GitHub integration);
- a Go log collector (a Python collector runs instead);
- LLM-generated tests (a rule-based generator with a fixed grammar runs instead);
- an NLI model (an optional model support check, which can only remove claims, runs instead);
- Jira and Slack import (the target has neither; logged in BACKLOG).

**Constraints set by the maintainer:**
- Public or sample data only.
- GitHub is read-only for Hindsight: the public API, optional token, cached on disk, capped per run.
  Hindsight never writes to GitHub; the maintainer pushes the project's own repository.
- Everything runs locally: the dashboard, the API and the kind cluster with the demo shop.

**Users (PS 06):** new team members learning the codebase; developers and tech leads planning
changes and reviews; testers deciding what to re-check after a change.

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

## 4. Coverage of the three problem statements (as built)

| PS requirement | Feature as built | Status |
|---|---|---|
| **PS 06:** collect project history | F1: git history with renames and bot/sweep/merge/generated filters; GitHub issues, PRs and comments (read-only, cached); in-repo Markdown; uploaded documents (F7) | Done |
| PS 06: connected memory | One graph of commits, PRs, issues, comments, files, components and services, document sections, tests, people and functions. Every link stores how it was found and readable evidence | Done |
| PS 06: "why" Q&A with sources; say "not found" | F2: cited answers, citation check, abstention | Done |
| PS 06: impact check with a reason per item | F3: services, files, tests, docs, people and runbooks, each with reasons | Done, measured |
| PS 06 bonus: visual map | F8 Map (3D), plus the code structure layer (F11) | Done |
| PS 06 bonus: knowledge at risk | F9 Bus factor | Done |
| PS 06 bonus: pull request check | — | Dropped |
| **PS 04:** parse and cluster logs | F5: Python collector (`kubectl logs -f`), Drain3 patterns per service | Built |
| PS 04: spikes and new error types on a timeline | F5: new-pattern and spike detectors; Incidents timeline | Built |
| PS 04: LLM summary + root-cause service, with evidence | F5 culprit vs victim ranking; F10 incident brief (cited, must name the ranked culprit) | Built; the brief has not yet run with a working model |
| PS 04 bonus: live broken service, alerts | 10 scripted faults on the local shop; Discord webhook | Built |
| **PS 08:** ticket input (mock OK) | F4: YAML tickets that cite a real issue, PR or commit | Built |
| PS 08: generate, run, record | F4: rule-based generator, 3/3 validation, Playwright runner, run history | Built |
| PS 08: self-heal on locator change; log every repair | F4: scored healer; repair records with screenshots and a diff; approve, reject, revert | Built |
| PS 08: report passed / failed / repaired | Tests page: passed, healed, failed, needs human, flaky | Built |
| PS 08 bonus: flaky detection | A failure that passes on a re-run is labelled flaky | Built |

## 5. Features (as built)

Priority: **P0** = required for the demo, **P1** = if on schedule, **P2** = future ideas.

**F1. History ingest (P0) · done**
- Clones the target repository and reads `git log` with renames.
- Filters bot, sweep (more than 30 files), merge and generated-file commits from all statistics; the bot share is shown.
- Fetches issues, PRs and comments read-only, at most `FORGE_MAX_REQUESTS` (default 50) requests per run, cached on disk; the next run resumes.
- Links:
  - commit → PR from the `(#N)` title suffix, confirmed when GitHub's merge commit matches;
  - commit or PR → issue through closing keywords (stated) or other `#N` mentions (simple rule);
  - file → service by path: children of `src/`, `services/`, `microservices/` or `svc/`, and children of `app/`, `apps/` or `cmd/` that hold their own build or entry file (for example go-kratos beer-shop's `app/<service>/`); everything else groups by top-level folder;
  - service → service calls from client patterns in the source (read from code);
  - files that change together (learned from history).
- *Accept:* the Data page shows counts computed from data, including the bot share (65.5% on Online Boutique, `eval/results/latest.md`).

**F2. Grounded "why" answers (P0) · done**
- Evidence comes from the graph (the file, the service, `git blame` of the given lines), keyword search (SQLite FTS5) and, when an index exists, local semantic search. The three are fused, and at most 12 items go into the evidence pack.
- If the best evidence covers less than 60% of the question, the answer is "Not found in project history", with the 3 nearest items.
- The model returns JSON claims with citations. A claim survives only if it cites items it was shown and those items cover at least half of its words. An optional second model check can only remove claims.
- With no model, the answer quotes sentences from the evidence.
- Visitors can rate answers; the tally is shown as a spot check, not a benchmark.
- *Accept:* one answerable and one unanswerable question shown live (tour gates 1 and 2). Citation validity and abstention accuracy are **not** measured yet: no Q&A evaluation is built.

**F3. Impact check (P0) · done, measured**
- Score = 0.45 × changed together + 0.25 × service calls + 0.15 × same service + 0.15 × recency. These are default weights and were not tuned.
- Output: services, files, tests to run, docs that may need updating, people to ask and runbooks, each with reasons and how they were found.
- Checking a past commit uses only history from before that commit.
- *Accept:* history replay against baselines (EVALUATION §2). On Online Boutique (n = 74 held-out commits), R@10 is 0.611 [0.520, 0.699], against 0.581 for co-change only and 0.413 for same directory (`impact-20261030T185851Z.json`). The interval overlaps co-change only. The same protocol runs on .NET eShop and the OpenTelemetry Demo (`make try-repo`).

**F4. SentinelQA (P0) · built**
- Tickets: YAML files in `target/tickets/`, each citing its issue, PR or commit, written in a fixed Given/When/Then grammar.
- A rule-based generator writes the test. Quoted texts are located in the repository, together with the commit that introduced them. A test is kept only if it passes 3 of 3 runs on the clean shop, and element fingerprints are saved from the passing run.
- Runner rules:
  - a failed assertion, a backend 5xx or an RPC error is reported as **failed** and never healed;
  - an open incident on a service the test covers gives **needs human**;
  - two clean re-runs come first, and a pass there is labelled **flaky**.
- Healer: accepts a new locator only if its similarity is at least 0.75 and at least 0.15 above the runner-up. Every original assertion must then pass, or the heal is discarded.
- Repairs wait for review (approve, reject, revert), with before and after screenshots and a diff. Assertions are never edited.
- *Accept:* mutation harness (synthetic: 27 cosmetic and 8 defect page changes, applied in the test browser only). Heal rate 1.000 (n = 21), correct refusals 1.000 (n = 14), **false heals 0** (`heal-20261030T211918Z.json`).

**F5. LogSense (P0) · built**
- Python collector: `kubectl logs -f` per deployment with a 3-second reorder buffer. Every line is also saved for replay (`make demo-replay`).
- Drain3 log patterns per service, in 10-second windows.
- Detectors:
  - a new error pattern, after a warm-up, at warning severity or above or with at least 5 lines;
  - an error-rate spike (robust z ≥ 4 for 2 windows).
- Incidents merge within 120 s.
- Culprit vs victim: errors that name another service (by name, gRPC name or cluster IP) move blame to the named service, with a small bonus for services that callers depend on.
- Optional Discord alert.
- *Accept:* fault campaign (synthetic, n = 10): 8 detected, median time to detect 16.5 s, 0 false alerts per hour. Culprit top-1 and top-3 are both 0.500 [0.200, 0.800], against 0.000 for both baselines (`rca-20261030T205813Z.json`). That is below the 80% top-3 target; the fixes are listed in BACKLOG.

**F6. Incident → change correlation (P0) · built**
- Candidates are deploys in the 24 hours before the incident.
- Score = 0.4 × time decay (10-minute scale) + 0.3 × touches the culprit + 0.2 × culprit was in the change's impact prediction stored at deploy time + 0.1 × a changed file is named in the logs.
- Always labelled "candidate", a lead and not proof.
- *Measured:* Hit@1 1.000, against 0.250 for the most recent deploy (`rca-20261030T205813Z.json`). **Do not quote this yet:** the fault script picked the same release commit, which changes every service, as the "guilty" change in most runs. The fix and re-run are in BACKLOG.

**F7. Documents (P1) · done**
- In-repo Markdown, plus upload of Markdown or PDF (up to 20 MB).
- Heading-based sections, linked to the services and files they name.
- Runbooks (a repo path containing "runbook" or "playbook", or an upload of type Runbook) are shown on incidents and in impact reports.
- Uploading the same file name again makes a new version; the old version leaves answers.
- Instruction-like text is flagged and never sent to a model. Uploads stay on the machine unless `DOCS_ALLOW_EXTERNAL=true`.

**F8. Dashboard (P0) · done**
- Pages: Home, Ask, Impact, Map, Incidents, Tests, Proof, and More (Bus factor, Docs, The data, Settings, Manual, Profile).
- A skippable guided tour with six gates, each calling the live API.
- Achievements and levels.

**F9. Knowledge at risk (P1) · done**
- Lists files where one person made at least 80% of at least 3 counted changes. The owner is marked inactive after 365 days without a commit, counted back from the repository's latest commit.
- Per-component bus factor: the fewest people behind half of the changes.

**F10. Incident brief (P1, added) · built**
- Code gathers the evidence.
- The model may make up to 4 read-only lookups, each checked against the incident; out-of-scope calls are refused.
- Claims must cite evidence, and a claim that names a root cause must name the ranked culprit.
- Follow-up questions are supported. Falls back to quoted evidence.
- Not yet run with a working model.

**F11. Code structure layer (added) · done**
- graphify, run offline, reads functions, classes and their calls from the source.
- The Map shows them with file and line behind a switch.
- Impact scoring never reads this layer.

**F12. Workspace mode (added) · done**
- A second dashboard on :8080 (`make run` or `make start`) reads any public GitHub repository into its own folder and measures the impact check on its history.
- Incidents and Tests are hidden there, because they need the demo shop.

**F13. Model settings (added) · done**
- Choose the provider order and models in the dashboard.
- API keys stay in `.env` and are shown only as set or not set.

**Out of scope:** learned ranker, per-test coverage instrumentation, GitHub App, multi-tenant access
control, Confluence/Notion connectors, gRPC/protobuf between our components, Postgres/ClickHouse.
Neo4j is used as an optional read layer (ADR 0001).

## 6. The demo journey (as built)

1. **Home**, after the tour (skippable).
2. **Ask:** "Why was currencyservice rewritten in TypeScript?" gives a cited answer. "Why was paymentservice rewritten in Rust?" gives **Not found**.
3. **Impact:** `src/checkoutservice/main.go` shows the affected services, files, tests, docs and people, each with reasons.
4. **Map:** it opens on the repository's most linked service (`frontend` on Online Boutique); search `checkoutservice` to centre there. With **Code structure** on, functions and their calls appear with file and line.
5. **Incidents:** `make fault SCENARIO=payment-down DEPLOYS=1` while the log stream runs opens an incident. It shows paymentservice as the culprit and frontend as a victim, the candidate change, the runbook and the Brief. The fault and deploys are synthetic, and labelled so.
6. **Tests:** run the generated tests against the shop. Healing and refusal are shown by the mutation harness (Proof › Healing) and by any repairs waiting for review.
7. **Proof:** every number with its baseline, n, 95% range and reproduce command.

The original plan had one "Speed up checkout" commit drive every step. That was not built: faults
are scripted on the running shop, and page changes are applied in the test browser.

## 7. Success metrics (computed by `make eval`, `make eval-heal` and `make eval-rca`; targets are hypotheses)

| Metric | Target | Latest measured | Source |
|---|---|---|---|
| Blast radius R@10 vs. same-directory baseline | Beats the baseline | 0.611 vs 0.413 (co-change only 0.581), n = 74 | `impact-20261030T185851Z.json` |
| Citation validity / abstention accuracy | ≥ 95% / ≥ 90% | Not measured (no Q&A evaluation built) | — |
| Heal rate / **false heals** | ≥ 80% / **0** | 1.000 (n = 21) / **0**, synthetic | `heal-20261030T211918Z.json` |
| Culprit top-3 accuracy / median time to detect | ≥ 80% / ≤ 60 s | 0.500 (n = 10) / 16.5 s, synthetic | `rca-20261030T205813Z.json` |

## 8. Risks

| Risk | Mitigation |
|---|---|
| The live cluster misbehaves on stage | `make demo-replay` from recorded logs; backup video at H30 |
| LLM quota or outage | Gemini → Groq → Ollama chain, SQLite record/replay cache, 5-second connect timeout, quoted evidence when no model answers |
| Scope overrun (three PSs in 36 h) | Hard checkpoints and cut lines in PLAN.md |
| Healer hides a real bug | Assertions untouchable; evidence-based refusal; false-heal metric |
| Noisy history (bots) | Classification filters; bot share shown openly |
| The model's wording blames the wrong service | Code ranks the culprit; claims naming another root cause are removed |
| The correlation number is flattered by the fault script's choice of release commits | Exclude release and sweep commits when picking the guilty change, then re-run (BACKLOG) |
| Small samples (10 faults, 74 commits) | Every number shows n and a 95% range |
