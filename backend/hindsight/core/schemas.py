"""API contracts (pydantic v2). FastAPI publishes them as OpenAPI and the web app generates its
types from that, so this file is the single source of truth for every payload."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    """Response fields that have defaults are always present, so OpenAPI marks them required."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


Provenance = Literal["explicit", "api", "static", "statistical", "runtime", "heuristic"]


class EvidenceItem(Contract):
    id: str = Field(description="Citation id inside an answer (S1…Sn)")
    node_key: str
    kind: str
    span_kind: str
    title: str
    url: str
    snippet: str
    ts: int | None = None
    flagged: bool = Field(
        default=False, description="Contains instruction-like text; shown, never followed"
    )
    path: list[str] = Field(
        default_factory=list, description="How this evidence was reached from the anchor"
    )
    score: float = 0.0


class Claim(Contract):
    text: str
    cites: list[str]


class AskRequest(Contract):
    question: str = Field(min_length=3, max_length=500)
    file: str | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)


class Answer(Contract):
    status: Literal["answered", "not_found"]
    mode: Literal["llm", "extractive", "none"]
    question: str
    claims: list[Claim] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    nearest: list[EvidenceItem] = Field(default_factory=list)
    anchors: list[str] = Field(default_factory=list)
    checker: str
    notes: list[str] = Field(default_factory=list)


class Reason(Contract):
    text: str
    provenance: Provenance
    url: str = ""


class ImpactItem(Contract):
    target_kind: Literal["file", "service", "component", "doc", "test", "person"]
    target: str
    score: float
    reasons: list[Reason]
    url: str = ""


class ImpactRequest(Contract):
    files: list[str] = Field(default_factory=list)
    commit: str | None = None


class ImpactReport(Contract):
    changed_files: list[str]
    commit: str | None = None
    as_of_ts: int | None = None
    components: list[ImpactItem]
    files: list[ImpactItem]
    tests: list[ImpactItem]
    docs: list[ImpactItem]
    people: list[ImpactItem]
    runbooks: list[ImpactItem] = Field(default_factory=list)
    weights: dict[str, float]
    notes: list[str] = Field(default_factory=list)


class UploadReport(Contract):
    doc_key: str
    filename: str
    version: int
    doc_type: str
    status: Literal["created", "unchanged", "needs_attention"]
    chunks: int
    mentions: int
    runbook_links: int
    flagged_sections: list[str]
    warnings: list[str]


class DocInfo(Contract):
    key: str
    title: str
    url: str
    source: Literal["repo", "upload"]
    doc_type: str
    version: int | None = None
    updated_ts: int | None = None
    chunks: int
    mentions: int
    runbook_links: int
    flagged: int
    superseded: bool
    needs_attention: bool


class RunbookSection(Contract):
    chunk_key: str
    title: str
    section: str
    url: str
    snippet: str
    updated_ts: int | None = None


class CulpritRank(Contract):
    service: str
    role: Literal["culprit", "victim", "affected"]
    score: float
    evidence: dict[str, Any]
    templates: list[str]
    samples: list[str]


class IncidentTemplate(Contract):
    id: str
    service: str
    text: str
    severity: str
    count: int
    samples: list[str]


class ChangeCandidate(Contract):
    commit: str
    title: str
    url: str
    score: float
    deployed_at: int
    synthetic: bool
    evidence: dict[str, Any]


class TimelinePoint(Contract):
    ts: int
    count: int
    errors: int


class IncidentSummary(Contract):
    id: int
    opened_at: int
    updated_at: int
    closed_at: int | None = None
    source: str
    culprit: str | None = None
    services: list[str]


class Incident(Contract):
    id: int
    opened_at: int
    updated_at: int
    closed_at: int | None = None
    source: str
    ranking: list[CulpritRank]
    templates: list[IncidentTemplate]
    anomalies: list[dict[str, Any]]
    candidates: list[ChangeCandidate]
    runbooks: list[RunbookSection]
    timeline: dict[str, list[TimelinePoint]]
    summary: str


class InvestigateRequest(Contract):
    question: str | None = Field(
        default=None,
        min_length=3,
        max_length=500,
        description="A follow-up question about the incident; empty for the incident brief",
    )


class InvestigationStep(Contract):
    by: Literal["code", "model"] = Field(
        description="code: evidence gathered every time; model: a tool the model chose to call"
    )
    tool: str
    args: dict[str, str] = Field(default_factory=dict)
    status: Literal["ok", "empty", "refused"]
    evidence_ids: list[str] = Field(default_factory=list)
    note: str = ""


class IncidentBrief(Contract):
    incident_id: int
    question: str | None = None
    culprit: str | None = Field(default=None, description="As ranked by code, never by the model")
    status: Literal["answered", "not_found"]
    mode: Literal["llm", "extractive", "none"]
    claims: list[Claim] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    steps: list[InvestigationStep] = Field(default_factory=list)
    checker: str
    notes: list[str] = Field(default_factory=list)


class DeployRequest(Contract):
    commit: str
    ts: int | None = None
    note: str = ""


class DeployRecord(Contract):
    id: int
    commit: str
    title: str
    ts: int
    synthetic: bool
    note: str
    predicted_components: list[str]


class GraphNode(Contract):
    id: str
    kind: str
    label: str
    url: str = ""
    ts: int | None = None


class GraphEdge(Contract):
    source: str
    target: str
    kind: str
    provenance: Provenance
    evidence: dict[str, Any]


class GraphResponse(Contract):
    center: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    truncated: bool
    backend: str = "sqlite"  # which store answered: "neo4j" or "sqlite"


class GraphStatus(Contract):
    backend: str  # the store graph reads use right now
    configured: str  # GRAPH_BACKEND: auto | sqlite | neo4j
    neo4j_reachable: bool
    namespace: str
    synced_at: int | None
    in_sync: bool
    nodes: int
    edges: int
    browser_url: str


class SearchHit(Contract):
    key: str
    kind: str
    label: str
    url: str = ""


class RiskFile(Contract):
    path: str
    component: str
    changes: int
    authors: int
    top_author: str
    top_share: float
    single_owner: bool
    top_author_last_active_ts: int
    top_author_active: bool
    url: str = ""


class RiskComponent(Contract):
    component: str
    changes: int
    authors: int
    bus_factor: int = Field(
        description="Fewest authors who together made at least half of the changes"
    )
    top_author: str
    top_share: float


class RiskReport(Contract):
    files: list[RiskFile]
    components: list[RiskComponent]
    min_changes: int
    inactive_after_days: int


class CommitSummary(Contract):
    sha: str
    title: str
    ts: int
    author: str
    n_files: int
    is_bot: bool
    url: str
    pr: str | None = None


class RepoStats(Contract):
    repo: dict[str, Any] | None
    stats: dict[str, Any] | None
    github: dict[str, Any] | None
    ingested_at: int | None


class MethodScore(Contract):
    name: str  # 'hindsight' or a baseline's name
    value: float
    ci_low: float
    ci_high: float


class ScoreCard(Contract):
    suite: str  # "impact" (history replay) or "rca" (fault campaign)
    synthetic: bool
    note: str
    metric: str
    method: str
    value: float
    ci_low: float
    ci_high: float
    baseline: str
    baseline_value: float
    n: int
    command: str
    result_file: str
    methods: list[MethodScore]  # Hindsight first, then every baseline


class QaRun(Contract):
    id: int
    test_id: str
    version: int
    outcome: str  # passed | healed | failed | needs_human | flaky
    reason: str
    started_at: int
    duration_ms: int


class QaGrounding(Contract):
    step: int
    text: str
    found: bool
    file: str | None = None
    line: int | None = None
    origin_commit: str | None = None  # the commit that introduced the text (git log -S)


class QaTest(Contract):
    id: str
    title: str
    status: str  # active | unvalidated | rejected
    reason: str
    version: int
    source_key: str
    source_kind: str
    source_title: str
    source_url: str
    services: list[str]
    steps: list[str]
    grounding: list[QaGrounding]
    code: str
    last_run: QaRun | None


class QaRepair(Contract):
    id: int
    test_id: str
    run_id: int
    step: int
    line: str
    status: str  # proposed | approved | rejected | reverted
    old_locator: dict[str, str]
    new_locator: dict[str, str]
    score: float | None
    runner_up: float | None
    breakdown: dict[str, float]
    before_shot: str | None
    after_shot: str | None
    patch: str
    created_at: int


class AnswerCheckRequest(Contract):
    """A visitor's verdict on an answer they asked for (a live spot check, not a benchmark)."""

    question: str = Field(min_length=3, max_length=500)
    status: Literal["answered", "not_found"]
    mode: str = Field(max_length=20)
    verdict: Literal["supported", "unsupported", "right_not_found", "should_have_answered"]


class AnswerCheck(Contract):
    question: str
    status: str
    verdict: str
    created_at: int


class AnswerCheckSummary(Contract):
    n: int
    answered_rated: int
    supported: int
    not_found_rated: int
    right_not_found: int
    recent: list[AnswerCheck]


Feature = Literal["ask", "impact", "map", "incidents", "tests", "proof", "risk", "docs", "data"]


class AppConfig(Contract):
    """What this instance is: the demo (Online Boutique) or a workspace for your own repository."""

    mode: Literal["demo", "workspace"]
    demo_port: int
    repo: str | None = None
    features: list[Feature]


class ConnectRequest(Contract):
    url: str = Field(min_length=19, max_length=200, description="https://github.com/<owner>/<name>")


class WorkspaceStatus(Contract):
    """The repository on screen, and the connect job (ingest, then the accuracy replay)."""

    repo: str | None = None
    job: Literal["idle", "running", "failed"]
    job_repo: str | None = None
    stage: str = ""
    progress: float = 0.0
    error: str | None = None
    measured: bool = False
    measure_note: str | None = None


ProviderName = Literal["gemini", "groq", "ollama"]


class LlmProvider(Contract):
    """A model provider. Keys live in .env only: this reports whether one is set, never its value."""

    name: ProviderName
    label: str
    external: bool
    key_env: str | None = None
    key_set: bool
    model: str
    model_env: str
    ready: bool


class LlmSettings(Contract):
    order: list[ProviderName]
    providers: list[LlmProvider]
    allow_external: bool
    active: ProviderName | None = None


class LlmSettingsUpdate(Contract):
    order: list[ProviderName] = Field(max_length=3)
    models: dict[ProviderName, str] = Field(default_factory=dict)


class ModelList(Contract):
    provider: ProviderName
    models: list[str]
    error: str | None = None
