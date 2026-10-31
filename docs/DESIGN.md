# Hindsight: Design (36-hour edition)

Section numbers are referenced from the code and the other docs. Keep them stable.

## 1. Graph and linking

**Ingest order:**
1. `git clone`
2. PyDriller walk, with renames
3. GitHub fetch for PRs, issues and reviews (cached JSON under `data/forge/`)
4. Linking
5. Classification
6. Co-change edges

**Classification** (node attributes):
- `is_bot`: the author matches a bot pattern (`renovate`, `dependabot`, `[bot]`).
- `is_sweep`: the commit touches more than 30 files.
- `is_generated`: the path is generated (`*.pb.go`, `*_pb2*.py`, `genproto/`, lockfiles, `go.sum`).
- `is_merge`: the commit has more than one parent.

All four are excluded from statistics.

**Linking rules, with the provenance each produces:**

| Edge | How | Provenance | Evidence example |
|---|---|---|---|
| Commit→PR | Title suffix `(#N)`, confirmed by the API when available | heuristic / api | `{"via":"title_suffix","text":"(#812)"}` |
| PR→Issue | `fixes/closes/resolves #N` in the PR body | explicit | `{"keyword":"Fixes","span_id":41}` |
| PR→Issue | A plain `#N` mention | heuristic | `{"text":"see #790"}` |
| Commit→File | From the diff | explicit | `{"additions":12,"deletions":3}` |
| File→Service | `src/<service>/**` path mapping | heuristic | `{"rule":"path_prefix","pattern":"src/checkoutservice/"}` |
| Service→Service | gRPC clients and `protos/demo.proto` | static | `{"method":"PaymentService/Charge"}` |
| File↔File | Co-change (§2.1) | statistical | `{"support":9,"confidence":0.64,"last_commit":"a1b2c3d"}` |
| DocChunk→Service/Symbol | Exact name match | heuristic | `{"surface":"PlaceOrder","section":"4.2"}` |
| Commit→Author (`AUTHORED_BY`) | Git author (mailmap applied) | explicit | `{"name":"Ana","is_bot":false}` |
| DocChunk→Doc (`PART_OF`) | The heading section a chunk was cut from | explicit | `{"section":"Payment failures","ordinal":2}` |
| Doc→Doc (`SUPERSEDES`) | An upload with the same title as an earlier one | explicit | `{"prev_version":1,"next_version":2,"uploaded_at":1793380000}` |
| DocChunk→Service (`RUNBOOK_FOR`) | A runbook's section that mentions the service | heuristic | `{"rule":"doc_type=runbook and MENTIONS","section":"Cart empty"}` |
| Test→Issue/PR/Commit (`DERIVED_FROM`) | The ticket's `source` field | explicit | `{"ticket":"target/tickets/OB-3578.yaml","field":"source"}` |
| Test→Service (`COVERS`) | The ticket's `services` field | explicit | `{"ticket":"target/tickets/OB-3578.yaml","field":"services"}` |
| Test→File (`EXERCISES`) | A quoted UI text found in the repository (`git grep -w -F`), with the commit that introduced it (`git log -S`) | static | `{"text":"Place Order","file":"src/frontend/templates/cart.html","line":213,"introduced_by":"f362d2b2…"}` |
| Incident→Service | Culprit ranking | runtime | `{"rank":1,"templates":["T17"]}` |
| Incident→Commit | Correlation | statistical | `{"gap_s":38,"in_blast_radius":true}` |

**Spans** are the citable text units: commit messages, PR bodies, issue bodies and comments, review
comments, doc chunks and log samples. Each has a URL back to its source.

## 2. Blast radius

### 2.1 Co-change
- Count pair co-occurrences over filtered commits.
- `confidence(a→b) = n_ab / n_a`. Keep a rule only when `support ≥ 2`.
- Apply time decay with a 365-day half-life: each commit's weight is `0.5^(age_days/365)`.

### 2.2 Score
For a change C (its changed files) and a candidate file f:

```
score(f) = 0.45 · max_{c∈C} conf(c→f)
         + 0.25 · static(f)        # 1 if f's service is called by / calls a changed service (proto graph), 0.5 at 2 hops
         + 0.15 · same_service(f)  # 1 if f is in a changed service
         + 0.15 · recency(f)       # decay of f's last co-change with C
```

- Weights live in `config` and are tuned on the **validation** split only (EVALUATION §2). Report the final weights.
- Files roll up to services (max score) and tests (any covered route touches an impacted service).
- Docs are flagged when a chunk `MENTIONS` a changed symbol or service.

### 2.3 Reasons
Each reason is a template filled from edge evidence, for example:
- "changed together in 9 of 14 commits (last: a1b2c3d)"
- "PaymentService.Charge is called by checkoutservice"
- "§4.2 mentions PlaceOrder"

The LLM never writes reasons.

## 3. Grounded "why" answers

1. **Anchor:** a file, line range or symbol if given; otherwise names extracted from the question by lexical match against file and service names.
2. **Blame path:** `git blame` the anchored lines, then follow commit → PR → issue → reviews.
3. **Hybrid search:** FTS5 BM25 plus embedding cosine over spans in the anchor's 2-hop neighbourhood, merged with Reciprocal Rank Fusion. Fall back to global search, marked as such.
4. **Evidence pack:** at most 12 spans and about 3k tokens, numbered S1…Sn.
5. **Generation:** JSON only: `{"claims":[{"text":"…","cites":["S3"]}]}`.
6. **Validator (code):**
   - drop citations not in the pack, and drop claims left uncited;
   - run NLI between each claim and its cited spans, and keep the claim only if one span entails it (≥ τ);
   - if nothing survives, or the top retrieval score is below τ_ret, answer `not_found` with the 3 nearest items.
7. Thresholds are calibrated on the question set's validation half and recorded in `eval/results/`.

**As built (phase 2):** semantic search covers all spans (not only the anchor's neighbourhood) and
joins the RRF with the graph path and BM25; `not_found` is still decided by the IDF-weighted lexical
coverage of the best evidence (τ = 0.6), so adding semantic search cannot loosen abstention. The NLI
step is a model support check: one call judges every surviving claim against its cited text, and
a claim without a clear "supported" verdict is removed. Retrieval is measured automatically by
`make eval-retrieval` (find the issue a PR or commit links to from its title alone, as of its time);
the answer-level question set of EVALUATION §3 is still to be written by the team.

**Subject check:** when the question names a service or file (and gives no line range), evidence
must be about it: either reached from it in the graph (its commits, their PRs and issues, docs
that mention it), or containing one sentence that names it and another key word of the question.
Without this, a false-premise question ("Why was paymentservice rewritten in Rust?") was answered
from unrelated text that happened to share its rare words ("no Rust toolchain" in a load-generator
PR, "currencyservice has been rewritten in TypeScript").

**Live spot checks:** on the Ask page, visitors rate the answer to their own question (backed by
its sources or not; "not found" right or not). The tally is shown with n and labelled as a spot
check, not a benchmark (`GET/POST /api/answer-checks`).

## 4. Docs RAG-lite

- **Sources:** in-repo `README*` and `docs/**` (versioned by git), plus uploads (PDF, Markdown) with an optional `doc_type` (`runbook | design | spec`).
- **Parse:** headings, paragraphs, tables and code blocks. Chunk at headings, aiming for 200–600 tokens. Tables and code blocks are never split. Each chunk stores its `section_path`.
- **Link:** match chunks against a dictionary of service names, file paths, symbols and endpoints → `MENTIONS`. A runbook chunk that mentions a service also gets `RUNBOOK_FOR`.
- **Version:** re-uploading a doc creates a new version; citations show `doc@v §section` and the doc's age.
- **Safety:** flag instruction-like passages (e.g. "ignore previous instructions"). Flagged chunks stay citable but are marked, and are never used as test-generation context.

## 5. SentinelQA

### 5.1 Tickets
`target/tickets/*.yaml`:

```yaml
id: SHOP-42
title: Customer can place an order
acceptance_criteria:
  - Given a cart with one product
  - When the customer clicks "Place Order"
  - Then an order confirmation with an order ID is shown
```

### 5.2 Generation
- The LLM fills a fixed Playwright template. Locator policy, in order of preference: role+name, label, test-id, text. CSS is a last resort and is flagged.
- Assertions must map to "Then" lines; a lint rejects untraceable assertions.
- Each test is validated 3/3 on a clean build and rejected otherwise.
- **Fingerprints** are saved for every located element: tag, role, accessible name, id, classes, text, placeholder, test-id, parent signature, neighbour texts and a normalised bounding box.

### 5.3 Heal (on a locator failure only)
1. Re-run up to 2 times. If it passes, label the test `flaky` and stop.
2. Collect candidates: interactable elements with a compatible role.
3. Score each one: `Σ w_i · sim_i`. Use exact or normalised-edit similarity for strings, Jaccard for classes and neighbours, and distance for the box.
4. Accept only if `top ≥ θ` (default 0.75) and `top − second ≥ δ` (default 0.15).
5. Re-run the step **and every original assertion**.
6. Write a `Repair`: old and new locator, per-feature breakdown, screenshots before and after, a unified-diff patch, and status `proposed`. A human approves it in the UI.

### 5.4 Refusal rules (any one → `genuine_failure` or `needs_human`)
- An assertion failed. **Assertions are never modified.**
- A network response during the step had status ≥ 500, or a gRPC error surfaced in the page or console.
- LogSense has an open incident on a service this test touches.
- No candidate passes θ and δ.
- Assertions fail after the heal.

## 6. LogSense

### 6.1 Collector (Go)
- Runs `kubectl logs -f --timestamps` per deployment.
- Parses JSON when possible (`timestamp`/`time`, `severity`/`level`, `message`/`msg`) and falls back to the raw line.
- Emits `{ts, service, pod, severity, message, raw}`.
- POSTs batches to `/api/logs` and also appends to `data/logs/<date>.jsonl`.

### 6.2 Templates
Drain3 per service, fed `message` only, with masks for IPs, UUIDs, hex, numbers and emails. Templates
are stored with their first-seen and last-seen times.

### 6.3 Detectors (10 s windows per service × template)
- **New template:** first seen after a 10-minute warm-up, with severity ≥ warn or ≥ 5 occurrences.
- **Spike:** robust z-score against a rolling median and MAD, alerting when z ≥ 4 for 2 consecutive windows.

Anomalies within 2 minutes of each other merge into one incident.

### 6.4 Culprit vs. victim
For each anomalous service s:

```
base(s)    = normalised error-rate increase + 0.5 · novelty(new error templates)
callee(s)  = mass moved to service t when s's error messages name t (e.g. "checkoutservice",
             "CheckoutService/PlaceOrder", or t's cluster IP): base(s) × (s's error lines naming t ÷ s's
             error lines), counted from 2 minutes before the incident opened
graph(s)   = +bonus if s is downstream (callee) of another anomalous service in the proto call graph
score(s)   = base(s) + callee transfers + graph(s)
```

- The highest score is the **culprit** (ties: the service named by more others, then by name). Anomalous
  services whose errors name the culprit are **victims**. A service that went silent can still be the
  culprit through these transfers alone.
- Each ranked service carries its template IDs and up to 3 sample lines.

### 6.5 Summary and alert
- The LLM receives only the ranking, templates, samples, candidate commit and runbook titles. It must cite template IDs, and the validator checks them.
- The Discord webhook posts: the title, the culprit, the top candidate commit, and a link to the incident view.

**As built (block H, 2026-10-31): the incident brief** (`correlate/investigate.py`, the Brief tab).
1. Code gathers the core evidence and numbers it E1…En: one code-written sentence per ranked service, the incident's log patterns with a sample line, the top 3 candidate changes with their score parts, and the runbook sections.
2. The model may then call up to 4 read-only tools, one per reply, as JSON (`{"call": {"tool", "args"}}` or `{"finish": {"claims"}}`):
   `why(question)` (the why-answer retrieval, without a model), `impact(commit)` (candidate commits only),
   `pattern_samples(template_id)` (this incident's patterns only), `past_incidents(service)` (services in
   this incident; only incidents opened earlier). Anything else is refused and still counts against the budget.
3. Claims must cite evidence the model was shown and pass the lexical support check (as in §3). A claim that uses root-cause wording ("root cause", "culprit", "caused") must name the culprit code ranked and must not pin the cause on another service; otherwise it is removed. The optional model support check can only remove claims.
4. No model, no answer, budget spent or no surviving claim → sentences quoted from the evidence (`extractive`). No ranked culprit → "not found". A follow-up question the evidence does not answer → "not found".
5. Flagged text never reaches a model; uploaded documents stay local unless `DOCS_ALLOW_EXTERNAL`. Every step (code and model) is returned and shown, with the evidence ids it added.

The code-written `summary` field of an incident is unchanged and remains the alert text source.
Not yet measured: there is no evaluation of brief quality (see EVALUATION; pending a decision).

## 7. Incident → change correlation

- **Deploys** are recorded by `make fault` or by the deploy script (commit SHA + time).
- **Candidates** are commits in deploys within the 24 h before the incident.
- **Score:**
  ```
  score = 0.4 · exp(−gap/600s)
        + 0.3 · touches_culprit
        + 0.2 · culprit_in_stored_impact_prediction
        + 0.1 · frame_overlap
  ```
  `culprit_in_stored_impact_prediction` reads the prediction stored at deploy time, never a fresh one.
- **Output:** a ranked list labelled "candidates", each with evidence.

## 8. UI

| View | Content |
|---|---|
| **Change** | Commit or diff picker → impact grouped by service, with reasons and confidence bars; tests to run; docs that may need updating |
| **Ask** | Question box → claims with citation chips (clicking opens the source) and an evidence-path breadcrumb; the "not found" state shows the nearest items |
| **Tests** | Runs table (passed / failed / healed / genuine failure / flaky / needs human); repair drawer with before/after screenshots, score breakdown, patch, Approve/Reject |
| **Incidents** | Timeline lanes per service; ranking with culprit/victim badges; candidate change; runbook sections; summary with citations |
| **Docs** | Upload area; list with version, age and links; flagged passages |
| **Scorecard** | Cards: metric, baseline, n, CI, and a reproduce command |
| **Graph** (P1) | Cytoscape neighbourhood with provenance-coloured edges |

**Principles:**
- Every claim and score is clickable down to its evidence.
- Provenance badges are shown on edges.
- "Not found" and "genuine failure" are presented as normal outcomes, not errors.

### 8.1 As built (2026-10-31)

Black, minimal, pixel type: uppercase pixel labels (VT323), dot-matrix numbers (Doto), thin Inter
headlines. Pages: Home · Ask · Impact · Map · Incidents · Tests · Proof · More (Bus factor, Docs,
The data, Settings, Manual, Profile); busy pages split into sub-tabs. Copy is one line per idea.
Numbers sit in rounded cards with a small label above (`cm-card`, `cm-stats`, `cm-rows`).

Every colour means one thing, everywhere (legend in Manual › Colours):

| Colour | Meaning | Examples |
|---|---|---|
| Green | Confirmed: stated, passed, ready | `explicit` links, Hindsight's bars, tests on the map, call to action |
| Blue | History | commits, pull requests, issues, `api` links, the likely change of an incident |
| Orange | Learned | co-change (`statistical`) links, predictions |
| Red | Runtime | `runtime` links, the culprit, errors |
| White | The project's files | services, folders, files, docs, `static` links |
| Grey | Simple rule, baseline, inactive | `heuristic` links, simple-method bars, victims |
| Gold | People | authors, owners, your XP and badges |

The onboarding run is an original wireframe runner (three.js): six gates, one feature each, with a
live "try it" against the demo's API. The Map draws items as wireframe shapes (shape = kind,
colour = meaning) with the same glow. Its **Code structure** switch (off by default) adds the
functions and classes in each code file and the calls between them, read from the source by
graphify (TRD §0.4); centring on a function shows its calls. A workspace opens on a connect screen, plays the run while
the repository is read, then shows a progress card until it is ready.

## 9. LLM layer

- `llm.complete(task, prompt, schema)` walks the provider chain in order.
- Responses are cached in SQLite, keyed on `sha256(provider, model, template_version, prompt)`.
- `LLM_CACHE_MODE=replay` is used for tests and demos.
- Every task has a JSON schema. Invalid JSON gets 2 repair attempts, then fails closed.
- Per-task token caps, and a daily request counter shown on the Scorecard.
