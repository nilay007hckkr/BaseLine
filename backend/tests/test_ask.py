import json

from hindsight.brain.ask import ask
from hindsight.core.llm import Chain


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
