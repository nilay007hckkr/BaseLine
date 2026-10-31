from hindsight.brain.impact import impact_report
from hindsight.brain.risk import knowledge_at_risk
from hindsight.eval.impact_replay import run


def _targets(items):
    return {i.target: i for i in items}


def test_impact_reasons_come_from_evidence(fx):
    report = impact_report(fx.conn(), files=["src/checkoutservice/main.go"])
    files = _targets(report.files)
    charge = files["src/paymentservice/charge.js"]
    assert charge.reasons[0].provenance == "statistical"
    assert (
        "changed together with src/checkoutservice/main.go in 4 of 6 commits"
        in charge.reasons[0].text
    )
    components = _targets(report.components)
    assert components["checkoutservice"].score == 1.0
    assert any(r.provenance == "static" for r in components["paymentservice"].reasons)
    assert any("frontend calls checkoutservice" in r.text for r in components["frontend"].reasons)
    assert any("README.md" in d.target and "Checkout" in d.target for d in report.docs)
    assert "Ana Lima" in _targets(report.people)


def test_impact_of_a_commit_uses_only_earlier_history(fx):
    """Leakage check: late.go only ever changes with main.go after the 'log' commit."""
    now = _targets(impact_report(fx.conn(), files=["src/checkoutservice/main.go"]).files)
    assert "src/checkoutservice/late.go" in now
    then = impact_report(fx.conn(), commit=fx.shas["log"])
    assert then.as_of_ts is not None
    assert "src/checkoutservice/late.go" not in _targets(then.files)
    assert any("only history before" in n for n in then.notes)


def test_unknown_commit_is_reported_not_guessed(fx):
    report = impact_report(fx.conn(), commit="deadbeef")
    assert report.files == [] and report.notes == ["commit deadbeef not found"]


def test_bot_and_sweep_commits_do_not_count(fx):
    report = impact_report(fx.conn(), files=["src/paymentservice/charge.js"])
    assert "go.sum" not in _targets(report.files)
    assert not any(t.startswith("docs/licences/") for t in _targets(report.files))


def test_knowledge_at_risk_finds_single_owner_files(fx):
    report = knowledge_at_risk(fx.conn())
    entry = next(f for f in report.files if f.path == "src/cartservice/cart.cs")
    assert entry.single_owner and entry.top_author == "Ben Okafor" and entry.changes == 3


def test_replay_evaluation_is_deterministic(fx):
    first = run(fx.conn(), out_dir=None, test_share=0.5)
    second = run(fx.conn(), out_dir=None, test_share=0.5)
    assert first["n_queries"] >= 1
    assert set(first["methods"]) == {
        "hindsight",
        "cochange_only",
        "same_directory",
        "most_frequent",
    }
    assert first["methods"] == second["methods"]
    # one row per held-out change, and the paired difference computed from exactly those rows
    assert len(first["rows"]) == first["n_queries"]
    r10 = [r["hindsight"]["R@10"] - r["cochange_only"]["R@10"] for r in first["rows"]]
    paired = first["paired_hindsight_minus_cochange"]["R@10"]
    assert abs(paired["value"] - sum(r10) / len(r10)) < 1e-3
    assert paired["ci_low"] <= paired["value"] <= paired["ci_high"]
    assert "code_sha" in first
