"""FastAPI app. Run with `make dev` (API + web dev server) or `make serve` (API + built web)."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from typing import Annotated, Literal

import httpx
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Path,
    Query,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from hindsight.api import llm_settings, service
from hindsight.api.workspace import Workspace
from hindsight.brain.graph import default_centre, neighbourhood, search, services_overview
from hindsight.brain.impact import impact_report
from hindsight.brain.risk import knowledge_at_risk
from hindsight.brain.uploads import (
    MAX_BYTES,
    UnsupportedDocument,
    ingest_upload,
    list_docs,
    runbooks_for,
)
from hindsight.core.config import ROOT, Settings, load_settings
from hindsight.core.db import connect, get_meta
from hindsight.core.graphstore import GraphReader, GraphUnavailableError, graph_reader
from hindsight.core.schemas import (
    Answer,
    AnswerCheckRequest,
    AnswerCheckSummary,
    AppConfig,
    AskRequest,
    CommitSummary,
    ConnectRequest,
    DeployRecord,
    DeployRequest,
    DocInfo,
    Feature,
    GraphResponse,
    GraphStatus,
    ImpactItem,
    ImpactReport,
    ImpactRequest,
    Incident,
    IncidentBrief,
    IncidentSummary,
    InvestigateRequest,
    LlmSettings,
    LlmSettingsUpdate,
    ModelList,
    ProviderName,
    QaRepair,
    QaTest,
    RepoStats,
    RiskReport,
    RunbookSection,
    ScoreCard,
    SearchHit,
    UploadReport,
    WorkspaceStatus,
)
from hindsight.correlate.deploys import list_deploys, record_deploy
from hindsight.logsense.incidents import get_incident, list_incidents
from hindsight.sentinel import service as qa
from hindsight.sentinel.store import decide_repair

_BLOB = re.compile(r"^[0-9a-f]{64}\.(md|markdown|pdf)$")
_SHOT = re.compile(r"^[A-Z0-9-]+-\d+-\d+-(before|after)\.png$")

app = FastAPI(
    title="Hindsight",
    version="0.1.0",
    description="Project Brain: why was this built this way, and what will this change affect?",
)
_base = load_settings()
_prefs = llm_settings.Prefs(_base.data_dir / "settings.json")
_workspace = Workspace(_base) if _base.mode == "workspace" else None
if _base.cors_origins:  # e.g. the workspace instance's tour calls the demo's API
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(_base.cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

DEMO_FEATURES: tuple[Feature, ...] = ("ask", "impact", "map", "proof", "risk", "docs", "data")
# Incidents and Tests (PS 04 / PS 08): no new work, shown on the demo as additional features
# (SHOW_FROZEN=false hides them). They need the demo shop (its logs and browser tests), so a
# workspace never shows them.
FROZEN_FEATURES: tuple[Feature, ...] = ("incidents", "tests")
WORKSPACE_FEATURES: tuple[Feature, ...] = DEMO_FEATURES


def demo_features(settings: Settings) -> list[Feature]:
    if not settings.show_frozen:
        return list(DEMO_FEATURES)
    return [*DEMO_FEATURES, *FROZEN_FEATURES]


def get_base_settings() -> Settings:
    """This instance's settings with the dashboard's model choices applied."""
    return _prefs.apply(_base)


def get_settings(base: Annotated[Settings, Depends(get_base_settings)]) -> Settings:
    """Per request: in workspace mode, the folders of the repository on screen."""
    return _workspace.current(base) if _workspace else base


def get_prefs() -> llm_settings.Prefs:
    return _prefs


def get_workspace() -> Workspace | None:
    return _workspace


def get_http() -> Iterator[httpx.Client]:
    with httpx.Client(timeout=10) as client:
        yield client


def get_conn(settings: Annotated[Settings, Depends(get_settings)]) -> Iterator[sqlite3.Connection]:
    conn = connect(settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(get_conn)]
Cfg = Annotated[Settings, Depends(get_settings)]
BaseCfg = Annotated[Settings, Depends(get_base_settings)]
PrefsDep = Annotated[llm_settings.Prefs, Depends(get_prefs)]
WorkspaceDep = Annotated[Workspace | None, Depends(get_workspace)]
Http = Annotated[httpx.Client, Depends(get_http)]


def get_graph(conn: Conn, settings: Cfg) -> GraphReader:
    try:
        return graph_reader(conn, settings)
    except GraphUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


Graph = Annotated[GraphReader, Depends(get_graph)]


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config", response_model=AppConfig)
def app_config(settings: Cfg, workspace: WorkspaceDep) -> AppConfig:
    if workspace is None:
        return AppConfig(
            mode="demo",
            demo_port=settings.demo_port,
            repo=service.primary_web(settings),
            features=demo_features(settings),
        )
    return AppConfig(
        mode="workspace",
        demo_port=settings.demo_port,
        repo=workspace.repo(),
        features=list(WORKSPACE_FEATURES),
    )


def _need_workspace(workspace: Workspace | None) -> Workspace:
    if workspace is None:
        raise HTTPException(status_code=404, detail="only on the workspace instance (make run)")
    return workspace


@app.get("/api/workspace/status", response_model=WorkspaceStatus)
def workspace_status(workspace: WorkspaceDep) -> WorkspaceStatus:
    return _need_workspace(workspace).status()


@app.post("/api/workspace/connect", response_model=WorkspaceStatus, status_code=202)
def workspace_connect(
    req: ConnectRequest, workspace: WorkspaceDep, base: BaseCfg
) -> WorkspaceStatus:
    ws = _need_workspace(workspace)
    try:
        return ws.connect(base, req.url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/settings/llm", response_model=LlmSettings)
def llm_view(settings: Cfg) -> LlmSettings:
    return llm_settings.view(settings)


@app.put("/api/settings/llm", response_model=LlmSettings)
def llm_save(update: LlmSettingsUpdate, prefs: PrefsDep, base: BaseCfg) -> LlmSettings:
    try:
        prefs.save(update)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return llm_settings.view(prefs.apply(base))


@app.get("/api/settings/llm/{provider}/models", response_model=ModelList)
def llm_models(provider: ProviderName, settings: Cfg, http: Http) -> ModelList:
    return llm_settings.list_models(settings, provider, http)


@app.get("/api/repo/stats", response_model=RepoStats)
def repo_stats(conn: Conn) -> RepoStats:
    return RepoStats(
        repo=get_meta(conn, "repo"),
        stats=get_meta(conn, "stats"),
        github=get_meta(conn, "forge"),
        ingested_at=get_meta(conn, "ingested_at"),
    )


@app.post("/api/ask", response_model=Answer)
def ask(req: AskRequest, conn: Conn, settings: Cfg, graph: Graph) -> Answer:
    return service.ask_question(
        conn, settings, req.question, req.file, req.line_start, req.line_end, graph
    )


@app.get("/api/answer-checks", response_model=AnswerCheckSummary)
def answer_checks(conn: Conn) -> AnswerCheckSummary:
    return service.answer_check_summary(conn)


@app.post("/api/answer-checks", response_model=AnswerCheckSummary)
def rate_answer(check: AnswerCheckRequest, conn: Conn) -> AnswerCheckSummary:
    try:
        with conn:
            return service.record_answer_check(conn, check)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/impact", response_model=ImpactReport)
def impact(req: ImpactRequest, conn: Conn, graph: Graph) -> ImpactReport:
    if not req.files and not req.commit:
        raise HTTPException(status_code=422, detail="give changed files or a commit")
    return impact_report(conn, files=req.files, commit=req.commit, graph=graph)


def _pr_report(
    number: int, conn: sqlite3.Connection, settings: Settings, graph: GraphReader
) -> ImpactReport:
    from hindsight.brain.prcheck import pr_report

    try:
        return pr_report(conn, settings, number, graph=graph)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=f"PR #{number}: {exc}") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/impact/pr/{number}", response_model=ImpactReport)
def impact_of_pr(
    number: Annotated[int, Path(ge=1)], conn: Conn, settings: Cfg, graph: Graph
) -> ImpactReport:
    """PR check: the impact of a GitHub pull request's changed files (read-only)."""
    return _pr_report(number, conn, settings, graph)


@app.get("/api/impact/pr/{number}/comment", response_class=PlainTextResponse)
def impact_of_pr_comment(
    number: Annotated[int, Path(ge=1)], conn: Conn, settings: Cfg, graph: Graph
) -> str:
    """The same summary as Markdown, ready to paste as a PR comment (nothing is posted)."""
    from hindsight.brain.prcheck import markdown

    return markdown(_pr_report(number, conn, settings, graph))


@app.get("/api/commits", response_model=list[CommitSummary])
def commits(
    conn: Conn, limit: Annotated[int, Query(ge=1, le=200)] = 50, include_bots: bool = False
) -> list[CommitSummary]:
    return service.recent_commits(conn, limit, include_bots)


@app.get("/api/graph", response_model=GraphResponse)
def graph_view(
    conn: Conn,
    graph: Graph,
    key: str | None = None,
    depth: Annotated[int, Query(ge=1, le=2)] = 1,
    code: bool = False,
    view: Literal["around", "services"] = "around",
) -> GraphResponse:
    """`code=true` adds functions, classes and their calls (the code structure layer). Without a
    `key`, the Map starts at the most linked service, component or file of this repository.
    `view=services` draws every service and the calls between them instead (a repository with
    fewer than two services gets the neighbourhood view)."""
    if view == "services" and not key:
        overview = services_overview(conn, graph)
        if overview is not None:
            return overview
    centre = key or default_centre(conn)
    if centre is None:
        raise HTTPException(status_code=404, detail="the graph is empty: ingest a repository first")
    result = neighbourhood(conn, centre, depth, graph=graph, code=code)
    if result is None:
        raise HTTPException(status_code=404, detail=f"node {centre} not found")
    return result


@app.get("/api/graph/status", response_model=GraphStatus)
def graph_status(conn: Conn, settings: Cfg) -> GraphStatus:
    return service.graph_status(conn, settings)


@app.get("/api/search", response_model=list[SearchHit])
def search_nodes(conn: Conn, q: Annotated[str, Query(min_length=2)]) -> list[SearchHit]:
    return search(conn, q)


@app.get("/api/risk", response_model=RiskReport)
def risk(conn: Conn) -> RiskReport:
    return knowledge_at_risk(conn)


@app.get("/api/scorecard", response_model=list[ScoreCard])
def scorecard(settings: Cfg) -> list[ScoreCard]:
    return service.scorecard(settings.results_dir, primary=service.primary_web(settings))


@app.post("/api/docs", response_model=UploadReport)
def upload_doc(
    conn: Conn,
    settings: Cfg,
    background: BackgroundTasks,
    file: Annotated[UploadFile, File(description="PDF or Markdown")],
    doc_type: Annotated[str, Form()] = "other",
) -> UploadReport:
    data = file.file.read(MAX_BYTES + 1)
    try:
        with conn:
            report = ingest_upload(
                conn, settings.uploads_dir, file.filename or "document", data, doc_type
            )
    except UnsupportedDocument as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    # until the copy finishes, graph reads fall back to SQLite (the counts no longer match)
    background.add_task(service.resync_graph, settings)
    return report


@app.get("/api/docs", response_model=list[DocInfo])
def docs(conn: Conn) -> list[DocInfo]:
    return list_docs(conn)


@app.get("/api/docs/stale", response_model=list[ImpactItem])
def stale(conn: Conn) -> list[ImpactItem]:
    """Docs that may be out of date: what they describe kept changing after their last edit."""
    from hindsight.brain.stale import stale_docs

    return stale_docs(conn)


@app.get("/api/docs/blob/{name}")
def doc_blob(name: str, settings: Annotated[Settings, Depends(get_settings)]) -> FileResponse:
    path = settings.uploads_dir / name
    if not _BLOB.match(name) or not path.is_file():
        raise HTTPException(status_code=404, detail="no such document")
    return FileResponse(path)


@app.get("/api/services/{name}/runbooks", response_model=list[RunbookSection])
def service_runbooks(name: str, conn: Conn) -> list[RunbookSection]:
    return runbooks_for(conn, name)


@app.get("/api/qa/tests", response_model=list[QaTest])
def qa_tests(conn: Conn) -> list[QaTest]:
    return qa.list_tests(conn)


@app.post("/api/qa/tests/{test_id}/run", response_model=QaTest)
def qa_run(test_id: str, conn: Conn, settings: Cfg) -> QaTest:
    if not qa.run_tests(conn, settings, [test_id]):
        raise HTTPException(status_code=404, detail=f"no active test {test_id} (validate it first)")
    return next(t for t in qa.list_tests(conn) if t.id == test_id)


@app.get("/api/qa/repairs", response_model=list[QaRepair])
def qa_repairs(conn: Conn) -> list[QaRepair]:
    return qa.list_repairs(conn)


@app.post("/api/qa/repairs/{repair_id}/{decision}", response_model=QaRepair)
def qa_decide(repair_id: int, decision: str, conn: Conn) -> QaRepair:
    if decision not in ("approve", "reject", "revert"):
        raise HTTPException(status_code=422, detail="decision is approve, reject or revert")
    try:
        with conn:
            decide_repair(conn, repair_id, decision)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"repair {repair_id} not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return next(r for r in qa.list_repairs(conn) if r.id == repair_id)


@app.get("/api/qa/shots/{name}")
def qa_shot(name: str, settings: Cfg) -> FileResponse:
    path = qa.shots_dir(settings) / name
    if not _SHOT.match(name) or not path.is_file():
        raise HTTPException(status_code=404, detail="no such screenshot")
    return FileResponse(path)


@app.get("/api/incidents", response_model=list[IncidentSummary])
def incidents(conn: Conn) -> list[IncidentSummary]:
    return list_incidents(conn)


@app.get("/api/incidents/{incident_id}", response_model=Incident)
def incident(incident_id: int, conn: Conn) -> Incident:
    found = get_incident(conn, incident_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"incident {incident_id} not found")
    return found


@app.post("/api/incidents/{incident_id}/investigate", response_model=IncidentBrief)
def investigate_incident(
    incident_id: int, req: InvestigateRequest, conn: Conn, settings: Cfg
) -> IncidentBrief:
    brief = service.investigate_incident(conn, settings, incident_id, req.question)
    if brief is None:
        raise HTTPException(status_code=404, detail=f"incident {incident_id} not found")
    return brief


@app.get("/api/deploys", response_model=list[DeployRecord])
def deploys(conn: Conn) -> list[DeployRecord]:
    return list_deploys(conn)


@app.post("/api/deploys", response_model=DeployRecord)
def add_deploy(req: DeployRequest, conn: Conn) -> DeployRecord:
    record = record_deploy(conn, req.commit, ts=req.ts, note=req.note)
    if record is None:
        raise HTTPException(status_code=404, detail=f"commit {req.commit} not found")
    return record


_web = ROOT / "web" / "dist"
if _web.is_dir():
    app.mount("/", StaticFiles(directory=_web, html=True), name="web")
