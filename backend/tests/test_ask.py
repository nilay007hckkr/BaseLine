import json

import httpx

from hindsight.brain.ask import ask, code_evidence, excerpt, facts
from hindsight.core.llm import Chain
from hindsight.core.text import claim_terms


class FakeProvider:
    """Stands in for a model; records the prompt it was given."""

    name = "fake"
    model = "fake-1"

    def __init__(self, response: dict) -> None:
        self.response = response
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return json.dumps(self.response)


def test_answerable_question_cites_its_sources(fx):
    answer = ask(fx.conn(), "Why do we charge in USD?", repo=fx.repo)
    assert answer.status == "answered"
    ids = {e.id for e in answer.evidence}
    assert answer.claims and all(set(c.cites) <= ids for c in answer.claims)
    kinds = {e.kind for e in answer.evidence}
    assert "PR" in kinds or "Commit" in kinds
    assert all(e.url for e in answer.evidence)


def test_unanswerable_question_says_not_found(fx):
    answer = ask(fx.conn(), "Why was the payment service rewritten in Rust?", repo=fx.repo)
    assert answer.status == "not_found"
    assert answer.claims == []
    assert len(answer.nearest) <= 3
    assert any("rust" in n for n in answer.notes)


def test_blame_path_anchors_the_answer(fx):
    answer = ask(
        fx.conn(),
        "Why is the charge converted to USD here?",
        repo=fx.repo,
        file="src/paymentservice/charge.js",
        line_start=1,
        line_end=1,
    )
    assert answer.status == "answered"
    assert any(e.path and e.path[0] == "src/paymentservice/charge.js" for e in answer.evidence)


def test_pointing_at_lines_answers_from_their_history_even_without_matching_words(fx):
    answer = ask(
        fx.conn(),
        "Why is this zebra logic here?",
        repo=fx.repo,
        file="src/paymentservice/charge.js",
        line_start=1,
        line_end=1,
    )
    assert answer.status == "answered"
    assert any("git blame" in n for n in answer.notes)
    first = answer.evidence[0]
    assert first.kind == "Commit" and "Charge in USD" in first.title
    assert first.path[0] == "src/paymentservice/charge.js"
    assert any(
        e.node_key == "gh:12" for e in answer.evidence
    )  # the PR the blamed commit belongs to


def test_without_lines_the_same_question_is_not_found(fx):
    answer = ask(fx.conn(), "Why is this zebra logic here?", repo=fx.repo)
    assert answer.status == "not_found"


def test_llm_claims_with_bad_citations_are_dropped(fx):
    fake = FakeProvider(
        {
            "claims": [
                {
                    "text": "We charge in USD because the payment provider settles in USD.",
                    "cites": ["S99"],
                },
                {"text": "Quantum billing was chosen for blockchain reasons.", "cites": ["S1"]},
            ]
        }
    )
    answer = ask(
        fx.conn(), "Why do we charge in USD?", repo=fx.repo, chain=Chain(fx.conn(), [fake], "off")
    )
    assert answer.status == "answered"
    assert answer.mode == "extractive"
    assert any("failed the citation check" in n for n in answer.notes)


def test_llm_claims_that_pass_validation_are_kept(fx):
    probe = ask(fx.conn(), "Why do we charge in USD?", repo=fx.repo)
    target = next(
        e.id for e in probe.evidence if "settles in USD" in e.snippet or "rounding" in e.snippet
    )
    fake = FakeProvider(
        {"claims": [{"text": "Charging in USD avoids rounding errors.", "cites": [target]}]}
    )
    answer = ask(
        fx.conn(), "Why do we charge in USD?", repo=fx.repo, chain=Chain(fx.conn(), [fake], "off")
    )
    assert answer.mode == "llm"
    assert answer.claims[0].cites == [target]


class ThenOffline:
    """Answers the first prompt, then behaves like a model that stopped responding."""

    name, model, timeout = "ollama", "qwen2.5:7b", 300

    def __init__(self, first: dict) -> None:
        self.first: dict | None = first

    def complete(self, prompt: str) -> str:
        if self.first is None:
            raise httpx.ReadTimeout("slow", request=httpx.Request("POST", "http://localhost"))
        reply, self.first = self.first, None
        return json.dumps(reply)


def test_a_long_source_is_cut_to_its_subject_and_best_sentences():
    filler = "This paragraph talks about release notes and nothing else at all. " * 60
    text = (
        f"Charge in USD\n\n{filler}We charge in USD because the provider settles in USD. {filler}"
    )
    cut = excerpt(text, ["charge", "usd"], {}, 300)
    assert len(cut) <= 300
    assert cut.startswith("Charge in USD")
    assert "settles in USD" in cut
    assert excerpt("short text", ["usd"], {}, 300) == "short text"


def test_claims_that_passed_the_citation_check_stay_when_the_second_check_cannot_run(fx):
    probe = ask(fx.conn(), "Why do we charge in USD?", repo=fx.repo)
    target = next(
        e.id for e in probe.evidence if "settles in USD" in e.snippet or "rounding" in e.snippet
    )
    model = ThenOffline(
        {"claims": [{"text": "Charging in USD avoids rounding errors.", "cites": [target]}]}
    )
    answer = ask(
        fx.conn(),
        "Why do we charge in USD?",
        repo=fx.repo,
        chain=Chain(fx.conn(), [model], "off"),
        support_check=True,
    )
    assert answer.mode == "llm"
    assert [c.cites for c in answer.claims] == [[target]]
    assert any("could not run" in n for n in answer.notes)
    assert any("timed out after 300 s" in n for n in answer.notes)


def test_when_no_model_answers_the_notes_say_why(fx):
    class Down:
        name, model, host = "ollama", "qwen2.5:7b", "http://localhost:11434"

        def complete(self, prompt: str) -> str:
            raise httpx.ConnectError("refused", request=httpx.Request("POST", self.host))

    answer = ask(
        fx.conn(), "Why do we charge in USD?", repo=fx.repo, chain=Chain(fx.conn(), [Down()], "off")
    )
    assert answer.mode == "extractive" and answer.claims
    assert "no LLM provider answered; showing quoted evidence" in answer.notes
    assert any("is not reachable at http://localhost:11434" in n for n in answer.notes)


def test_a_question_about_a_file_adds_its_code_and_a_walkthrough(fx):
    model = FakeProvider(
        {"claims": [{"text": "The code charges the card in USD, then retries.", "cites": ["C1"]}]}
    )
    answer = ask(
        fx.conn(),
        "Why is the charge converted to USD here?",
        repo=fx.repo,
        file="src/paymentservice/charge.js",
        line_start=1,
        line_end=2,
        chain=Chain(fx.conn(), [model], "off"),
    )
    code = next(e for e in answer.evidence if e.id == "C1")
    assert code.kind == "Code" and code.title == "src/paymentservice/charge.js lines 1-2"
    assert "   1  // charge the card" in code.snippet
    assert "walk through the code in C1" in model.prompts[0]
    assert answer.mode == "llm" and answer.claims[0].cites == ["C1"]


def test_code_outside_the_repository_is_never_read(fx):
    assert code_evidence(fx.conn(), fx.repo, "../../../etc/passwd", None, ["root"]) is None
    assert code_evidence(fx.conn(), fx.repo, "src/missing.go", None, ["x"]) is None


def test_sources_carry_plain_facts_and_quotes_lead_with_them(fx):
    conn = fx.conn()
    key = conn.execute(
        "SELECT key FROM nodes WHERE kind = 'Commit' AND title LIKE 'Charge in USD%'"
    ).fetchone()[0]
    fact = facts(conn, key)
    assert fact.startswith(f"Commit {key.split(':')[1][:7]} by ")
    assert "changed main.go, charge.js: Charge in USD" in fact
    quoted = ask(conn, "Why do we charge in USD?", repo=fx.repo)  # no model: quoted
    assert quoted.mode == "extractive"
    assert any(c.text.startswith(("Commit ", "Pull request #", "Issue #")) for c in quoted.claims)


def test_explaining_words_are_not_required_in_the_cited_text():
    terms = claim_terms("We charge in USD because it helps avoid rounding errors.")
    assert "because" not in terms and "helps" not in terms
    assert {"charge", "usd", "rounding"} <= set(terms)


def test_flagged_text_never_reaches_the_model(fx):
    fake = FakeProvider({"claims": []})
    ask(
        fx.conn(),
        "Why was PR 12 approved for USD rounding?",
        repo=fx.repo,
        chain=Chain(fx.conn(), [fake], "off"),
    )
    assert fake.prompts
    assert all("Ignore previous instructions" not in p for p in fake.prompts)


def test_evidence_must_be_about_the_service_the_question_names(fx):
    """FIXTURE: USD charging is recorded for checkout and payment, never for the cart. A question
    that pins it on cartservice shares the words but not the subject, so it is not answered."""
    assert (
        ask(fx.conn(), "Why does checkoutservice charge in USD?", repo=fx.repo).status == "answered"
    )
    answer = ask(fx.conn(), "Why does cartservice charge in USD?", repo=fx.repo)
    assert answer.status == "not_found"
    assert any("not about cartservice" in n for n in answer.notes)


def test_a_commit_named_by_hash_is_read_like_git_blame(fx):
    conn = fx.conn()
    sha = fx.shas["usd"][:7]
    a = ask(conn, f"Why was src/checkoutservice/main.go changed in commit {sha}?", repo=fx.repo)
    assert a.status == "answered"
    keys = [e.node_key for e in a.evidence]
    assert f"commit:{fx.shas['usd']}" in keys and "gh:12" in keys and "gh:3" in keys
    assert any(f"history of commit {sha}" in n for n in a.notes)
    assert not any(sha in n for n in a.notes if n.startswith("no project history"))


def test_a_hash_that_names_no_commit_anchors_nothing(fx):
    conn = fx.conn()
    a = ask(conn, "Why was commit 0000000 deadbeef made in the cart?")
    assert not any(label.startswith("commit") for label in a.anchors)


def test_hashes_and_search_terms():
    from hindsight.core.text import content_terms, fts_term, is_sha

    assert is_sha("53a3d6e") and not is_sha("defaced") and not is_sha("abc12")
    assert content_terms("Why was it changed in commit 53a3d6e?") == []
    # porter stems 'rounding' to 'round', which the crude six-letter prefix 'roundi' misses
    assert fts_term("rounding") == '("rounding"* OR "roundi"*)' and fts_term("cart") == '"cart"*'


def test_text_from_bot_accounts_is_not_evidence(tmp_path):
    from hindsight.brain.ask import _skip_span
    from hindsight.core.db import connect, upsert_node

    conn = connect(tmp_path / "db.sqlite")
    bot = upsert_node(conn, "PR", "gh:1", title="bump tar", attrs={"user": "dependabot[bot]"})
    human = upsert_node(conn, "PR", "gh:2", title="Migrate to TypeScript", attrs={"user": "ana"})
    assert _skip_span(conn, bot) and not _skip_span(conn, human)


def test_a_source_must_say_the_question_s_most_specific_word(tmp_path):
    """FIXTURE: two commits share the common words of a false-premise question; a third, unrelated
    one is the only text that mentions its rare word. The answer is "not found"."""
    from hindsight.core.db import add_span, connect, upsert_node

    conn = connect(tmp_path / "db.sqlite")
    with conn:
        texts = [
            "Cart storage moved to a faster layer for the shop",
            "Cart storage moved behind an interface",
            "Bump the MongoDB driver used by an old tools script",
            "Fix typo in the README",
        ]
        for i, text in enumerate(texts):
            nid = upsert_node(conn, "Commit", f"commit:{i:040d}", title=text, ts=1_600_000_000 + i)
            add_span(conn, nid, "commit_msg", text, ts=1_600_000_000 + i)
    false_premise = ask(conn, "Why was cart storage moved to MongoDB?")
    assert false_premise.status == "not_found"
    assert any("'mongodb', the question's most specific word" in n for n in false_premise.notes)
    assert ask(conn, "Why was cart storage moved?").status == "answered"
