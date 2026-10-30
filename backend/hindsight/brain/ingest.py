"""History ingest: clone → git log → graph (commits, files, components, people, docs) →
read-only GitHub cache → linking → co-change → statistics. Every step is idempotent."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import subprocess
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from hindsight.brain import docs as docs_mod
from hindsight.brain.forge import GitHubCache, status_dict
from hindsight.brain.gitlog import (
    RawCommit,
    head_files,
    head_info,
    is_generated,
    lineage_map,
    read_log,
)
from hindsight.brain.static import component_of, discover_services, service_calls
from hindsight.core.config import Settings
from hindsight.core.db import add_edge, add_span, connect, node_id, set_meta, upsert_node
from hindsight.core.text import looks_like_instructions

_GITHUB = re.compile(r"github\.com[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")
_SUFFIX = re.compile(r"\(#(\d+)\)\s*$")
_CLOSING = re.compile(r"\b(close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)\b", re.IGNORECASE)
_HASHREF = re.compile(r"(?<![\w/&])#(\d+)\b")

Log = Callable[[str], None]


def parse_github(url: str) -> tuple[str, str]:
    m = _GITHUB.search(url.strip())
    if not m:
        raise ValueError(f"not a GitHub repository URL: {url}")
    return m.group(1), m.group(2)


def ensure_clone(url: str, dest: Path, log: Log) -> Path:
    if (dest / ".git").exists():
        log(f"using existing clone at {dest}")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"cloning {url} → {dest}")
    subprocess.run(["git", "clone", "--quiet", url, str(dest)], check=True)
    return dest


def _iso_ts(value: str | None) -> int | None:
    if not value:
        return None
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def author_key(name: str) -> str:
    """People are keyed by normalised name (after .mailmap), so two addresses of one person merge.
    Email addresses are not stored."""
    normalised = " ".join(name.lower().split())
    return "author:" + hashlib.sha1(normalised.encode()).hexdigest()[:12]


def _gh_node(
    conn: sqlite3.Connection, web: str, number: int, kind: str = "", title: str = ""
) -> int:
    key = f"gh:{number}"
    existing = conn.execute("SELECT kind FROM nodes WHERE key = ?", (key,)).fetchone()
    if existing and not kind:
        return int(conn.execute("SELECT id FROM nodes WHERE key = ?", (key,)).fetchone()[0])
    kind = kind or "Issue"
    path = "pull" if kind == "PR" else "issues"
    return upsert_node(conn, kind, key, title=title, url=f"{web}/{path}/{number}")


def _text_links(conn: sqlite3.Connection, web: str, src: int, text: str, skip: set[int]) -> None:
    """Closing keywords → FIXES (explicit); other #N mentions → REFERENCES (heuristic)."""
    closed: set[int] = set()
    for m in _CLOSING.finditer(text):
        number = int(m.group(2))
        if number in skip:
            continue
        closed.add(number)
        add_edge(
            conn,
            src,
            _gh_node(conn, web, number),
            "FIXES",
            "explicit",
            {"keyword": m.group(1), "text": m.group(0)},
        )
    for m in _HASHREF.finditer(text):
        number = int(m.group(1))
        if number in skip or number in closed:
            continue
        start = max(0, m.start() - 40)
        add_edge(
            conn,
            src,
            _gh_node(conn, web, number),
            "REFERENCES",
            "heuristic",
            {"text": text[start : m.end() + 20].strip()},
        )


def ingest_git(
    conn: sqlite3.Connection,
    commits: list[RawCommit],
    lineage: dict[str, str],
    head: set[str],
    services: set[str],
    web: str,
) -> None:
    for c in commits:
        files = [f for f in c.files]
        cid = upsert_node(
            conn,
            "Commit",
            f"commit:{c.sha}",
            title=c.subject,
            url=f"{web}/commit/{c.sha}",
            ts=c.ts,
            attrs={
                "sha": c.sha,
                "author": c.author,
                "is_bot": c.is_bot,
                "is_sweep": c.is_sweep,
                "is_merge": c.is_merge,
                "n_files": len(files),
            },
        )
        akey = author_key(c.author)
        conn.execute(
            "INSERT OR REPLACE INTO commits(id, sha, ts, author_key, n_files, is_bot, is_sweep, is_merge, ok)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cid,
                c.sha,
                c.ts,
                akey,
                len(files),
                int(c.is_bot),
                int(c.is_sweep),
                int(c.is_merge),
                int(c.ok),
            ),
        )
        aid = upsert_node(conn, "Author", akey, title=c.author, attrs={"is_bot": c.is_bot})
        add_edge(
            conn,
            cid,
            aid,
            "AUTHORED_BY",
            "explicit",
            {"name": c.author, "is_bot": c.is_bot},
            ts=c.ts,
        )
        message = f"{c.subject}\n\n{c.body}".strip()
        add_span(
            conn,
            cid,
            "commit_msg",
            message,
            url=f"{web}/commit/{c.sha}",
            ts=c.ts,
            flagged=looks_like_instructions(message),
        )
        for f in files:
            lin = lineage.get(f.path, f.path)
            gen = is_generated(lin)
            fid = upsert_node(
                conn,
                "File",
                f"file:{lin}",
                title=lin,
                url=f"{web}/blob/HEAD/{lin}" if lin in head else "",
                attrs={"at_head": lin in head, "generated": gen},
            )
            evidence: dict[str, Any] = {
                "additions": f.additions,
                "deletions": f.deletions,
                "change_type": "rename" if f.old_path else "modify",
            }
            if f.old_path:
                evidence["old_path"] = f.old_path
            add_edge(conn, cid, fid, "MODIFIES", "explicit", evidence, ts=c.ts)
            conn.execute(
                "INSERT OR REPLACE INTO changes(commit_id, path, lineage, old_path, additions, deletions, generated)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (cid, f.path, lin, f.old_path, f.additions, f.deletions, int(gen)),
            )
        m = _SUFFIX.search(c.subject)
        suffix = {int(m.group(1))} if m else set()
        if m:
            pr = _gh_node(
                conn, web, int(m.group(1)), kind="PR", title=_SUFFIX.sub("", c.subject).strip()
            )
            add_edge(
                conn,
                cid,
                pr,
                "PART_OF",
                "heuristic",
                {"via": "title_suffix", "text": m.group(0)},
                ts=c.ts,
            )
        _text_links(conn, web, cid, message, skip=suffix)

    # files → components
    for row in conn.execute("SELECT id, title FROM nodes WHERE kind = 'File'").fetchall():
        kind, name = component_of(row["title"], services)
        sid = upsert_node(conn, kind, f"{kind.lower()}:{name}", title=name)
        pattern = (
            f"src/{name}/"
            if kind == "Service"
            else (f"{name}/" if name != "(root)" else "(top level)")
        )
        add_edge(
            conn,
            row["id"],
            sid,
            "BELONGS_TO",
            "heuristic",
            {"rule": "path_prefix", "pattern": pattern},
        )


def ingest_docs(
    conn: sqlite3.Connection, repo: Path, head: set[str], services: set[str], web: str
) -> int:
    from hindsight.brain.uploads import link_chunk, mention_names

    names = mention_names(conn)
    n_chunks = 0
    for path in sorted(p for p in head if docs_mod.is_doc(p)):
        runbook = bool(re.search(r"runbook|playbook", path, re.IGNORECASE))
        text = (repo / path).read_text(errors="replace")
        last = conn.execute(
            "SELECT c.sha, c.ts FROM changes ch JOIN commits c ON c.id = ch.commit_id"
            " WHERE ch.lineage = ? ORDER BY c.ts DESC LIMIT 1",
            (path,),
        ).fetchone()
        did = upsert_node(
            conn,
            "Doc",
            f"doc:{path}",
            title=path,
            url=f"{web}/blob/HEAD/{path}",
            ts=last["ts"] if last else None,
            attrs={
                "last_commit": last["sha"] if last else None,
                "source": "repo",
                "doc_type": "runbook" if runbook else "other",
            },
        )
        for i, (section, body) in enumerate(docs_mod.chunk_markdown(text)):
            ckey = f"chunk:{path}#{i}"
            cid = upsert_node(
                conn,
                "DocChunk",
                ckey,
                title=f"{path} § {section}",
                url=f"{web}/blob/HEAD/{path}",
                ts=last["ts"] if last else None,
                attrs={"section": section, "doc": path},
            )
            add_edge(conn, cid, did, "PART_OF", "explicit", {"section": section, "ordinal": i})
            add_span(
                conn,
                cid,
                "doc_chunk",
                f"{section}\n\n{body}",
                url=f"{web}/blob/HEAD/{path}",
                ts=last["ts"] if last else None,
                flagged=looks_like_instructions(body),
            )
            link_chunk(conn, cid, did, body, section, names, runbook=runbook)
            n_chunks += 1
    return n_chunks


def ingest_static(conn: sqlite3.Connection, repo: Path, head: set[str], services: set[str]) -> int:
    calls = service_calls(repo, head, services)
    for caller, callee, evidence in calls:
        a = upsert_node(conn, "Service", f"service:{caller}", title=caller)
        b = upsert_node(conn, "Service", f"service:{callee}", title=callee)
        add_edge(conn, a, b, "CALLS", "static", evidence)
    return len(calls)


def ingest_forge(conn: sqlite3.Connection, cache: GitHubCache, web: str) -> None:
    for it in cache.items("issues"):
        number = int(it["number"])
        is_pr = "pull_request" in it
        nid = upsert_node(
            conn,
            "PR" if is_pr else "Issue",
            f"gh:{number}",
            title=it.get("title") or "",
            url=it.get("html_url") or "",
            ts=_iso_ts(it.get("created_at")),
            attrs={
                "state": it.get("state"),
                "user": (it.get("user") or {}).get("login"),
                "labels": [lb.get("name") for lb in it.get("labels") or []],
            },
        )
        text = f"{it.get('title') or ''}\n\n{it.get('body') or ''}".strip()
        add_span(
            conn,
            nid,
            "pr_body" if is_pr else "issue_body",
            text,
            url=it.get("html_url") or "",
            ts=_iso_ts(it.get("created_at")),
            flagged=looks_like_instructions(text),
        )
        _text_links(conn, web, nid, it.get("body") or "", skip={number})
    for pr in cache.items("pulls"):
        sha = pr.get("merge_commit_sha")
        if not sha or not pr.get("merged_at"):
            continue
        commit = node_id(conn, f"commit:{sha}")
        if commit is None:
            continue
        pid = _gh_node(conn, web, int(pr["number"]), kind="PR", title=pr.get("title") or "")
        add_edge(
            conn,
            commit,
            pid,
            "PART_OF",
            "api",
            {"via": "merge_commit_sha", "sha": sha[:12]},
            ts=_iso_ts(pr.get("merged_at")),
        )
    for cm in cache.items("issue_comments"):
        number = int(str(cm.get("issue_url", "")).rstrip("/").rsplit("/", 1)[-1] or 0)
        if not number:
            continue
        login = (cm.get("user") or {}).get("login") or "unknown"
        cid = upsert_node(
            conn,
            "Comment",
            f"comment:{cm['id']}",
            title=f"{login} on #{number}",
            url=cm.get("html_url") or "",
            ts=_iso_ts(cm.get("created_at")),
            attrs={"user": login, "is_bot": (cm.get("user") or {}).get("type") == "Bot"},
        )
        add_span(
            conn,
            cid,
            "comment",
            cm.get("body") or "",
            url=cm.get("html_url") or "",
            ts=_iso_ts(cm.get("created_at")),
            flagged=looks_like_instructions(cm.get("body") or ""),
        )
        add_edge(
            conn, cid, _gh_node(conn, web, number), "ON", "api", {"url": cm.get("html_url") or ""}
        )
    for rc in cache.items("review_comments"):
        number = int(str(rc.get("pull_request_url", "")).rstrip("/").rsplit("/", 1)[-1] or 0)
        if not number:
            continue
        login = (rc.get("user") or {}).get("login") or "unknown"
        path = rc.get("path") or ""
        rid = upsert_node(
            conn,
            "Review",
            f"review:{rc['id']}",
            title=f"{login} on #{number} {path}",
            url=rc.get("html_url") or "",
            ts=_iso_ts(rc.get("created_at")),
            attrs={"user": login, "path": path},
        )
        add_span(
            conn,
            rid,
            "review_comment",
            f"{path}\n\n{rc.get('body') or ''}",
            url=rc.get("html_url") or "",
            ts=_iso_ts(rc.get("created_at")),
            flagged=looks_like_instructions(rc.get("body") or ""),
        )
        add_edge(
            conn,
            rid,
            _gh_node(conn, web, number, kind="PR"),
            "ON",
            "api",
            {"url": rc.get("html_url") or ""},
        )
        fid = node_id(conn, f"file:{path}")
        if fid is not None:
            add_edge(
                conn,
                rid,
                fid,
                "ABOUT",
                "api",
                {"path": path, "line": rc.get("line") or rc.get("original_line")},
            )


def build_cochange(conn: sqlite3.Connection, top_k: int = 5) -> int:
    """File ↔ file co-change edges over filtered commits (DESIGN §2.1), top_k partners per file."""
    conn.execute("DELETE FROM edges WHERE kind = 'CO_CHANGES_WITH'")
    counts = dict(
        conn.execute(
            "SELECT ch.lineage, COUNT(DISTINCT ch.commit_id) FROM changes ch"
            " JOIN commits c ON c.id = ch.commit_id AND c.ok = 1 WHERE ch.generated = 0 GROUP BY ch.lineage"
        ).fetchall()
    )
    pairs = conn.execute(
        "SELECT a.lineage AS a, b.lineage AS b, COUNT(*) AS n, MAX(c.ts) AS last_ts FROM changes a"
        " JOIN changes b ON b.commit_id = a.commit_id AND b.lineage <> a.lineage AND b.generated = 0"
        " JOIN commits c ON c.id = a.commit_id AND c.ok = 1"
        " WHERE a.generated = 0 GROUP BY a.lineage, b.lineage HAVING n >= 2"
    ).fetchall()
    best: dict[str, list[tuple[float, str, int, int]]] = {}
    for row in pairs:
        conf = row["n"] / counts[row["a"]]
        best.setdefault(row["a"], []).append((conf, row["b"], row["n"], row["last_ts"]))
    made = 0
    for a, partners in best.items():
        src = node_id(conn, f"file:{a}")
        for conf, b, n, last_ts in sorted(partners, reverse=True)[:top_k]:
            dst = node_id(conn, f"file:{b}")
            if src is None or dst is None:
                continue
            last = datetime.fromtimestamp(last_ts).date().isoformat()
            add_edge(
                conn,
                src,
                dst,
                "CO_CHANGES_WITH",
                "statistical",
                {"support": n, "confidence": round(conf, 3), "of": counts[a], "last_change": last},
                weight=conf,
                ts=last_ts,
            )
            made += 1
    return made


def compute_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    c = conn.execute(
        "SELECT COUNT(*) total, SUM(ok) ok, SUM(is_bot) bot, SUM(is_sweep) sweep, SUM(is_merge) merge,"
        " MIN(ts) first_ts, MAX(ts) last_ts FROM commits"
    ).fetchone()
    kinds = dict(conn.execute("SELECT kind, COUNT(*) FROM nodes GROUP BY kind").fetchall())
    edges = [
        dict(r)
        for r in conn.execute(
            "SELECT kind, provenance, COUNT(*) AS n FROM edges GROUP BY kind, provenance ORDER BY kind, provenance"
        ).fetchall()
    ]
    humans = conn.execute(
        "SELECT COUNT(DISTINCT author_key) FROM commits WHERE is_bot = 0"
    ).fetchone()[0]
    flagged = conn.execute("SELECT COUNT(*) FROM spans WHERE flagged = 1").fetchone()[0]
    total = c["total"] or 0
    return {
        "commits": total,
        "commits_used_for_statistics": c["ok"] or 0,
        "bot_commits": c["bot"] or 0,
        "bot_share": round((c["bot"] or 0) / total, 4) if total else 0.0,
        "sweep_commits": c["sweep"] or 0,
        "merge_commits": c["merge"] or 0,
        "first_commit_ts": c["first_ts"],
        "last_commit_ts": c["last_ts"],
        "human_authors": humans,
        "nodes_by_kind": kinds,
        "edges_by_kind_and_provenance": edges,
        "spans": conn.execute("SELECT COUNT(*) FROM spans").fetchone()[0],
        "flagged_spans": flagged,
    }


def ingest(
    settings: Settings, repo_url: str | None = None, fetch_forge: bool = True, log: Log = print
) -> dict[str, Any]:
    url = repo_url or settings.repo_url
    owner, name = parse_github(url)
    web = f"https://github.com/{owner}/{name}"
    repo = ensure_clone(url, settings.repos_dir / f"{owner}__{name}", log)
    started = time.time()

    log("reading git history")
    commits = read_log(repo)
    lineage = lineage_map(commits)
    head = head_files(repo)
    services = discover_services(head)
    head_sha, head_ts = head_info(repo)

    conn = connect(settings.db_path)
    try:
        with conn:
            ingest_git(conn, commits, lineage, head, services, web)
        log(f"  {len(commits)} commits, {len(services)} services")
        with conn:
            n_calls = ingest_static(conn, repo, head, services)
        log(f"  {n_calls} static service calls")
        with conn:
            n_chunks = ingest_docs(conn, repo, head, services, web)
        log(f"  {n_chunks} doc chunks")

        cache = GitHubCache(
            owner,
            name,
            settings.forge_dir,
            token=settings.github_token,
            max_requests=settings.forge_max_requests,
        )
        if fetch_forge:
            log("reading public GitHub issues, PRs and comments (read-only, cached)")
            status = cache.fetch()
        else:
            status = cache.status()
        log(
            f"  GitHub cache: pages {status.pages}, complete {status.complete}"
            + (f", stopped: {status.stopped}" if status.stopped else "")
        )
        with conn:
            ingest_forge(conn, cache, web)
        with conn:
            n_co = build_cochange(conn)
        log(f"  {n_co} co-change edges")

        stats = compute_stats(conn)
        with conn:
            set_meta(
                conn,
                "repo",
                {
                    "url": url,
                    "web": web,
                    "owner": owner,
                    "name": name,
                    "path": str(repo),
                    "head_sha": head_sha,
                    "head_ts": head_ts,
                },
            )
            set_meta(conn, "forge", status_dict(status))
            set_meta(conn, "stats", stats)
            set_meta(conn, "ingested_at", int(time.time()))
        log(f"done in {time.time() - started:.1f}s")
        return stats
    finally:
        conn.close()
