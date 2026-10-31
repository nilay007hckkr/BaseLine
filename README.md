# Hindsight

**A project's delivery memory.** Ask *"why was this built this way?"* and *"what will this change
affect?"* and get answers backed by the project's real history, with every source linked, or an honest
"not found".

Built for INNOVISTA **PS 06: Project Brain**. The demo data is Google's public
[Online Boutique](https://github.com/GoogleCloudPlatform/microservices-demo) repository. LogSense and
SentinelQA (PS 04 / PS 08) were built in phase 2 and are now frozen: hidden on the demo, still in the
code (`SHOW_FROZEN=true` shows them).

## What it does

| You want to… | Hindsight gives you |
|---|---|
| See the project's history in one place | Commits, pull requests, issues, discussions, people and docs, linked into one map. Bot and bulk commits are filtered out and counted openly |
| Ask why code is the way it is | Answers built only from that history. Every sentence cites its commit, PR, issue, comment or doc; if the evidence isn't there, it says **not found** and shows the closest items |
| Know what a change will affect | Files, services, tests to re-check, docs that may need updating and people who know the code, each with a reason taken from history |
| See connections | An interactive map of code, tickets, people and docs; every link shows how it was found |
| Spot knowledge at risk | Code that only one person has worked on, and whether that person is still active |
| Check a pull request | The impact summary of an open PR (or a local branch) before it is merged, as a paste-ready comment; nothing is posted |
| Know which docs went stale | Docs whose subject kept changing after their last edit, with the commits as evidence |
| Trust the numbers | Answers are scored on questions generated from the project's own "Fixes #N" links; the impact check is replayed on past commits; both against simple baselines |

Phase 2 additions (in progress; see [docs/PLAN.md](docs/PLAN.md)):

| You want to… | Hindsight gives you |
|---|---|
| Add your own docs | Upload Markdown or PDF. A new upload with the same title becomes the next version; the old one stops being used in answers. Runbooks are linked to the services they mention and shown in impact reports |
| Know what broke | LogSense watches the demo shop's logs, groups lines into templates, and opens an incident on a new error pattern or a spike. It separates the service that broke from the services that only suffered |
| Know which change did it | Each incident lists recent deploys ranked by timing, whether they touched the broken service, and whether the impact check had predicted it, with the reason shown |
| Test the shop from real tickets | Tickets cite the issue, PR or commit they come from (e.g. issue #3578: the cart keeps items after an order). Each becomes a browser test; every quoted UI text is traced to the template line and commit that introduced it. When a button is renamed or restyled, the test heals itself; when something is really broken, it fails and says why. Repairs wait for a person to approve |
| Better answers, still checked | Semantic search with a local embedding model finds evidence that uses different words. A local model (Ollama) can phrase answers, and a second check removes any claim its sources do not support. Nothing leaves the machine |

The dashboard opens with a short guided run (skippable at any time): a wireframe ship flies a
vector grid and stops at six gates, each explaining one feature with a live example. Then come a
minimal home page, one page per feature (sub-pages instead of long scrolls), plain-language results
and a manual; every technical word has a plain explanation on hover.

Requirement-by-requirement mapping: [docs/PS06.md](docs/PS06.md).

## Quick start (WSL Ubuntu or Linux)

One command, after `make bootstrap` has installed the tools once:

```bash
make start         # or: bash scripts/start.sh [--live] [--dev] [--no-neo4j] [--skip-setup]
```

It installs the dependencies, ingests Online Boutique on the first run, starts Neo4j when Docker
is running, builds the Map's code layer when graphify is installed, streams the local shop's logs
into Incidents when its cluster is running (`--live` creates it), then serves the demo on
http://localhost:8000 and the workspace on http://localhost:8080. Missing optional parts are
skipped with a note. Ctrl-C stops the dashboards and the log stream; `make graph-down` and
`make target-down` stop Neo4j and the cluster. With make, pass the switches as `LIVE=1`, `DEV=1`,
`NO_NEO4J=1` or `SKIP_SETUP=1`.

The same steps one at a time:

```bash
make bootstrap     # installs or verifies tools; asks before any sudo step
make setup         # backend and web dependencies
make ingest        # clone + read the history of Online Boutique (or REPO=<github url>)
make eval          # measure the impact check → eval/results/
make dev           # API on :8000 + dashboard on http://localhost:5173
make run           # one command, two dashboards: the demo on :8000, your own repo on :8080
```

`make run` serves the demo (Online Boutique) on http://localhost:8000 and an empty **workspace**
on http://localhost:8080: paste a public GitHub URL, and Hindsight reads that repository into its
own folder (`data/workspace/`), then measures the impact check on its history. The onboarding run
plays while it reads, using the demo's examples. Incidents and Tests need the demo shop, so the
workspace leaves them out. **More › Settings** picks the model provider order and model names;
API keys stay in `.env` and are never shown or typed in the browser.

Optional graph database: `make graph-up && make graph-sync` copies the graph into Neo4j (Docker). The Map,
the "why" path and impact structure then read it with Cypher, and the Neo4j Browser on
http://localhost:7474 shows the whole graph ([docs/GRAPH.md](docs/GRAPH.md)). Without it, the same
features read SQLite.

Optional code structure: `uv tool install graphifyy==0.9.99`, then `make codegraph` adds the
functions and classes in each code file, and the calls between them, to the Map (switch on **Code
structure**). It parses the code offline with tree-sitter; every link names the file and line it
came from, and impact scoring does not use it.

`make serve` builds the dashboard and serves everything from http://localhost:8000. The CLI works too:
`cd backend && uv run hindsight ask "Why does checkoutservice convert currency to USD?"`.

Incident triage needs the local demo shop (Docker, kind and kubectl; a few GB of images):

```bash
make target-up                          # kind cluster + Online Boutique → http://localhost:8081
make collect                            # stream the shop's logs into LogSense (Ctrl-C to stop)
make fault SCENARIO=payment-down DEPLOYS=1   # break one service on purpose; records synthetic deploys
make fault-revert SCENARIO=payment-down
make eval-rca RUNS=10                   # repeat inject → detect → revert and score it (slow, ~2 h)
make qa-generate qa-validate qa-run     # tickets → browser tests (3/3 on the healthy shop) → run
make eval-heal                          # mutation harness: heals, refusals, false heals (synthetic)
```

Local models (optional, [Ollama](https://ollama.com)): `ollama pull nomic-embed-text` and
`ollama pull gemma3:4b`, then
`make embed` builds the semantic index and `make eval-retrieval` measures keyword vs semantic vs
hybrid search. Set `OLLAMA_CHAT_MODEL=gemma3:4b` in `.env` to let the local model phrase answers.

No keys are needed. Optional settings in `.env`:
- `GITHUB_TOKEN`: faster GitHub reads. Without it GitHub allows 60 requests an hour, so issue and PR text
  arrives over several `make ingest` runs (pages are cached).
- `GEMINI_API_KEY`/`GEMINI_MODEL`, `GROQ_API_KEY`/`GROQ_MODEL` or `OLLAMA_CHAT_MODEL`: lets a language
  model phrase answers. It only rewords evidence that the code has already chosen, and every sentence
  must pass the citation check. Without a model, answers quote their sources directly.

## How it works

- **History:** `git log` with renames followed; bot, bulk (more than 30 files), merge and generated-file
  changes are excluded from statistics. Public GitHub issues, PRs and comments are read once,
  read-only, and cached. Markdown docs are split by heading.
- **Memory:** one graph. Every link stores *how it was found* (stated in the data, confirmed by GitHub,
  read from the code, computed from history, or a simple rule) plus the evidence behind it. Ingest
  writes it to SQLite; `make graph-sync` copies it into Neo4j, which then answers the graph walks.
  Both stores are tested to give identical answers.
- **Why answers:** start from what the question names: a file or service, a commit hash, or a
  function or class (its lines go through `git blame`); follow commit → pull request → issue →
  discussion, add keyword search, and keep only evidence that covers the question. Rare question
  words that appear nowhere in the history lead to "not found". PRs and issues opened by bot
  accounts are never evidence.
- **PR check and stale docs:** the same impact check on a pull request's changed files; a doc is
  flagged when what it describes changed 3+ times after its last edit. Neither uses a model.
- **Impact:** files that changed together in the past, service-to-service calls read from the code,
  same service, and how recent those changes were. Only history from before a change is used.

## Results

Generated by `make eval` and `make eval-ask` into [eval/results/latest.md](eval/results/latest.md);
never typed by hand. The same file reports the impact protocol on two more public repositories
(OpenTelemetry Demo and .NET eShop), to check that it is not fitted to Online Boutique:
`make try-repo REPO=<github url>` reproduces them. Protocols: [docs/EVALUATION.md](docs/EVALUATION.md).

## Limitations

- Answer quality depends on what the history records. Commit messages are short, so the richest
  answers come from PR and issue text, which arrives gradually without a GitHub token.
- The answer check is keyword-based. It verifies that cited text exists and covers the claim's key
  words; it is not a full meaning check.
- Answer scoring covers questions the history links explicitly ("Fixes #N"), n = 25. When the question
  names only the file and the change (not the commit), the issue behind it is cited in 24% of answers:
  busy files share words with many commits. Trick questions: 9 of 12 answered "not found" (a set the
  rule was fixed on, so in-sample). All numbers so far are for quoted answers, not model-written ones.
- Impact weights are defaults, not tuned. The gain over "co-change only" is small and its confidence
  interval overlaps; the gain over the directory and frequency baselines is clear.
- The PR check is read-only: nothing is posted to GitHub (copy and paste by design).
- "May be out of date" is a heuristic (3+ commits changed what a doc describes after its last edit),
  not a verdict.
- Incident triage is measured on faults we inject into a local demo shop, with synthetic deploys.
  Those numbers show the method works on a controlled system; they are not production results.

## Documentation
[PS 06 mapping](docs/PS06.md) · [PRD](docs/PRD.md) · [TRD](docs/TRD.md) · [Design](docs/DESIGN.md) ·
[Graph in Neo4j](docs/GRAPH.md) · [ADRs](docs/adr/) ·
[Plan](docs/PLAN.md) · [Evaluation](docs/EVALUATION.md) · [Setup](docs/SETUP.md)
