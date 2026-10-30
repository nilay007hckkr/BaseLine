# ADR 0002: A second instance for your own repository

- Status: accepted (2026-10-31)
- Deciders: maintainer

## Context

The dashboard showed one repository (Online Boutique) and its demo shop. The maintainer asked for
one command that runs everything, plus a separate, fresh dashboard where a visitor enters their own
repository. Ingest, the graph and the evaluation all read one `DATA_DIR`/`DB_PATH`. Incidents and
tests depend on the demo shop's logs and browser, so they cannot follow an arbitrary repository.

## Options

1. **Multi-repository single instance:** a repository switcher in one process, every query scoped
   by repository. Touches every table and query.
2. **Second instance:** the same app started again on another port in `workspace` mode, with its
   own data folder per connected repository.
3. **Separate app:** a new frontend and backend for "your repo".

## Decision

Option 2. `scripts/run.sh` (`make run`) starts the demo on :8000 and a workspace on :8080.

- A workspace keeps each repository in `data/workspace/repos-by-name/<owner>__<name>/`; nothing
  mixes with the demo. It uses SQLite for the graph (Neo4j holds the demo).
- Connecting runs ingest, then the impact replay (same protocol as the demo) in one background
  thread; the dashboard polls `/api/workspace/status`.
- Pages that need the demo shop (Incidents, Tests) are hidden; Proof shows the replay on the
  connected repository, or says why it was not measured.
- The workspace's tour uses the demo's API (CORS), as chosen by the maintainer.
- Model choice moved into the dashboard, but keys stay in `.env` (AGENTS.md rule 7), as chosen by
  the maintainer: the API reports whether a key is set and never returns or accepts one.

## Consequences

- Two processes and two data folders; restarting keeps the connected repository (`active.json`).
- A job's progress lives in memory: a restart during a connect forgets the job, not the data.
- Large repositories without `GITHUB_TOKEN` get partial GitHub data (60 requests an hour); the next
  connect continues from the cache.
