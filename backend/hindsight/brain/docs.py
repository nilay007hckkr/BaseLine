"""In-repo Markdown docs: heading-based chunks that keep their section path. Code fences are never
split. Chunks are linked to the services and files they name (DESIGN §4)."""

from __future__ import annotations

import re

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
MAX_WORDS = 450


def is_doc(path: str) -> bool:
    lowered = path.lower()
    if not lowered.endswith(".md"):
        return False
    return not any(part in lowered for part in ("node_modules/", "vendor/", ".github/"))


def chunk_markdown(text: str, max_words: int = MAX_WORDS) -> list[tuple[str, str]]:
    """Return (section_path, body) chunks. A section longer than max_words is split at blank
    lines outside code fences."""
    chunks: list[tuple[str, str]] = []
    stack: list[tuple[int, str]] = []
    body: list[str] = []
    in_fence = False

    def flush() -> None:
        content = "\n".join(body).strip()
        if not content:
            return
        section = " > ".join(title for _, title in stack) or "(top)"
        for part in _split_long(content, max_words):
            chunks.append((section, part))

    for line in text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        heading = None if in_fence else _HEADING.match(line)
        if heading:
            flush()
            body = []
            level, title = len(heading.group(1)), heading.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            continue
        body.append(line)
    flush()
    return chunks


def _split_long(content: str, max_words: int) -> list[str]:
    if len(content.split()) <= max_words:
        return [content]
    parts: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in content.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        current.append(line)
        if not in_fence and not line.strip() and len(" ".join(current).split()) >= max_words:
            parts.append("\n".join(current).strip())
            current = []
    if current and "\n".join(current).strip():
        parts.append("\n".join(current).strip())
    return parts


def mentions(text: str, names: dict[str, str]) -> list[str]:
    """Node keys whose name occurs in the text as a whole word (case-insensitive)."""
    lowered = text.lower()
    found = []
    for name, key in names.items():
        if re.search(rf"(?<![\w/.-]){re.escape(name.lower())}(?![\w-])", lowered):
            found.append(key)
    return found
