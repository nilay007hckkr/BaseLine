"""Workspace mode (connect your own repository) and model settings. Ingest, evaluation and the
providers' HTTP APIs are replaced by fakes; every payload below is FIXTURE data."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from hindsight.api import app as app_module
from hindsight.api import llm_settings
from hindsight.api.workspace import Workspace, parse_repo_url
from hindsight.core.config import Settings, load_settings
from hindsight.core.db import connect, set_meta
from hindsight.core.schemas import LlmSettingsUpdate

FAKE_KEY = "AIzaFIXTUREFIXTUREFIXTUREFIXTUREFIXTURE12"


def _settings(tmp_path: Path, **env: str) -> Settings:
    return load_settings({"DATA_DIR": str(tmp_path / "ws"), "LLM_PROVIDER_ORDER": "", **env})


def _fake_ingest(settings: Settings, repo_url: str, log) -> dict[str, int]:
    log(f"cloning {repo_url}")
    log("reading git history")
    conn = connect(settings.db_path)
    with conn:
        set_meta(conn, "repo", {"url": repo_url, "web": repo_url.removesuffix(".git")})
    conn.close()
    log("done in 0.1s")
    return {}


def _measured(conn: sqlite3.Connection, out: Path) -> bool:
    out.mkdir(parents=True, exist_ok=True)
    (out / "impact-20260101T000000Z.json").write_text("{}")
    return True


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://github.com/acme/shop", True),
        ("https://github.com/acme/shop.git", True),
        ("https://github.com/acme/shop/", True),
        ("http://github.com/acme/shop", False),
        ("https://gitlab.com/acme/shop", False),
        ("https://evil.example/github.com/acme/shop", False),
        ("https://github.com/acme/shop/tree/main", False),
        ("https://github.com/../shop", False),
        ("git@github.com:acme/shop.git", False),
    ],
)
def test_only_public_github_urls_are_accepted(url, ok):
    if ok:
        assert parse_repo_url(url) == ("acme", "shop")
    else:
        with pytest.raises(ValueError):
            parse_repo_url(url)


def test_connect_ingests_into_its_own_folder_then_measures(tmp_path):
    base = _settings(tmp_path)
    ws = Workspace(base, ingest_fn=_fake_ingest, evaluate_fn=_measured)
    assert ws.status().job == "idle" and ws.repo() is None
    assert ws.current(base).db_path == tmp_path / "ws" / "empty" / "hindsight.db"

    ws.connect(base, "https://github.com/acme/shop")
    ws.join(5)

    status = ws.status()
    assert status.repo == "https://github.com/acme/shop"
    assert status.job == "idle" and status.measured and status.measure_note is None
    current = ws.current(base)
    assert current.db_path == tmp_path / "ws" / "repos-by-name" / "acme__shop" / "hindsight.db"
    assert current.results_dir.parent == current.data_dir
    assert current.graph_backend == "sqlite"
    # a restart remembers the repository on screen
    assert Workspace(base).repo() == "https://github.com/acme/shop"


def test_a_failed_connect_is_reported_and_scrubbed(tmp_path):
    def broken(settings, repo_url, log):
        raise RuntimeError("clone failed for ghp_" + "x" * 30)

    base = _settings(tmp_path)
    ws = Workspace(base, ingest_fn=broken, evaluate_fn=_measured)
    ws.connect(base, "https://github.com/acme/shop")
    ws.join(5)
    status = ws.status()
    assert status.job == "failed" and status.repo is None
    assert status.error and "ghp_" not in status.error and "[redacted]" in status.error


def test_too_little_history_is_usable_but_unmeasured(tmp_path):
    base = _settings(tmp_path)
    ws = Workspace(base, ingest_fn=_fake_ingest, evaluate_fn=lambda conn, out: False)
    ws.connect(base, "https://github.com/acme/shop")
    ws.join(5)
    status = ws.status()
    assert status.repo and status.job == "idle" and not status.measured
    assert status.measure_note and "too little history" in status.measure_note


def test_one_connect_at_a_time(tmp_path):
    release = threading.Event()

    def slow(settings, repo_url, log):
        release.wait(5)
        return _fake_ingest(settings, repo_url, log)

    base = _settings(tmp_path)
    ws = Workspace(base, ingest_fn=slow, evaluate_fn=_measured)
    ws.connect(base, "https://github.com/acme/shop")
    with pytest.raises(RuntimeError):
        ws.connect(base, "https://github.com/acme/other")
    release.set()
    ws.join(5)


def _client(settings: Settings, workspace: Workspace | None) -> TestClient:
    prefs = llm_settings.Prefs(settings.data_dir / "settings.json")
    app_module.app.dependency_overrides.clear()
    app_module.app.dependency_overrides[app_module.get_base_settings] = lambda: settings
    app_module.app.dependency_overrides[app_module.get_settings] = lambda: (
        workspace.current(prefs.apply(settings)) if workspace else prefs.apply(settings)
    )
    app_module.app.dependency_overrides[app_module.get_prefs] = lambda: prefs
    app_module.app.dependency_overrides[app_module.get_workspace] = lambda: workspace
    return TestClient(app_module.app)


def test_config_and_workspace_endpoints(tmp_path):
    base = _settings(tmp_path)
    demo = _client(base, None)
    config = demo.get("/api/config").json()
    assert config["mode"] == "demo" and "incidents" in config["features"]
    assert demo.get("/api/workspace/status").status_code == 404
    assert (
        demo.post("/api/workspace/connect", json={"url": "https://github.com/a/b"}).status_code
        == 404
    )

    ws = Workspace(base, ingest_fn=_fake_ingest, evaluate_fn=_measured)
    c = _client(base, ws)
    config = c.get("/api/config").json()
    assert config["mode"] == "workspace" and config["repo"] is None
    assert "incidents" not in config["features"] and "tests" not in config["features"]
    assert (
        c.post("/api/workspace/connect", json={"url": "https://gitlab.com/a/b"}).status_code == 422
    )
    accepted = c.post("/api/workspace/connect", json={"url": "https://github.com/acme/shop"})
    assert accepted.status_code == 202
    ws.join(5)
    assert c.get("/api/workspace/status").json()["repo"] == "https://github.com/acme/shop"
    assert c.get("/api/config").json()["repo"] == "https://github.com/acme/shop"
    app_module.app.dependency_overrides.clear()


def test_model_settings_never_return_a_key_and_persist_choices(tmp_path):
    base = _settings(tmp_path, GEMINI_API_KEY=FAKE_KEY, GEMINI_MODEL="fixture-model-a")
    c = _client(base, None)
    view = c.get("/api/settings/llm")
    assert FAKE_KEY not in view.text
    gemini = next(p for p in view.json()["providers"] if p["name"] == "gemini")
    assert gemini["key_set"] and gemini["ready"] and gemini["key_env"] == "GEMINI_API_KEY"

    saved = c.put(
        "/api/settings/llm",
        json={"order": ["ollama", "gemini"], "models": {"gemini": "fixture-model-b"}},
    ).json()
    assert saved["order"] == ["ollama", "gemini"]
    assert (
        next(p for p in saved["providers"] if p["name"] == "gemini")["model"] == "fixture-model-b"
    )
    assert saved["active"] == "gemini"  # ollama has no model set, so gemini answers
    stored = json.loads((base.data_dir / "settings.json").read_text())
    assert FAKE_KEY not in json.dumps(stored)

    bad = {"order": ["gemini", "gemini"], "models": {}}
    assert c.put("/api/settings/llm", json=bad).status_code == 422
    bad = {"order": ["gemini"], "models": {"gemini": "rm -rf /"}}
    assert c.put("/api/settings/llm", json=bad).status_code == 422
    assert c.put("/api/settings/llm", json={"order": ["gpt"]}).status_code == 422
    app_module.app.dependency_overrides.clear()


def test_model_lists_come_from_the_provider(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "generativelanguage.googleapis.com":
            assert request.headers["x-goog-api-key"] == FAKE_KEY
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "models/fixture-chat",
                            "supportedGenerationMethods": ["generateContent"],
                        },
                        {
                            "name": "models/fixture-embed",
                            "supportedGenerationMethods": ["embedContent"],
                        },
                    ]
                },
            )
        if request.url.host == "api.groq.com":
            return httpx.Response(401, json={"error": "bad key"})
        return httpx.Response(200, json={"models": [{"name": "fixture-local:4b"}]})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    settings = _settings(tmp_path, GEMINI_API_KEY=FAKE_KEY, GROQ_API_KEY="gsk_fixture")
    assert llm_settings.list_models(settings, "gemini", http).models == ["fixture-chat"]
    groq = llm_settings.list_models(settings, "groq", http)
    assert groq.models == [] and groq.error == "401 from Groq"
    assert llm_settings.list_models(settings, "ollama", http).models == ["fixture-local:4b"]

    no_key = _settings(tmp_path)
    missing = llm_settings.list_models(no_key, "gemini", http)
    assert missing.error == "set GEMINI_API_KEY in .env"
    offline = _settings(tmp_path, GEMINI_API_KEY=FAKE_KEY, LLM_ALLOW_EXTERNAL="false")
    assert llm_settings.list_models(offline, "gemini", http).error == "LLM_ALLOW_EXTERNAL is false"


def test_saved_choices_reach_the_answer_chain(tmp_path):
    base = _settings(tmp_path, GROQ_API_KEY="gsk_fixture", GROQ_MODEL="fixture-groq")
    prefs = llm_settings.Prefs(base.data_dir / "settings.json")
    prefs.save(LlmSettingsUpdate(order=["groq"], models={"groq": "fixture-groq-2"}))
    applied = llm_settings.Prefs(base.data_dir / "settings.json").apply(base)
    assert applied.llm_provider_order == ("groq",) and applied.groq_model == "fixture-groq-2"


def test_a_missing_repository_fails_fast_instead_of_prompting(tmp_path):
    from hindsight.brain.ingest import ensure_clone

    with pytest.raises(RuntimeError, match="could not clone"):
        ensure_clone(str(tmp_path / "no-such-repo"), tmp_path / "dest", log=lambda _: None)
