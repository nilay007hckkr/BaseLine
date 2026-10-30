"""Element fingerprints and the Similo-style similarity used by the healer (DESIGN §5.3).

A fingerprint records what a person would use to recognise a control: tag, role, accessible name,
visible text, id, classes, test id, the parent's signature, the texts next to it, and where it sits
on the page. Similarity is a weighted sum over the features the original element had: exact match
for tag/role/id/test id/parent, normalised edit similarity for name and text, Jaccard for classes and
neighbours, and distance for the position."""

from __future__ import annotations

import math
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

THETA = 0.75  # accept only a candidate this similar…
DELTA = 0.15  # …and this much better than the runner-up

WEIGHTS = {
    "tag": 0.10,
    "role": 0.10,
    "name": 0.20,
    "text": 0.10,
    "id": 0.05,
    "testid": 0.05,
    "classes": 0.10,
    "parent": 0.10,
    "neighbours": 0.10,
    "box": 0.10,
}

# Elements a click step can be healed onto (a link and a button are interchangeable for a person).
CANDIDATES = (
    "a[href], button, input[type=submit], input[type=button], input[type=reset],"
    " [role=button], [role=link]"
)
CLICKABLE_ROLES = {"button", "link"}

_FP_BODY = """
const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
const role = (e) => {
  const explicit = e.getAttribute('role');
  if (explicit) return explicit;
  const t = e.tagName.toLowerCase();
  if (t === 'a' && e.hasAttribute('href')) return 'link';
  if (t === 'button') return 'button';
  if (t === 'input') {
    const ty = (e.getAttribute('type') || 'text').toLowerCase();
    return ['submit', 'button', 'reset'].includes(ty) ? 'button' : 'textbox';
  }
  if (/^h[1-6]$/.test(t)) return 'heading';
  return '';
};
const fp = (e) => {
  const text = norm(e.innerText || e.textContent);
  const name = norm(e.getAttribute('aria-label'))
    || (e.tagName === 'INPUT' ? norm(e.value || e.getAttribute('value')) : '')
    || text || norm(e.getAttribute('title'))
    || norm((e.querySelector('img[alt]') || {}).alt);
  const p = e.parentElement;
  const neighbours = p ? Array.from(p.children).filter((s) => s !== e)
    .map((s) => norm(s.innerText || s.textContent).slice(0, 40)).filter(Boolean).slice(0, 6) : [];
  const r = e.getBoundingClientRect();
  const W = Math.max(document.documentElement.scrollWidth, window.innerWidth) || 1;
  const H = Math.max(document.documentElement.scrollHeight, window.innerHeight) || 1;
  return {
    tag: e.tagName.toLowerCase(), role: role(e), name: name.slice(0, 80), text: text.slice(0, 80),
    id: e.id || '', classes: Array.from(e.classList),
    testid: e.getAttribute('data-testid') || '',
    parent: p ? p.tagName.toLowerCase() + '.' + Array.from(p.classList).sort().join('.') : '',
    neighbours,
    box: [(r.left + window.scrollX + r.width / 2) / W, (r.top + window.scrollY + r.height / 2) / H],
    disabled: e.disabled === true || e.getAttribute('aria-disabled') === 'true',
    visible: !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length),
    // evaluation only: set by the mutation harness on the element a heal should find; never scored
    _truth: e.getAttribute('data-hs-truth') === '1',
  };
};
"""

FINGERPRINT_JS = f"(e) => {{ {_FP_BODY} return fp(e); }}"
CANDIDATES_JS = (
    f"(sel) => {{ {_FP_BODY} return Array.from(document.querySelectorAll(sel)).map(fp); }}"
)


def stored(fp: dict[str, Any]) -> dict[str, Any]:
    """What is saved with a test: everything except evaluation-only and per-run fields."""
    return {
        k: v for k, v in fp.items() if not k.startswith("_") and k not in ("disabled", "visible")
    }


def _edit(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)


def _box(a: list[float], b: list[float]) -> float:
    d = math.dist(a, b)
    return max(0.0, 1.0 - d / 0.25)


def similarity(
    original: dict[str, Any], candidate: dict[str, Any]
) -> tuple[float, dict[str, float]]:
    """Weighted similarity in [0, 1] and the per-feature breakdown. Features the original element
    did not have (no id, no test id, no neighbours) are left out and the weights renormalised."""
    parts: dict[str, float] = {}
    for feature in WEIGHTS:
        a, b = original.get(feature), candidate.get(feature)
        if feature in ("id", "testid", "neighbours", "classes") and not a:
            continue
        if feature in ("tag", "role", "id", "testid", "parent"):
            parts[feature] = 1.0 if a == b else 0.0
        elif feature in ("name", "text"):
            parts[feature] = _edit(str(a or ""), str(b or ""))
        elif feature in ("classes", "neighbours"):
            parts[feature] = _jaccard(list(a or []), list(b or []))
        elif feature == "box":
            parts[feature] = _box(list(a or [0, 0]), list(b or [0, 0]))
    total = sum(WEIGHTS[f] for f in parts)
    score = sum(WEIGHTS[f] * v for f, v in parts.items()) / total if total else 0.0
    return round(score, 4), {f: round(v, 3) for f, v in parts.items()}


@dataclass
class Choice:
    index: int | None  # candidate index, or None when the healer refuses
    score: float
    runner_up: float
    breakdown: dict[str, float]
    ranked: list[tuple[int, float]]  # top candidates, for the repair record
    reason: str


def choose(
    original: dict[str, Any],
    candidates: list[dict[str, Any]],
    theta: float = THETA,
    delta: float = DELTA,
) -> Choice:
    scored = []
    for i, c in enumerate(candidates):
        if not c.get("visible") or c.get("role") not in CLICKABLE_ROLES:
            continue
        score, parts = similarity(original, c)
        scored.append((score, i, parts))
    scored.sort(key=lambda t: (-t[0], t[1]))
    if not scored:
        return Choice(None, 0.0, 0.0, {}, [], "no visible button or link on the page")
    top, idx, parts = scored[0]
    second = scored[1][0] if len(scored) > 1 else 0.0
    ranked = [(i, s) for s, i, _ in scored[:5]]
    if top < theta:
        return Choice(
            None, top, second, parts, ranked, f"best candidate scores {top:.2f} < θ {theta}"
        )
    if top - second < delta:
        return Choice(
            None,
            top,
            second,
            parts,
            ranked,
            f"best {top:.2f} is within δ {delta} of the next ({second:.2f})",
        )
    return Choice(idx, top, second, parts, ranked, f"score {top:.2f}, next {second:.2f}")


def naive(original: dict[str, Any], candidates: list[dict[str, Any]]) -> int | None:
    """Baseline (EVALUATION §4): the first element with the same tag and the same text."""
    for i, c in enumerate(candidates):
        if (
            c.get("visible")
            and c.get("tag") == original.get("tag")
            and c.get("text") == original.get("text")
        ):
            return i
    return None
