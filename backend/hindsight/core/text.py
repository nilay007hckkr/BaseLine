"""Text helpers shared by retrieval and validation: tokens, crude stems, keyword coverage,
sentence splitting, and the instruction-like-text detector for untrusted content."""

from __future__ import annotations

import math
import re

_TOKEN = re.compile(r"[a-z0-9]+")
_HEX = frozenset("0123456789abcdef")
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n\s*\n|\n\s*[-*]\s+")
STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "being",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "doing",
        "done",
        "for",
        "from",
        "had",
        "has",
        "have",
        "having",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "may",
        "might",
        "more",
        "most",
        "must",
        "no",
        "not",
        "of",
        "on",
        "or",
        "our",
        "out",
        "over",
        "should",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "to",
        "too",
        "under",
        "until",
        "up",
        "upon",
        "us",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "also",
        "just",
        "only",
        "very",
        "via",
        "per",
        "any",
        "all",
        "each",
        "other",
        "same",
        "new",
        "old",
        "get",
        "got",
        "make",
        "made",
    ]
)
QUESTION_WORDS = frozenset(
    [
        "why",
        "what",
        "how",
        "who",
        "when",
        "where",
        "which",
        "code",
        "written",
        "write",
        "wrote",
        "built",
        "build",
        "way",
        "ways",
        "reason",
        "reasons",
        "explain",
        "explained",
        "decide",
        "decided",
        "decision",
        "chose",
        "choose",
        "chosen",
        "happen",
        "happened",
        # words that point at the history itself ("why was X changed in commit abc1234")
        "commit",
        "commits",
        "changed",
        "like",
        "work",
        "works",
        "working",
    ]
)
# Words that carry an explanation rather than a fact ("because", "which means", "helps"). A plain
# sentence for a newcomer uses many of them; they are not looked for in the cited text.
EXPLAIN_WORDS = frozenset(
    [
        "because",
        "since",
        "therefore",
        "thus",
        "hence",
        "means",
        "meaning",
        "meant",
        "help",
        "helps",
        "helped",
        "allow",
        "allows",
        "allowed",
        "lets",
        "let",
        "used",
        "uses",
        "using",
        "use",
        "instead",
        "rather",
        "without",
        "about",
        "first",
        "after",
        "before",
        "earlier",
        "later",
        "now",
        "keep",
        "keeps",
        "ensure",
        "ensures",
        "makes",
        "wanted",
        "needed",
        "needs",
        "order",
        "simply",
        "basically",
        "newcomer",
        "developer",
        "developers",
        "team",
        "project",
        "codebase",
        "part",
        "step",
        "steps",
        "here",
    ]
)

_INJECTION = re.compile(
    r"ignore (all |any )?(the )?(previous|prior|above|earlier) (instructions|prompts?)"
    r"|disregard (all |the )?(previous|prior|above)"
    r"|you are now (a|an|the)\b"
    r"|(reveal|print|show) (the |your )?system prompt"
    r"|act as (a|an|the) .{0,40}(assistant|model|ai)\b",
    re.IGNORECASE,
)


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def stem(token: str) -> str:
    """A deliberately crude stem: the first six characters of longer words, so 'rewritten',
    'rewrite' and 'rewriting' meet. Good enough for keyword coverage; FTS5 does its own stemming."""
    return token[:6] if len(token) > 6 else token


def is_sha(token: str) -> bool:
    """A commit hash as people paste it from git blame or a log (7 to 40 hex characters with at
    least one digit, so ordinary words such as 'defaced' are not mistaken for one)."""
    return (
        7 <= len(token) <= 40 and all(c in _HEX for c in token) and any(c.isdigit() for c in token)
    )


def content_terms(question: str) -> list[str]:
    seen: list[str] = []
    for tok in tokens(question):
        if len(tok) < 3 or tok in STOP or tok in QUESTION_WORDS or tok in seen or is_sha(tok):
            continue
        seen.append(tok)
    return seen


def fts_term(term: str) -> str:
    """One question term as an FTS5 (porter) query: the whole word, which the porter tokenizer
    stems ('rounding' → 'round'), or its crude six-character stem as a prefix ('rewrit*' meets
    'rewrite' and 'rewritten'). The crude stem alone misses words whose porter stem is shorter."""
    whole, crude = f'"{term}"*', f'"{stem(term)}"*'
    return whole if whole == crude else f"({whole} OR {crude})"


def claim_terms(claim: str) -> list[str]:
    """The terms of a model's claim that its cited text must contain: content words, without the
    words that only carry the explanation."""
    return [t for t in content_terms(claim) if t not in EXPLAIN_WORDS]


def stems(text: str) -> set[str]:
    return {stem(t) for t in tokens(text)}


def idf(df: int, n_docs: int) -> float:
    return math.log((n_docs + 1) / (df + 1)) + 1.0


def weighted_coverage(terms: list[str], weights: dict[str, float], text: str) -> float:
    """Share of the question's (IDF-weighted) content terms that appear in the text."""
    if not terms:
        return 0.0
    present = stems(text)
    total = sum(weights.get(t, 1.0) for t in terms)
    hit = sum(weights.get(t, 1.0) for t in terms if stem(t) in present)
    return hit / total if total else 0.0


_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")


def plain(text: str) -> str:
    """Markdown → readable plain text for quoted claims: links keep their label, table pipes go."""
    text = _MD_LINK.sub(r"\1", text)
    text = re.sub(r"\s*\|\s*", " · ", text).strip(" ·")
    return " ".join(text.replace("`", "").split())


def sentences(text: str) -> list[str]:
    parts = [plain(p.strip(" \t\n-*#>")) for p in _SENTENCE.split(text)]
    return [p for p in parts if len(p) >= 12]


def looks_like_instructions(text: str) -> bool:
    return bool(_INJECTION.search(text))


def snippet(text: str, terms: list[str], width: int = 320) -> str:
    """A window of the text around the first matching term."""
    flat = " ".join(text.split())
    if len(flat) <= width:
        return flat
    lowered = flat.lower()
    positions = [lowered.find(stem(t)) for t in terms]
    hits = [p for p in positions if p >= 0]
    start = max(0, min(hits) - width // 4) if hits else 0
    end = min(len(flat), start + width)
    return ("…" if start else "") + flat[start:end] + ("…" if end < len(flat) else "")
