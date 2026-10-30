# Hindsight: Demo script (3 minutes) and judge Q&A

**Setup on stage:** cluster running with the load generator, dashboard open on the Change view, Discord visible.
**Fallback:** `make demo-replay`, plus the backup video.

| Time | Action | What to say |
|---|---|---|
| 0:00–0:20 | Title slide → Overview | "When seniors leave, the *why* leaves. Evidence about a change is scattered across git, PRs, docs, tests and logs. We connected it into one graph, on Google's own Online Boutique." Show repo stats, including the bot share we filter out. |
| 0:20–0:45 | **Ask** | "Why does checkout charge in USD?" → claims with citation chips; click one to open the PR. Then an unanswerable question → **Not found**. "It admits ignorance instead of guessing." |
| 0:45–1:15 | **Change** | Select the "Speed up checkout" commit → affected services with reasons, 3 of N tests to run, a runbook section flagged as possibly stale. |
| 1:15–1:50 | **Tests** | Run the selected tests. The renamed button is **healed** (score breakdown + diff, awaiting approval). place_order is a **GENUINE FAILURE: healing refused**, with the reasons shown. "Self-healing that hides bugs is worse than no tests." |
| 1:50–2:30 | **Incidents** | Deploy → Discord alert → incident: **checkoutservice = culprit, frontend = victim**, with template evidence; runbook steps; the candidate commit is the one we just reviewed. |
| 2:30–3:00 | **Scorecard** | Blast radius vs. baseline, false heals = 0 of N real bugs, culprit top-1/top-3, citation validity, each with n and a CI. "One graph: what breaks, what to test, what caused it, and how often we're right." |

## Judge Q&A

- **"Isn't co-change just noise?"** Raw co-change is noisy. We filter bots and sweeps, fuse it with the static gRPC graph, and show it beating the same-directory baseline on held-out history. The numbers are on the scorecard.
- **"How do you know the healer doesn't hide bugs?"** It never touches assertions, refuses when there is backend error evidence, needs a score margin, and every repair waits for human approval. We measured false heals on injected real bugs.
- **"Why not Playwright's own healer?"** It's an LLM agent loop. Ours is deterministic and scored, with logged repairs and a measured false-heal rate.
- **"Victim vs. culprit?"** The frontend logs the most errors, but its messages name the failing callee, and the call graph points downstream. We measured top-1/top-3 accuracy over injected faults.
- **"What if the LLM hallucinates?"** It never decides anything. Citations are validated against the evidence pack in code; unsupported claims are dropped, and it abstains when nothing survives.
- **"Is the incident→commit link causal?"** No. It's ranked correlation, labelled as a candidate, with its evidence.
- **"How is this different from Falconer, Glean or other knowledge tools?"** They answer what the docs say. We predict what a *change* breaks, using runtime evidence, and publish our accuracy.
- **"Does it scale?"** The graph already runs in Neo4j (`make graph-up graph-sync`), with SQLite as the store ingest writes to; both are tested to answer identically. SQLite → Postgres for the tables is a mechanical swap. The same evaluation ran on two more public repositories (OpenTelemetry Demo, eShop).

## Future ideas slide
- Learned impact ranker
- Per-test coverage for test selection
- GitHub App with PR checks (shadow → advisory → gating)
- OpenTelemetry traces for root-cause analysis
- Confluence/Notion connectors
- Contradiction detection between docs and code
- MCP server for coding agents
- Postgres/ClickHouse at scale
