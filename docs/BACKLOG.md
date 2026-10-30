# Backlog
One line per idea, tagged with the block that surfaced it.

- (planning) Learned impact ranker trained by history replay.
- (planning) MCP server so coding agents can ask "what does this change affect?".
- (phase 1) Question set for why-answers (≥ 20 answerable with gold evidence + ≥ 10 unanswerable), written by the team before seeing answers, to measure citation validity and abstention accuracy (EVALUATION §3).
- (phase 1) Tune impact weights on the validation split and report tuned vs. default.
- (phase 1) Meaning-level support check (a small NLI model) for model-phrased claims; today the check is keyword coverage.
- (phase 1) Embeddings alongside keyword search for why-questions phrased differently from the history.
- (phase 1) Complete the GitHub cache (a token, or more `make ingest` runs) and add per-PR reviews (approve/request-changes summaries).
- (phase 1) Pull-request check: impact summary on each new change (dropped for now; nothing is posted to GitHub).
- (phase 1) Importers for chat and tickets from exports (Slack, Jira, Confluence) using sample data.
