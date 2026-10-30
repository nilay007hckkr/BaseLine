"""Tickets → test specs (DESIGN §5.1 and §5.2), rule-based.

A ticket (target/tickets/*.yaml) cites where it comes from in the target repository (a GitHub issue
or PR number, or a commit) and lists Given/When/Then lines in a small fixed grammar. Each line
becomes one step; "And" continues the previous keyword. A line outside the grammar rejects the
ticket. The assertion lint checks that every assertion comes from a Then line and every Then line
produced exactly one assertion. Assertions are never changed after generation (the healer only
replaces click locators)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

_GOTO = re.compile(r'^the customer opens the page "(?P<path>/[^"]*)"$', re.IGNORECASE)
_CLICK = re.compile(
    r'^the customer clicks the (?P<role>button|link) "(?P<name>[^"]+)"$', re.IGNORECASE
)
_EXPECT = re.compile(
    r'^the (?P<what>heading|text) "(?P<value>[^"]+)" is shown(?: within (?P<s>\d+(?:\.\d+)?) seconds)?$',
    re.IGNORECASE,
)
_KEYWORD = re.compile(r"^(Given|When|Then|And)\s+(.*)$", re.IGNORECASE)
DEFAULT_WAIT_S = 5.0


class Locator(BaseModel):
    role: str = ""  # ARIA role, used with the exact accessible name
    name: str = ""
    css: str = ""  # last resort, flagged (DESIGN §5.2)

    def describe(self) -> str:
        if self.role:
            return f'role={self.role} name="{self.name}"'
        return f"css={self.css} (flagged: CSS locator)"


class Step(BaseModel):
    op: Literal["goto", "click", "expect_heading", "expect_text"]
    criterion: int  # index of the ticket line this step comes from
    line: str
    path: str = ""
    target: Locator | None = None
    value: str = ""
    within_s: float = DEFAULT_WAIT_S
    fingerprint: dict[str, Any] | None = None  # saved from a passing run (click steps)


class Source(BaseModel):
    github: int | None = None
    commit: str | None = None

    def key(self) -> str:
        return f"gh:{self.github}" if self.github is not None else f"commit:{self.commit}"


class Ticket(BaseModel):
    id: str = Field(pattern=r"^[A-Z0-9][A-Z0-9-]*$")
    title: str
    source: Source
    services: list[str]
    acceptance_criteria: list[str]


class Spec(BaseModel):
    id: str
    title: str
    source: str  # graph key of the issue, PR or commit the ticket comes from
    services: list[str]
    steps: list[Step]
    version: int = 1


class TicketError(ValueError):
    pass


def load_ticket(path: Path) -> Ticket:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise TicketError(f"{path.name}: not a ticket")
    return Ticket.model_validate(data)


def generate(ticket: Ticket) -> Spec:
    steps: list[Step] = []
    keyword = ""
    problems: list[str] = []
    for i, raw in enumerate(ticket.acceptance_criteria):
        m = _KEYWORD.match(raw.strip())
        if not m:
            problems.append(f"line {i + 1} has no Given/When/Then/And: {raw!r}")
            continue
        word, rest = m.group(1).capitalize(), m.group(2).strip()
        if word != "And":
            keyword = word
        elif not keyword:
            problems.append(f"line {i + 1} starts with And but nothing precedes it")
            continue
        if keyword == "Then":
            e = _EXPECT.match(rest)
            if not e:
                problems.append(f"line {i + 1} is not an assertion this generator knows: {raw!r}")
                continue
            op: Literal["expect_heading", "expect_text"] = (
                "expect_heading" if e.group("what").lower() == "heading" else "expect_text"
            )
            steps.append(
                Step(
                    op=op,
                    criterion=i,
                    line=raw,
                    value=e.group("value"),
                    target=Locator(role="heading", name=e.group("value"))
                    if op == "expect_heading"
                    else None,
                    within_s=float(e.group("s") or DEFAULT_WAIT_S),
                )
            )
        elif g := _GOTO.match(rest):
            steps.append(Step(op="goto", criterion=i, line=raw, path=g.group("path")))
        elif c := _CLICK.match(rest):
            steps.append(
                Step(
                    op="click",
                    criterion=i,
                    line=raw,
                    target=Locator(role=c.group("role").lower(), name=c.group("name")),
                )
            )
        else:
            problems.append(f"line {i + 1} is not an action this generator knows: {raw!r}")
    spec = Spec(
        id=ticket.id,
        title=ticket.title,
        source=ticket.source.key(),
        services=ticket.services,
        steps=steps,
    )
    problems += lint(spec, ticket)
    if problems:
        raise TicketError(f"{ticket.id}: " + "; ".join(problems))
    return spec


def then_lines(ticket: Ticket) -> set[int]:
    lines, keyword = set(), ""
    for i, raw in enumerate(ticket.acceptance_criteria):
        word = raw.strip().split(" ", 1)[0].capitalize()
        if word in ("Given", "When", "Then"):
            keyword = word
        if keyword == "Then":
            lines.add(i)
    return lines


def lint(spec: Spec, ticket: Ticket) -> list[str]:
    """Every assertion traces to a Then line; every Then line has exactly one assertion."""
    thens = then_lines(ticket)
    problems = []
    asserted: dict[int, int] = {}
    for step in spec.steps:
        if step.op.startswith("expect"):
            if step.criterion not in thens:
                problems.append(f"assertion {step.line!r} does not come from a Then line")
            asserted[step.criterion] = asserted.get(step.criterion, 0) + 1
        elif step.criterion in thens:
            problems.append(f"action {step.line!r} sits under Then")
    for i in sorted(thens):
        if asserted.get(i, 0) != 1:
            problems.append(f"Then line {i + 1} has {asserted.get(i, 0)} assertions, expected 1")
    if not any(s.op.startswith("expect") for s in spec.steps):
        problems.append("the ticket asserts nothing")
    return problems


def render(spec: Spec) -> str:
    """The spec as a readable Playwright test (what a reviewer sees, and what repair patches change)."""
    lines = [
        f"# {spec.id}: {spec.title}",
        f"# Source: {spec.source}. Generated by SentinelQA from the ticket; assertions are never edited.",
        "from playwright.sync_api import Page, expect",
        "",
        "",
        f"def test_{re.sub(r'[^a-z0-9]+', '_', spec.id.lower())}(page: Page, base_url: str) -> None:",
    ]
    for step in spec.steps:
        lines.append(f"    # {step.line}")
        if step.op == "goto":
            lines.append(f'    page.goto(base_url + "{step.path}")')
        elif step.op == "click" and step.target:
            lines.append(f"    {_locator_code(step.target)}.click()")
        elif step.op == "expect_heading" and step.target:
            lines.append(
                f"    expect({_locator_code(step.target)}).to_be_visible(timeout={int(step.within_s * 1000)})"
            )
        elif step.op == "expect_text":
            lines.append(
                f"    expect(page.get_by_text({step.value!r}).first).to_be_visible("
                f"timeout={int(step.within_s * 1000)})"
            )
    return "\n".join(lines) + "\n"


def _locator_code(loc: Locator) -> str:
    if loc.role:
        return f'page.get_by_role("{loc.role}", name={loc.name!r}, exact=True)'
    return f"page.locator({loc.css!r})"
