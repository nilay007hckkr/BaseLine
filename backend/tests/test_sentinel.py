"""SentinelQA: ticket grammar and lint, similarity and refusal rules, mutations, grounding, and
(integration) the healer in a real browser against the recorded shop pages in testdata/shop
(FIXTURE: recorded from the local demo shop)."""

import http.server
import json
import shutil
import threading
import uuid
from collections.abc import Iterator
from typing import ClassVar

import pytest

from hindsight.core.config import ROOT
from hindsight.core.db import connect
from hindsight.sentinel.fingerprint import THETA, choose, similarity
from hindsight.sentinel.mutations import CART, ORDER, PRODUCT, TRUTH, catalogue
from hindsight.sentinel.spec import Spec, TicketError, generate, lint, load_ticket, render
from hindsight.sentinel.store import register

TICKETS = ROOT / "target" / "tickets"
SHOP = ROOT / "testdata" / "shop"


def ticket(tid: str):
    return load_ticket(TICKETS / f"{tid}.yaml")


# ---------- tickets → specs ----------


def test_every_ticket_in_the_repository_generates():
    for path in sorted(TICKETS.glob("*.yaml")):
        spec = generate(load_ticket(path))
        assert spec.steps and any(s.op.startswith("expect") for s in spec.steps)
        assert "assertions are never edited" in render(spec)


def test_ticket_from_issue_3578_has_one_assertion_per_then_line():
    spec = generate(ticket("OB-3578"))
    assert [s.op for s in spec.steps] == [
        "goto",
        "click",
        "click",
        "expect_heading",
        "expect_text",
        "goto",
        "expect_heading",
    ]
    assert spec.source == "gh:3578"


def test_lines_outside_the_grammar_reject_the_ticket():
    t = ticket("OB-3474").model_copy(deep=True)
    t.acceptance_criteria.append("Then the customer feels happy")
    with pytest.raises(TicketError, match="not an assertion"):
        generate(t)


def test_lint_rejects_an_assertion_that_no_then_line_asked_for():
    t = ticket("OB-3474")
    spec = generate(t)
    forged = Spec.model_validate(spec.model_dump())
    forged.steps[3].criterion = 1  # pretend the assertion came from a Given/And line
    problems = lint(forged, t)
    assert any("does not come from a Then line" in p for p in problems)
    assert any("has 0 assertions" in p for p in problems)


# ---------- similarity, θ and δ ----------

ORIGINAL = {
    "tag": "button",
    "role": "button",
    "name": "Empty Cart",
    "text": "Empty Cart",
    "id": "",
    "classes": ["cymbal-button-secondary", "cart-summary-empty-cart-button"],
    "testid": "",
    "parent": "form.",
    "neighbours": ["Continue Shopping"],
    "box": [0.62, 0.21],
}


@pytest.mark.parametrize(
    ("change", "above_theta"),
    [
        ({}, True),
        ({"name": "Clear Cart", "text": "Clear Cart"}, True),  # relabelled, same control
        ({"classes": ["btn-alt"], "box": [0.6, 0.22]}, True),  # restyled
        (
            {
                "tag": "a",
                "name": "Continue Shopping",
                "text": "Continue Shopping",
                "classes": ["cymbal-button-primary"],
                "neighbours": [],
                "box": [0.7, 0.21],
            },
            False,
        ),  # the neighbouring control is not the same control
    ],
)
def test_similarity_table(change, above_theta):
    score, parts = similarity(ORIGINAL, {**ORIGINAL, **change})
    assert (score >= THETA) is above_theta, (score, parts)


def test_two_equally_good_candidates_are_refused():
    twin = {**ORIGINAL, "name": "Clear Cart", "text": "Clear Cart", "visible": True}
    choice = choose(ORIGINAL, [twin, dict(twin)])
    assert choice.index is None and "within δ" in choice.reason


def test_nothing_clickable_is_refused():
    assert choose(ORIGINAL, [{**ORIGINAL, "role": "heading", "visible": True}]).index is None


# ---------- mutations on the recorded pages ----------

PAGES = {PRODUCT: ["product.html"], CART: ["cart.html", "empty.html"], ORDER: ["done.html"]}


def test_every_mutation_changes_a_recorded_page():
    mutations = catalogue()
    assert sum(m.kind == "cosmetic" for m in mutations) >= 20
    assert sum(m.kind == "defect" for m in mutations) >= 5
    for m in mutations:
        if m.rewrite is None:
            assert m.fail_post
            continue
        pages = [(SHOP / f).read_text() for f in PAGES[m.page]]
        changed = [m.rewrite(html) for html in pages]
        assert any(a != b for a, b in zip(pages, changed, strict=True)), m.id
        if m.kind == "cosmetic":
            assert any(TRUTH in c for c in changed), m.id


# ---------- grounding in the repository and the graph ----------


def test_register_links_the_test_to_its_source_services_and_code(fx, tmp_path):
    db = tmp_path / "copy.db"
    shutil.copy(fx.settings.db_path, db)
    conn = connect(db)
    path = tmp_path / "SHOP-9.yaml"
    path.write_text(
        "id: SHOP-9\ntitle: Payment page explains the currency (FIXTURE)\nsource: {github: 3}\n"
        "services: [checkoutservice, paymentservice]\nacceptance_criteria:\n"
        '  - Given the customer opens the page "/"\n'
        '  - When the customer clicks the button "Pay"\n'
        '  - Then the text "charge the card in USD" is shown\n'
    )
    with conn:
        out = register(conn, path, fx.repo)
    assert out == {"id": "SHOP-9", "status": "registered", "grounded": 1, "texts": 2}
    edges = conn.execute(
        "SELECT e.kind, e.provenance, n.key, e.evidence FROM edges e JOIN nodes t ON t.id = e.src"
        " JOIN nodes n ON n.id = e.dst WHERE t.key = 'test:SHOP-9' ORDER BY e.kind, n.key"
    ).fetchall()
    kinds = [(r[0], r[1], r[2]) for r in edges]
    assert ("DERIVED_FROM", "explicit", "gh:3") in kinds
    assert ("COVERS", "explicit", "service:paymentservice") in kinds
    exercised = [json.loads(r[3]) for r in edges if r[0] == "EXERCISES"]
    assert exercised[0]["file"] == "src/paymentservice/charge.js"
    assert exercised[0]["introduced_by"] == fx.shas["usd"]


def test_register_rejects_a_ticket_whose_source_is_not_in_the_graph(fx, tmp_path):
    db = tmp_path / "copy.db"
    shutil.copy(fx.settings.db_path, db)
    conn = connect(db)
    path = tmp_path / "SHOP-10.yaml"
    path.write_text(
        "id: SHOP-10\ntitle: x (FIXTURE)\nsource: {github: 999}\nservices: []\nacceptance_criteria:\n"
        '  - Given the customer opens the page "/"\n  - Then the text "Shop" is shown\n'
    )
    with conn:
        out = register(conn, path, fx.repo)
    assert out["status"] == "rejected" and "gh:999" in out["reason"]


# ---------- the healer in a real browser (integration: needs Playwright's Chromium) ----------


class _Shop(http.server.BaseHTTPRequestHandler):
    """Serves the recorded pages with the shop's routes; a cookie keeps each browser's cart."""

    carts: ClassVar[dict[str, int]] = {}

    def _session(self) -> str:
        for part in (self.headers.get("Cookie") or "").split(";"):
            if part.strip().startswith("sid="):
                return part.strip()[4:]
        return ""

    def _send(self, status: int, page: str | None = None, location: str = "") -> None:
        sid = self._session() or uuid.uuid4().hex
        self.send_response(status)
        self.send_header("Set-Cookie", f"sid={sid}; Path=/")
        if location:
            self.send_header("Location", location)
        body = (SHOP / page).read_bytes() if page else b""
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        sid = self._session()
        if self.path == "/":
            self._send(200, "home.html")
        elif self.path.startswith("/product/"):
            self._send(200, "product.html")
        elif self.path == "/cart":
            self._send(200, "cart.html" if self.carts.get(sid) else "empty.html")
        else:
            self._send(404)

    def do_POST(self) -> None:
        sid = self._session()
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path == "/cart":
            self.carts[sid] = 1
            self._send(302, location="/cart")
        elif self.path == "/cart/empty":
            self.carts[sid] = 0
            self._send(302, location="/cart")
        elif self.path == "/cart/checkout":
            self.carts[sid] = 0
            self._send(200, "done.html")
        else:
            self._send(404)

    def log_message(self, *args: object) -> None:
        return


@pytest.fixture(scope="module")
def shop() -> Iterator[str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Shop)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.mark.integration
def test_healer_heals_cosmetic_changes_and_refuses_defects(shop, browser):
    from hindsight.eval.heal import classify, validate
    from hindsight.sentinel.runner import Options, run

    mutations = {m.id: m for m in catalogue()}
    specs = {}
    for tid in ("OB-289BD4D-CART", "OB-3578"):
        ok, spec, reasons = validate(browser, generate(ticket(tid)), shop)
        assert ok, reasons
        assert all(s.fingerprint for s in spec.steps if s.op == "click")
        specs[tid] = spec

    def outcome(mid: str, tid: str, heal: str = "similo") -> tuple[str, dict]:
        install, state = mutations[mid].installer()
        result = run(browser, specs[tid], Options(shop, heal=heal, install=install, reruns=1))
        assert state["applied"], mid
        return classify(mutations[mid], result), result

    cls, result = outcome("empty-cart:synonym", "OB-289BD4D-CART")
    assert cls == "correct_heal", result["reason"]
    assert result["repairs"][0]["new_locator"] == {
        "role": "button",
        "name": "Clear Cart",
        "css": "",
    }
    assert outcome("empty-cart:synonym", "OB-289BD4D-CART", heal="none")[0] == "missed"
    assert outcome("empty-cart:wrapped", "OB-289BD4D-CART")[0] == "passed"
    for defect in ("empty-cart:removed", "add-to-cart:removed"):
        assert outcome(defect, "OB-289BD4D-CART")[0] == "correct_refusal", defect
    for defect in ("place-order:disabled", "checkout:http-500", "cart:kept-after-order"):
        cls, result = outcome(defect, "OB-3578")
        assert cls == "correct_refusal" and result["outcome"] == "failed", (
            defect,
            result["reason"],
        )
