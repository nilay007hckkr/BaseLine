"""Command line: hindsight ingest | stats | ask | impact | risk."""

from __future__ import annotations

import argparse
import contextlib
import json
import sqlite3
import sys

from hindsight.core.config import Settings, load_settings
from hindsight.core.db import connect, get_meta
from hindsight.core.graphstore import graph_reader, neo4j_driver


def _print(obj: object) -> None:
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump()  # type: ignore[union-attr]
    print(json.dumps(obj, indent=2, default=str))


def _logsense(conn: sqlite3.Connection, settings: Settings, args: argparse.Namespace) -> None:
    from pathlib import Path

    from hindsight.logsense.alerts import send_alert
    from hindsight.logsense.collector import Collector, read_jsonl, remember_addresses
    from hindsight.logsense.engine import Engine, Incident, service_aliases
    from hindsight.logsense.incidents import get_incident

    def on_incident(incident: Incident, opened: bool) -> None:
        if not opened or incident.id is None:
            return
        full = get_incident(conn, incident.id)
        culprit = (
            next((r.service for r in full.ranking if r.role == "culprit"), "?") if full else "?"
        )
        print(f"incident #{incident.id} opened; likely culprit: {culprit}")
        if full and settings.alert_webhook:
            send_alert(settings.alert_webhook, full)

    context = f"kind-{settings.kind_cluster}"
    if args.cmd == "collect":
        remember_addresses(conn, context)
    aliases = service_aliases(conn)
    if args.cmd == "collect":
        engine = Engine(conn, aliases, warmup=args.warmup, on_incident=on_incident, source="live")
        print(f"collecting from {context}; Ctrl-C to stop")
        with contextlib.suppress(KeyboardInterrupt):
            Collector(engine, context, settings.data_dir / "logs").run(
                seconds=args.minutes * 60 if args.minutes else None
            )
        engine.flush()
    else:
        engine = Engine(conn, aliases, warmup=args.warmup, on_incident=on_incident, source="replay")
        events = read_jsonl([Path(f) for f in args.files])
        print(f"replaying {len(events)} events")
        for event in events:
            engine.add(event)
        engine.flush()
        print(f"{len(engine.closed)} incident(s)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hindsight", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_ingest = sub.add_parser("ingest", help="ingest a GitHub repository's history")
    p_ingest.add_argument("repo", nargs="?", help="repository URL (default: TARGET_REPO_URL)")
    p_ingest.add_argument(
        "--no-forge", action="store_true", help="do not call the GitHub API; use the cache only"
    )
    sub.add_parser("stats", help="print ingest statistics")
    p_ask = sub.add_parser("ask", help="ask why something is the way it is")
    p_ask.add_argument("question")
    p_ask.add_argument("--file")
    p_ask.add_argument("--lines", help="line range, e.g. 120-140")
    p_impact = sub.add_parser("impact", help="what a change affects")
    p_impact.add_argument("--files", nargs="*", default=[])
    p_impact.add_argument("--commit")
    sub.add_parser("risk", help="knowledge at risk")
    p_collect = sub.add_parser("collect", help="stream logs from the local cluster into LogSense")
    p_collect.add_argument("--minutes", type=float, help="stop after this many minutes")
    p_collect.add_argument(
        "--warmup", type=float, default=600, help="seconds before new patterns count"
    )
    p_replay = sub.add_parser(
        "replay", help="run recorded logs (data/logs/*.jsonl) through LogSense"
    )
    p_replay.add_argument("files", nargs="+")
    p_replay.add_argument("--warmup", type=float, default=600)
    p_fault = sub.add_parser(
        "fault", help="inject or revert a synthetic fault on the local cluster"
    )
    p_fault.add_argument("scenario")
    p_fault.add_argument("--revert", action="store_true")
    p_fault.add_argument(
        "--deploys", action="store_true", help="also record synthetic deploys first"
    )
    sub.add_parser("embed", help="index citable text with the local embedding model (Ollama)")
    p_sync = sub.add_parser("graph-sync", help="copy the graph into Neo4j (make graph-up first)")
    p_sync.add_argument(
        "--drop", metavar="NAMESPACE", help="remove a namespace (e.g. a test run) instead"
    )
    p_qa = sub.add_parser("qa", help="SentinelQA: tickets → browser tests → runs → repairs")
    p_qa.add_argument(
        "action", choices=["generate", "validate", "run", "approve", "reject", "revert"]
    )
    p_qa.add_argument("ids", nargs="*", help="test ids (run/validate) or one repair id")
    p_deploy = sub.add_parser("deploy", help="record that a commit was deployed now")
    p_deploy.add_argument("commit")
    p_deploy.add_argument("--note", default="")
    args = parser.parse_args(argv)

    settings = load_settings()
    if args.cmd == "ingest":
        from hindsight.brain.ingest import ingest

        stats = ingest(settings, args.repo, fetch_forge=not args.no_forge)
        _print(stats)
        from hindsight.api.service import resync_graph

        synced = resync_graph(settings)  # only when this repository was synced before
        if synced:
            print(f"graph re-synced to Neo4j: {synced['nodes']} nodes, {synced['edges']} edges")
        return 0

    conn = connect(settings.db_path)
    try:
        if args.cmd == "stats":
            _print(
                {
                    "repo": get_meta(conn, "repo"),
                    "stats": get_meta(conn, "stats"),
                    "github": get_meta(conn, "forge"),
                }
            )
        elif args.cmd == "ask":
            from hindsight.api.service import ask_question

            start, end = (int(x) for x in args.lines.split("-")) if args.lines else (None, None)
            graph = graph_reader(conn, settings)
            _print(ask_question(conn, settings, args.question, args.file, start, end, graph))
        elif args.cmd == "impact":
            from hindsight.brain.impact import impact_report

            graph = graph_reader(conn, settings)
            _print(impact_report(conn, files=args.files, commit=args.commit, graph=graph))
        elif args.cmd == "embed":
            from hindsight.core.embed import OllamaEmbedder, index_spans

            embedder = OllamaEmbedder(settings.ollama_host, settings.embed_model)
            if not embedder.available():
                print(
                    f"Ollama is not running or {settings.embed_model} is not pulled"
                    f" (ollama pull {settings.embed_model})"
                )
                return 1
            _print(index_spans(conn, embedder))
        elif args.cmd == "graph-sync":
            from hindsight.core.graphsync import drop_namespace, sync

            driver = neo4j_driver(settings)
            if driver is None:
                print("Neo4j is not reachable (or no password): run `make graph-up` first")
                return 1
            if args.drop:
                drop_namespace(driver, args.drop)
                print(f"removed namespace {args.drop}")
            else:
                _print(sync(conn, driver))
        elif args.cmd == "risk":
            from hindsight.brain.risk import knowledge_at_risk

            _print(knowledge_at_risk(conn))
        elif args.cmd in ("collect", "replay"):
            _logsense(conn, settings, args)
        elif args.cmd == "fault":
            from hindsight.core.config import ROOT
            from hindsight.logsense.faults import SCENARIOS, inject, kubectl_runner, revert

            if args.scenario not in SCENARIOS:
                print(f"unknown scenario; choose one of: {', '.join(SCENARIOS)}")
                return 2
            run = kubectl_runner(f"kind-{settings.kind_cluster}")
            faults_dir = ROOT / "eval" / "faults"
            if args.revert:
                _print(revert(args.scenario, run, faults_dir))
            else:
                _print(inject(args.scenario, run, faults_dir, conn=conn, deploys=args.deploys))
        elif args.cmd == "qa":
            from hindsight.sentinel import service as qa
            from hindsight.sentinel.store import decide_repair

            if args.action == "generate":
                with conn:
                    _print(qa.generate_all(conn))
            elif args.action == "validate":
                _print(qa.validate_tests(conn, settings, args.ids or None))
            elif args.action == "run":
                _print(qa.run_tests(conn, settings, args.ids or None))
            else:
                with conn:
                    _print(decide_repair(conn, int(args.ids[0]), args.action))
        elif args.cmd == "deploy":
            from hindsight.correlate.deploys import record_deploy

            record = record_deploy(conn, args.commit, note=args.note)
            if record is None:
                print(f"commit {args.commit} not found")
                return 1
            _print(record)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
