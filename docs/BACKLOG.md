# Backlog
One line per idea, tagged with the block that surfaced it.

- (planning) Learned impact ranker trained by history replay.
- (planning) MCP server so coding agents can ask "what does this change affect?".
- (phase 1) Question set for why-answers (≥ 20 answerable with gold evidence + ≥ 10 unanswerable), written by the team before seeing answers, to measure citation validity and abstention accuracy (EVALUATION §3).
- (phase 1) Tune impact weights on the validation split and report tuned vs. default.
- (phase 1, done in block D) Meaning-level support check for model-phrased claims (a model check that can only remove claims; a dedicated NLI model is still open).
- (phase 1, done in block D) Embeddings alongside keyword search for why-questions phrased differently from the history.
- (phase 1) Complete the GitHub cache (a token, or more `make ingest` runs) and add per-PR reviews (approve/request-changes summaries).
- (phase 1) Pull-request check: impact summary on each new change (dropped for now; nothing is posted to GitHub).
- (phase 1) Importers for chat and tickets from exports (Slack, Jira, Confluence) using sample data.
- (block B, campaign 1) Read service names from warning lines too: the frontend logs recommendation and ad failures as warnings that name the dead service's cluster IP, so those incidents blamed only the frontend.
- (block B, campaign 1) A pattern first seen during an earlier incident is no longer "new" (checkout-down reused payment-down's connection-refused pattern and was missed): treat a known error pattern that names a different service as new.
- (block B, campaign 1) `currency-crash` does not break the shop: with one replica the rolling update keeps the old pod serving. Make the scenario delete the old pod or set maxUnavailable to 1.
- (block B, campaign 1) Errors such as "name resolver error: produced zero addresses" name no service; use the call graph (who calls whom) to blame the caller's misconfigured dependency.
- (block B) Re-run the campaign after these fixes and report it as a second run next to the first.
- (block H) Importers for Slack and Jira exports with explicit-reference linking (PR numbers, SHAs, issue keys); the target has no Slack or Jira, so only labelled fixtures until a real export exists, kept out of `make eval`.
- (block H) The fault script picked the same "guilty" commit (11a66b2, release/v0.10.5, which bumps every service) in 9 of 11 runs, so "touches the culprit" holds for any culprit; exclude release and sweep commits when choosing the guilty change, then re-run the correlation numbers.
- (block H) Measure incident briefs: claims kept vs proposed, fallback rate and wrong-culprit removals over the detected campaign incidents (needs an EVALUATION.md change).
- (block I) Run `make codegraph` for repositories connected in workspace mode too (each has its own clone and database).
- (block I) Add inheritance links and the few imports that resolve inside the repository to the code layer.
- (block I) "Which functions call the changed function" as an impact reason, only after a history-replay run shows it helps.
- (map) Detect REST calls between services: Feign `@FeignClient(name=…)`, `http://<service>:<port>` URLs and `*_URL` / `*_HOST` variables, matched to service folders; piggymetrics has 10 services and no calls today.
- (map) Read docker-compose / Kubernetes / Helm: `build.context` → service folder, `depends_on` → calls (heuristic provenance).
- (map) Message queues: a topic produced by one service and consumed by another as a link (OpenTelemetry Demo's Kafka consumers have no calls today).
- (ask) "Dig deeper" agent for Ask: a few read-only lookups chosen by the model, every claim still citation-checked; proposal and decisions in docs/ASK_AGENT.md.
