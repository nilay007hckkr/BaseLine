"""Code structure layer for the Map: functions, methods and classes, and the calls between them.

graphify, an external command-line tool, parses the repository with tree-sitter. It runs offline:
code files only, no model backend. Only what it read directly from the source is imported:
- each symbol's own file and line, as File -DEFINES-> Symbol;
- call sites it marked EXTRACTED, as Symbol -CODE_CALLS-> Symbol.

Inferred links, type references, imports, comment text, configuration keys and generated files
are left out. Every edge is `static` and names the file and line it came from. The layer
describes HEAD only; impact scoring and the evaluation never read it."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from hindsight.brain.gitlog import head_info, is_generated
from hindsight.core.db import add_edge, get_meta, set_meta, upsert_node

TOOL = "graphify"
PINNED = "0.9.99"  # the version this importer was checked against (TRD §0.4)
SYMBOL = "Symbol"
DEFINES = "DEFINES"
CODE_CALLS = "CODE_CALLS"  # not "CALLS": impact reads every CALLS edge as a service-to-service call
CODE_KINDS = frozenset({DEFINES, CODE_CALLS})
MAX_SITES = 3
MAX_LABEL = 120
TIMEOUT = 600


class CodeGraphError(RuntimeError):
    """The code layer could not be built. The message says what to do."""


@dataclass
class CodeLayerStats:
    tool: str
    head: str
    symbols: int
    files: int
    calls: int
    skipped_inferred: int  # call links graphify derived rather than read
    skipped_generated: int  # symbols in generated files (protobuf stubs and the like)
    skipped_untracked: int  # symbols in files the graph does not know at HEAD


def run_graphify(repo: Path, out_dir: Path) -> tuple[Path, str]:
    """Run graphify offline on repo and return (graph.json path, tool version)."""
    exe = shutil.which(TOOL)
    if exe is None:
        raise CodeGraphError(f"graphify is not installed: uv tool install graphifyy=={PINNED}")
    quiet = {"capture_output": True, "text": True, "stdin": subprocess.DEVNULL}
    version = subprocess.run([exe, "--version"], timeout=30, **quiet).stdout.strip() or TOOL
    env = {**os.environ, "GRAPHIFY_BACKEND": "none"}  # no model calls, whatever keys are set
    cmd = [exe, "extract", str(repo), "--code-only", "--no-cluster", "--out", str(out_dir)]
    try:
        subprocess.run(cmd, env=env, timeout=TIMEOUT, check=True, **quiet)
    except subprocess.CalledProcessError as exc:
        tail = (exc.stderr or exc.stdout or "").strip()[-400:]
        raise CodeGraphError(f"graphify failed: {tail or exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CodeGraphError(f"graphify took longer than {TIMEOUT} s") from exc
    path = out_dir / "graphify-out" / "graph.json"
    if not path.exists():
        raise CodeGraphError(f"graphify wrote no graph at {path}")
    return path, version


def _line(location: object) -> int | None:
    if isinstance(location, str) and location.startswith("L") and location[1:].isdigit():
        return int(location[1:])
    return None


def _site(edge: dict[str, object]) -> str:
    line = _line(edge.get("source_location"))
    path = edge.get("source_file")
    return f"{path}:{line}" if isinstance(path, str) and path and line else str(path or "?")


def import_code_layer(
    conn: sqlite3.Connection, data: object, tool: str, head: str, web: str = ""
) -> CodeLayerStats:
    """Replace the code layer with the symbols and calls in a graphify graph.json payload."""
    nodes = data.get("nodes") if isinstance(data, dict) else None
    edges = data.get("edges") if isinstance(data, dict) else None
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise CodeGraphError("not a graphify graph.json: expected 'nodes' and 'edges' lists")
    files = {
        str(r[1]): int(r[0])
        for r in conn.execute(
            "SELECT id, title FROM nodes WHERE kind = 'File' AND json_extract(attrs, '$.at_head') = 1"
        )
    }
    skipped: Counter[str] = Counter()
    kept: dict[str, tuple[int, str]] = {}  # graphify id -> (node id, title)
    with conn:
        conn.execute(f"DELETE FROM edges WHERE kind IN ('{DEFINES}', '{CODE_CALLS}')")
        conn.execute(
            "DELETE FROM edges WHERE src IN (SELECT id FROM nodes WHERE kind = ?)"
            " OR dst IN (SELECT id FROM nodes WHERE kind = ?)",
            (SYMBOL, SYMBOL),
        )
        conn.execute("DELETE FROM nodes WHERE kind = ?", (SYMBOL,))
        for n in nodes:
            if not isinstance(n, dict) or not n.get("_callable") or n.get("file_type") != "code":
                continue  # configuration keys, comments, external modules
            gid, label, path = n.get("id"), n.get("label"), n.get("source_file")
            line = _line(n.get("source_location"))
            if not (isinstance(gid, str) and isinstance(label, str) and isinstance(path, str)):
                continue
            if not (gid and label.strip(".") and path and line):
                continue
            if is_generated(path):
                skipped["generated"] += 1
                continue
            if path not in files:
                skipped["untracked"] += 1
                continue
            title = label.lstrip(".")[:MAX_LABEL]
            sid = upsert_node(
                conn,
                SYMBOL,
                f"symbol:{path}#{title}@L{line}",
                title=title,
                url=f"{web}/blob/{head}/{path}#L{line}" if web else "",
                attrs={
                    "path": path,
                    "line": line,
                    "class": bool(n.get("_callable_class")),
                    "graphify_id": gid,
                },
            )
            add_edge(
                conn,
                files[path],
                sid,
                DEFINES,
                "static",
                {
                    "tool": tool,
                    "file": path,
                    "line": line,
                    "detail": f"{os.path.basename(path)} defines {title} at line {line}",
                },
            )
            kept[gid] = (sid, title)
        sites: dict[tuple[tuple[int, str], tuple[int, str]], list[str]] = {}
        for e in edges:
            if not isinstance(e, dict) or e.get("relation") != "calls":
                continue
            if e.get("confidence") != "EXTRACTED":
                skipped["inferred"] += 1
                continue
            a, b = kept.get(str(e.get("source"))), kept.get(str(e.get("target")))
            if a and b and a[0] != b[0]:
                sites.setdefault((a, b), []).append(_site(e))
        for (a, b), where in sites.items():
            add_edge(
                conn,
                a[0],
                b[0],
                CODE_CALLS,
                "static",
                {
                    "tool": tool,
                    "relation": "calls",
                    "sites": where[:MAX_SITES],
                    "count": len(where),
                    "detail": f"{a[1]} calls {b[1]} at {where[0]}",
                },
            )
        with_symbols = conn.execute(
            "SELECT COUNT(DISTINCT src) FROM edges WHERE kind = ?", (DEFINES,)
        ).fetchone()[0]
        stats = CodeLayerStats(
            tool=tool,
            head=head,
            symbols=len(kept),
            files=int(with_symbols),
            calls=len(sites),
            skipped_inferred=skipped["inferred"],
            skipped_generated=skipped["generated"],
            skipped_untracked=skipped["untracked"],
        )
        set_meta(conn, "codegraph", {**asdict(stats), "built_at": int(time.time())})
    return stats


def build_code_layer(
    conn: sqlite3.Connection, out_dir: Path, repo: Path | None, graph_path: Path | None = None
) -> CodeLayerStats:
    """Run graphify on the ingested clone (or read an existing graph.json) and import it."""
    meta = get_meta(conn, "repo") or {}
    if graph_path is None:
        if repo is None:
            raise CodeGraphError("no repository clone: run `make ingest` first")
        graph_path, tool = run_graphify(repo, out_dir)
    else:
        tool = f"{TOOL} (imported {graph_path.name})"
    try:
        data = json.loads(graph_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise CodeGraphError(f"cannot read {graph_path}: {exc}") from exc
    head = head_info(repo)[0] if repo else str(meta.get("head_sha") or "HEAD")
    return import_code_layer(conn, data, tool, head, str(meta.get("web") or ""))
