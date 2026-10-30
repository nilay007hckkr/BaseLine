"""Uploaded documents (PRD F7, DESIGN §4): PDF and Markdown, versioned, chunked by heading, linked
to the services and files they name, runbook sections tied to their service, and instruction-like
passages flagged. A document with no extractable text is kept and marked as needing attention;
it is never indexed as empty. Uploaded text stays local by default (DOCS_ALLOW_EXTERNAL=false)."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import statistics
import time
from pathlib import Path

from hindsight.brain import docs as docs_mod
from hindsight.core.db import add_edge, add_span, upsert_node
from hindsight.core.schemas import DocInfo, RunbookSection, UploadReport
from hindsight.core.text import looks_like_instructions, snippet

ALLOWED = {".md": "markdown", ".markdown": "markdown", ".pdf": "pdf"}
DOC_TYPES = ("runbook", "design", "spec", "other")
MAX_BYTES = 20 * 1024 * 1024
UPLOAD_PREFIX = "upload:"


class UnsupportedDocument(ValueError):
    pass


def slugify(name: str) -> str:
    stem = Path(name).stem.lower()
    return re.sub(r"[^a-z0-9]+", "-", stem).strip("-") or "document"


def pdf_to_markdown(data: bytes) -> str:
    """PDF text with headings recovered from font size (lines clearly larger than body text)."""
    import pymupdf

    lines: list[tuple[float, str]] = []
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        for page in pdf:
            for block in page.get_text("dict")["blocks"]:
                if block.get("type") != 0:
                    continue
                for line in block["lines"]:
                    text = "".join(span["text"] for span in line["spans"]).strip()
                    if text:
                        lines.append((max(span["size"] for span in line["spans"]), text))
                lines.append((0.0, ""))  # paragraph break after each block
    sized = [size for size, text in lines if text for _ in range(len(text))]
    if not sized:
        return ""
    body = statistics.median(sized)
    out: list[str] = []
    for size, text in lines:
        if not text:
            out.append("")
        elif size >= body * 1.2 and len(text) < 120:
            out.extend(["", ("#" if size >= body * 1.6 else "##") + " " + text, ""])
        else:
            out.append(text)
    return "\n".join(out)


def mention_names(conn: sqlite3.Connection) -> dict[str, str]:
    """Names a doc can mention: service names and file paths that exist at HEAD."""
    names = {
        r[0]: f"service:{r[0]}"
        for r in conn.execute("SELECT title FROM nodes WHERE kind = 'Service'")
    }
    for (path,) in conn.execute(
        "SELECT title FROM nodes WHERE kind = 'File' AND json_extract(attrs, '$.at_head') = 1"
        " AND json_extract(attrs, '$.generated') = 0"
    ):
        if "/" in path and len(path) > 8:
            names[path] = f"file:{path}"
    return names


def link_chunk(
    conn: sqlite3.Connection,
    chunk_id: int,
    doc_id: int,
    body: str,
    section: str,
    names: dict[str, str],
    runbook: bool,
) -> tuple[int, int]:
    """MENTIONS edges for names in the chunk; RUNBOOK_FOR for services when it is a runbook."""
    mentions = runbooks = 0
    for key in docs_mod.mentions(body, names):
        target = conn.execute("SELECT id FROM nodes WHERE key = ?", (key,)).fetchone()
        if target is None or target[0] == doc_id:
            continue
        surface = key.split(":", 1)[1]
        add_edge(
            conn,
            chunk_id,
            target[0],
            "MENTIONS",
            "heuristic",
            {"surface": surface, "section": section},
        )
        mentions += 1
        if runbook and key.startswith("service:"):
            add_edge(
                conn,
                chunk_id,
                target[0],
                "RUNBOOK_FOR",
                "heuristic",
                {"rule": "doc_type=runbook and MENTIONS", "section": section},
            )
            runbooks += 1
    return mentions, runbooks


def ingest_upload(
    conn: sqlite3.Connection, uploads_dir: Path, filename: str, data: bytes, doc_type: str = "other"
) -> UploadReport:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED:
        raise UnsupportedDocument(
            f"unsupported file type {ext or '(none)'}; upload PDF or Markdown"
        )
    if len(data) > MAX_BYTES:
        raise UnsupportedDocument(f"file is larger than {MAX_BYTES // (1024 * 1024)} MB")
    doc_type = doc_type if doc_type in DOC_TYPES else "other"
    slug = slugify(filename)
    sha = hashlib.sha256(data).hexdigest()
    latest = conn.execute(
        "SELECT id, key, json_extract(attrs, '$.version') AS version, json_extract(attrs, '$.sha256') AS sha"
        " FROM nodes WHERE kind = 'Doc' AND json_extract(attrs, '$.slug') = ?"
        " ORDER BY json_extract(attrs, '$.version') DESC LIMIT 1",
        (slug,),
    ).fetchone()
    if latest and latest["sha"] == sha:
        return UploadReport(
            doc_key=latest["key"],
            filename=filename,
            version=int(latest["version"]),
            doc_type=doc_type,
            status="unchanged",
            chunks=0,
            mentions=0,
            runbook_links=0,
            flagged_sections=[],
            warnings=["identical to the latest version; nothing changed"],
        )
    version = int(latest["version"]) + 1 if latest else 1

    uploads_dir.mkdir(parents=True, exist_ok=True)
    blob = uploads_dir / f"{sha}{ext}"
    blob.write_bytes(data)
    if ALLOWED[ext] == "pdf":
        try:
            text = pdf_to_markdown(data)
        except Exception as exc:  # a damaged PDF is reported, not fatal
            text, error = "", str(exc)
        else:
            error = ""
    else:
        text, error = data.decode("utf-8", errors="replace"), ""

    now = int(time.time())
    key = f"{UPLOAD_PREFIX}{slug}@v{version}"
    warnings: list[str] = []
    needs_attention = not text.strip()
    if needs_attention:
        warnings.append(
            "no text could be extracted (a scanned PDF? OCR is not enabled)"
            + (f": {error}" if error else "")
        )
    doc_id = upsert_node(
        conn,
        "Doc",
        key,
        title=f"{filename} v{version}",
        url=f"/api/docs/blob/{blob.name}",
        ts=now,
        attrs={
            "slug": slug,
            "version": version,
            "doc_type": doc_type,
            "filename": filename,
            "sha256": sha,
            "source": "upload",
            "uploaded_at": now,
            "needs_attention": needs_attention,
        },
    )
    if latest:
        add_edge(
            conn,
            doc_id,
            latest["id"],
            "SUPERSEDES",
            "explicit",
            {"prev_version": version - 1, "next_version": version, "uploaded_at": now},
        )
        conn.execute(
            "UPDATE nodes SET attrs = json_set(attrs, '$.superseded', json('true'))"
            " WHERE id = ? OR key LIKE ?",
            (latest["id"], f"chunk:{latest['key']}#%"),
        )

    names = mention_names(conn)
    chunks = mentions = runbooks = 0
    flagged: list[str] = []
    for i, (section, body) in enumerate(docs_mod.chunk_markdown(text) if text.strip() else []):
        is_flagged = looks_like_instructions(body)
        cid = upsert_node(
            conn,
            "DocChunk",
            f"chunk:{key}#{i}",
            title=f"{filename} v{version} § {section}",
            url=f"/api/docs/blob/{blob.name}",
            ts=now,
            attrs={"section": section, "doc": key, "doc_type": doc_type, "source": "upload"},
        )
        add_edge(conn, cid, doc_id, "PART_OF", "explicit", {"section": section, "ordinal": i})
        add_span(
            conn,
            cid,
            "doc_chunk",
            f"{section}\n\n{body}",
            url=f"/api/docs/blob/{blob.name}",
            ts=now,
            flagged=is_flagged,
        )
        m, r = link_chunk(conn, cid, doc_id, body, section, names, runbook=doc_type == "runbook")
        mentions += m
        runbooks += r
        chunks += 1
        if is_flagged:
            flagged.append(section)
    if flagged:
        warnings.append(
            "instruction-like text found; those sections are shown as evidence only and never sent to a model"
        )
    return UploadReport(
        doc_key=key,
        filename=filename,
        version=version,
        doc_type=doc_type,
        status="needs_attention" if needs_attention else "created",
        chunks=chunks,
        mentions=mentions,
        runbook_links=runbooks,
        flagged_sections=flagged,
        warnings=warnings,
    )


def list_docs(conn: sqlite3.Connection) -> list[DocInfo]:
    rows = conn.execute(
        "SELECT d.id, d.key, d.title, d.url, d.ts, d.attrs,"
        " (SELECT COUNT(*) FROM edges e WHERE e.dst = d.id AND e.kind = 'PART_OF') AS chunks,"
        " (SELECT COUNT(*) FROM edges p JOIN edges m ON m.src = p.src AND m.kind = 'MENTIONS'"
        "   WHERE p.dst = d.id AND p.kind = 'PART_OF') AS mentions,"
        " (SELECT COUNT(*) FROM edges p JOIN edges m ON m.src = p.src AND m.kind = 'RUNBOOK_FOR'"
        "   WHERE p.dst = d.id AND p.kind = 'PART_OF') AS runbook_links,"
        " (SELECT COUNT(*) FROM edges p JOIN spans s ON s.node_id = p.src AND s.flagged = 1"
        "   WHERE p.dst = d.id AND p.kind = 'PART_OF') AS flagged"
        " FROM nodes d WHERE d.kind = 'Doc' ORDER BY json_extract(d.attrs, '$.source') = 'upload' DESC, d.title"
    ).fetchall()
    out = []
    for r in rows:
        attrs = json.loads(r["attrs"])
        out.append(
            DocInfo(
                key=r["key"],
                title=r["title"],
                url=r["url"],
                source=attrs.get("source", "repo"),
                doc_type=attrs.get("doc_type", "other"),
                version=attrs.get("version"),
                updated_ts=r["ts"],
                chunks=r["chunks"],
                mentions=r["mentions"],
                runbook_links=r["runbook_links"],
                flagged=r["flagged"],
                superseded=bool(attrs.get("superseded")),
                needs_attention=bool(attrs.get("needs_attention")),
            )
        )
    return out


def runbooks_for(conn: sqlite3.Connection, service: str) -> list[RunbookSection]:
    rows = conn.execute(
        "SELECT c.key, c.title, c.url, c.ts, c.attrs, s.text FROM edges e JOIN nodes c ON c.id = e.src"
        " JOIN nodes svc ON svc.id = e.dst JOIN spans s ON s.node_id = c.id"
        " WHERE e.kind = 'RUNBOOK_FOR' AND svc.key = ?"
        " AND COALESCE(json_extract(c.attrs, '$.superseded'), 0) = 0 ORDER BY c.ts DESC",
        (f"service:{service}",),
    ).fetchall()
    return [
        RunbookSection(
            chunk_key=r["key"],
            title=r["title"],
            section=json.loads(r["attrs"]).get("section", ""),
            url=r["url"],
            snippet=snippet(r["text"], [service], width=400),
            updated_ts=r["ts"],
        )
        for r in rows
    ]
