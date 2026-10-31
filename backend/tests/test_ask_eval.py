"""The why-answer evaluation: generated answer key, scorer counts, Proof cards. FIXTURE data only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hindsight.api.service import scorecard
from hindsight.core.db import connect
from hindsight.core.schemas import Answer, Claim, EvidenceItem
from hindsight.eval.ask_eval import clean_subject, generate, score


def test_subject_loses_issue_numbers_and_closing_keywords():
    assert clean_subject("Charge in USD (#12)") == "Charge in USD"
    assert clean_subject("Fixes #3: rounding in checkout") == "rounding in checkout"


def test_answer_key_comes_from_explicit_fix_links(fx):
    conn = connect(fx.settings.db_path)
    qs = generate(conn)
    conn.close()
    usd = [q for q in qs if "gh:3" in q["gold"]]
    assert usd, qs  # PR 12 says "Fixes #3"; its commit becomes a question
    q = usd[0]
    assert "#3" not in q["question"] and "#12" not in q["question"]
    assert q["file"] and q["file"] in q["question"] and q["commit"][:7] in q["question"]


def _evidence(key: str) -> EvidenceItem:
    return EvidenceItem(
        id="S1", node_key=key, kind="Issue", span_kind="issue_body", title="t", url="u", snippet="s"
    )


def _fake_ask(conn: Any, question: str, **_: Any) -> Answer:
    if "Rust" in question:
        return Answer(status="not_found", mode="none", question=question, checker="test")
    if "wrong" in question:  # answered, but from the wrong source
        return Answer(
            status="answered",
            mode="extractive",
            question=question,
            checker="test",
            evidence=[_evidence("gh:99")],
            claims=[Claim(text="x", cites=["S1"])],
        )
    return Answer(
        status="answered",
        mode="extractive",
        question=question,
        checker="test",
        evidence=[_evidence("gh:3")],
        claims=[Claim(text="Settles in USD.", cites=["S1"])],
    )


def test_scorer_counts_and_writes_results(fx, tmp_path: Path):
    conn = connect(fx.settings.db_path)
    answerable = [
        {"id": "q1", "question": "Why charge in USD?", "gold": ["gh:3"]},
        {"id": "q2", "question": "Why the wrong thing?", "gold": ["gh:3"]},
    ]
    trick = [{"id": "t1", "question": "Why was paymentservice rewritten in Rust?"}]
    result = score(
        conn, answerable, trick, repo=None, chain=None, ask_fn=_fake_ask, out_dir=tmp_path
    )
    conn.close()
    ours = result["sets"]["answerable"]["methods"]["hindsight"]
    assert result["sets"]["answerable"]["n"] == 2
    assert ours["answered"]["value"] == 1.0
    assert ours["gold_in_sources"]["value"] == 0.5 and ours["gold_cited"]["value"] == 0.5
    assert result["sets"]["trick"]["methods"]["hindsight"]["not_found"]["value"] == 1.0
    # no model configured: the plain-model baseline is not run, and not reported as 0
    assert "model_without_sources" not in result["sets"]["trick"]["methods"]
    assert Path(result["result_file"]).exists()
    assert list(tmp_path.glob("ask-handcheck-*.csv"))

    cards = {c.metric: c for c in scorecard(tmp_path) if c.suite == "ask"}
    assert cards["Not found on trick questions"].value == 1.0
    assert cards["Linked issue cited"].n == 2
    assert {m.name for m in cards["Linked issue among the sources"].methods} == {
        "hindsight",
        "keyword_only",
    }


def test_missing_trick_questions_mean_not_measured(fx, tmp_path: Path):
    conn = connect(fx.settings.db_path)
    answerable = [{"id": "q1", "question": "Why charge in USD?", "gold": ["gh:3"]}]
    result = score(conn, answerable, [], repo=None, chain=None, ask_fn=_fake_ask, out_dir=tmp_path)
    conn.close()
    assert "trick" not in result["sets"]
    cards = [c.metric for c in scorecard(tmp_path) if c.suite == "ask"]
    assert "Not found on trick questions" not in cards
    saved = json.loads(Path(result["result_file"]).read_text())
    assert saved["code_sha"] is not None and saved["rows"][0]["gold"] == ["gh:3"]


def test_the_real_answers_on_the_fixture(fx, tmp_path: Path):
    conn = connect(fx.settings.db_path)
    qs = [q for q in generate(conn) if "gh:3" in q["gold"]]
    trick = [{"id": "t1", "question": "Why was paymentservice rewritten in Rust?"}]
    result = score(conn, qs, trick, repo=fx.repo, chain=None)
    conn.close()
    rows = {r["set"]: r for r in result["rows"]}
    assert rows["answerable"]["gold_in_sources"], rows["answerable"]
    assert rows["trick"]["status"] == "not_found"
