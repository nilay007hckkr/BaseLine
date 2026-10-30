"""Block 4 on the FIXTURE repository: semantic search (with a fake, deterministic embedder) and the
model support check (with a fake model). No Ollama needed."""

import json
import re
import shutil

import numpy as np

from hindsight.brain.ask import ask
from hindsight.core.db import connect
from hindsight.core.embed import QUERY_PREFIX, dense_search, has_index, index_spans
from hindsight.core.llm import Chain

VOCAB = ["usd", "rounding", "currency", "cart", "memory", "retry", "timeout", "runbook", "payment"]


class BagOfWords:
    """Deterministic stand-in for an embedding model: counts of a few words (plus a constant so no
    vector is zero)."""

    model = "fake-bow"

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        out = []
        for text in texts:
            words = re.findall(r"[a-z]+", text.lower())
            out.append([float(words.count(w)) for w in VOCAB] + [0.1])
        return out


class Scripted:
    """A fake model that answers each prompt with the next scripted reply."""

    name, model = "fake", "fake-1"

    def __init__(self, *replies: dict) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return json.dumps(self.replies.pop(0) if self.replies else {})


def _copy(fx, tmp_path):
    db = tmp_path / "copy.db"
    shutil.copy(fx.settings.db_path, db)
    return connect(db)


def test_index_embeds_each_span_once_and_search_ranks_by_meaning(fx, tmp_path):
    conn = _copy(fx, tmp_path)
    bow = BagOfWords()
    first = index_spans(conn, bow, log=lambda _: None)
    assert first["embedded_now"] == first["spans_with_vectors"] > 0
    assert index_spans(conn, bow, log=lambda _: None)["embedded_now"] == 0  # unchanged: skipped
    conn.execute("UPDATE spans SET text = text || ' retry' WHERE id = (SELECT MIN(id) FROM spans)")
    assert index_spans(conn, bow, log=lambda _: None)["embedded_now"] == 1  # changed: again
    assert has_index(conn, bow.model)
    top = dense_search(conn, bow, "rounding with USD", limit=3)
    text = conn.execute("SELECT text FROM spans WHERE id = ?", (top[0],)).fetchone()[0].lower()
    assert "usd" in text or "rounding" in text
    vec = np.asarray(bow.embed([QUERY_PREFIX + "x"])[0])
    assert vec.shape == (len(VOCAB) + 1,)


def test_search_respects_a_candidate_filter(fx, tmp_path):
    conn = _copy(fx, tmp_path)
    bow = BagOfWords()
    index_spans(conn, bow, log=lambda _: None)
    allowed = {r[0] for r in conn.execute("SELECT id FROM spans WHERE text NOT LIKE '%USD%'")}
    hits = dense_search(conn, bow, "USD rounding", limit=5, allowed=allowed.__contains__)
    assert hits and set(hits) <= allowed


def test_semantic_search_does_not_loosen_abstention(fx, tmp_path):
    conn = _copy(fx, tmp_path)
    bow = BagOfWords()
    index_spans(conn, bow, log=lambda _: None)

    def dense(q: str) -> list[int]:
        return dense_search(conn, bow, q)

    assert ask(conn, "Why do we charge in USD?", repo=fx.repo, dense=dense).status == "answered"
    unanswerable = "Why did we adopt Kubernetes service mesh sidecars?"
    assert ask(conn, unanswerable, repo=fx.repo).status == "not_found"
    assert ask(conn, unanswerable, repo=fx.repo, dense=dense).status == "not_found"


def test_semantic_search_failure_falls_back_to_keywords(fx):
    def broken(q: str) -> list[int]:
        raise ConnectionError("ollama is down")

    answer = ask(fx.conn(), "Why do we charge in USD?", repo=fx.repo, dense=broken)
    assert answer.status == "answered"
    assert any("semantic search unavailable" in n for n in answer.notes)


def _target(fx) -> str:
    probe = ask(fx.conn(), "Why do we charge in USD?", repo=fx.repo)
    return next(
        e.id for e in probe.evidence if "settles in USD" in e.snippet or "rounding" in e.snippet
    )


def test_support_check_keeps_supported_claims(fx):
    target = _target(fx)
    model = Scripted(
        {"claims": [{"text": "Charging in USD avoids rounding errors.", "cites": [target]}]},
        {"verdicts": [{"n": 1, "supported": True}]},
    )
    answer = ask(
        fx.conn(),
        "Why do we charge in USD?",
        repo=fx.repo,
        chain=Chain(fx.conn(), [model], "off"),
        support_check=True,
    )
    assert answer.mode == "llm" and len(answer.claims) == 1
    assert "model support check" in answer.checker
    assert "untrusted data" in model.prompts[1]


def test_support_check_can_only_remove_claims(fx):
    target = _target(fx)
    for verdict in ({"verdicts": [{"n": 1, "supported": False}]}, {"nonsense": True}, {}):
        model = Scripted(
            {"claims": [{"text": "Charging in USD avoids rounding errors.", "cites": [target]}]},
            verdict,
        )
        answer = ask(
            fx.conn(),
            "Why do we charge in USD?",
            repo=fx.repo,
            chain=Chain(fx.conn(), [model], "off"),
            support_check=True,
        )
        assert answer.mode == "extractive", verdict  # fell back to quoting the evidence
        assert any("claim(s) removed" in n for n in answer.notes), verdict
