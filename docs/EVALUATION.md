# Hindsight: Evaluation (36-hour edition)

These rules apply to every number in this document:
- Every number comes from `make eval` and is written to `eval/results/<name>-<timestamp>.json`, together with its config.
- Every metric is shown with a **baseline**, the sample size **n**, and a **95% bootstrap CI** (1,000 resamples, seed 13).
- Synthetic setups (mutations, injected faults) are labelled synthetic.
- If a result is worse than its baseline, show it anyway.

Other public repositories are evaluated with the same protocol and the same default weights,
each in its own data folder (`make try-repo REPO=<url>`); `eval/results/latest.md` lists them
separately from the main repository. Nothing is tuned on them.

## 1. Splits and leakage
- History is split by time: oldest 70% for statistics, next 15% for validation (tuning weights and thresholds), newest 15% for testing.
- Replay queries only use rows with `ts <= query commit time`.

## 2. Blast radius (history replay)
- **Queries:** test-split commits with 2–30 files, excluding bot, sweep, generated and merge commits.
- **Protocol:** reveal one file (seed-fixed) and predict the rest.
- **Metrics:** P@5, R@10, Hit@3.
- **Baselines:**
  - same directory (most recent first);
  - most frequently changed files;
  - co-change only (no static, service or recency signals).
- **Ablation:** with and without bot/sweep filtering. Report the bot share.

## 3. Why-answers
- **Question set** (`eval/questions.yaml`), written **before** looking at the system's answers: ≥ 20 answerable, each with gold evidence span IDs, and ≥ 10 unanswerable. If docs are ingested, at least 5 answerable questions must have their gold evidence in a doc.
- **Metrics:**
  - citation validity (cited span exists and entails the claim);
  - gold-evidence recall@5 of retrieval;
  - abstention accuracy on unanswerable questions;
  - false-abstention rate on answerable ones.
- **Baseline:** the same model answering without retrieval (shows its hallucination rate).
- Hand-check at least 10 answers and report how often the automatic checker agrees.

## 4. Healing (mutation harness)
- **Cosmetic mutations** (the healer should heal): id rename, class rename, a wrapper element inserted, sibling reorder, button text change that keeps its meaning. At least 20.
- **Real-defect mutations** (the healer must refuse): element removed, control disabled, backend returns an error (env-gated fault), wrong total shown. At least 5.
- **Metrics:** heal rate, **false heals (target 0)**, correct refusals, heal latency.
- **Baselines:** no healing; naive "first element with the same tag and text".

## 5. LogSense
- **Fault campaign:** at least 10 injections across scenarios (checkout error, latency via `tc netem`, scale-to-zero, crash loop) and services, each with ≥ 5 minutes of healthy traffic before it. Ground truth is written by `make fault`.
- **Metrics:** top-1 and top-3 culprit accuracy, median time to detect, false alerts per hour during healthy periods.
- **Baselines:** "service with the most error lines"; "service with the largest error-rate increase".
- **Optional (if time allows):** BARO from the RCAEval package on RE2-OB, reported as outside context, not as a head-to-head comparison.

## 6. Correlation
On injected-commit runs, report the guilty commit's rank (Hit@1, Hit@3) against the baseline "most recent deploy".

## 7. Scorecard
`GET /api/scorecard` reads the latest result files. Each card shows the metric, value, baseline, n, CI and the reproduce command.
