"""FastAPI app. Run with `make dev` (API + web dev server) or `make serve` (API + built web)."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from hindsight.api import service
from hindsight.brain.graph import neighbourhood, search
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
    AskRequest,
    CommitSummary,
    DeployRecord,
    DeployRequest,
    DocInfo,
    GraphResponse,
    GraphStatus,
    ImpactReport,
    ImpactRequest,
    Incident,
    IncidentSummary,
    QaRepair,
    QaTest,
    RepoStats,
    RiskReport,
    RunbookSection,
    ScoreCard,
    SearchHit,
    UploadReport,
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
_settings = load_settings()


def get_settings() -> Settings:
    return _settings


def get_conn(settings: Annotated[Settings, Depends(get_settings)]) -> Iterator[sqlite3.Connection]:
    conn = connect(settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(get_conn)]
Cfg = Annotated[Settings, Depends(get_settings)]


def get_graph(conn: Conn, settings: Cfg) -> GraphReader:
    try:
        return graph_reader(conn, settings)
    except GraphUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


Graph = Annotated[GraphReader, Depends(get_graph)]


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


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


@app.get("/api/commits", response_model=list[CommitSummary])
def commits(
    conn: Conn, limit: Annotated[int, Query(ge=1, le=200)] = 50, include_bots: bool = False
) -> list[CommitSummary]:
    return service.recent_commits(conn, limit, include_bots)


@app.get("/api/graph", response_model=GraphResponse)
def graph_view(
    conn: Conn, graph: Graph, key: str, depth: Annotated[int, Query(ge=1, le=2)] = 1
) -> GraphResponse:
    result = neighbourhood(conn, key, depth, graph=graph)
    if result is None:
        raise HTTPException(status_code=404, detail=f"node {key} not found")
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
def scorecard(settings: Annotated[Settings, Depends(get_settings)]) -> list[ScoreCard]:
    return service.scorecard(primary=service.primary_web(settings))


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
