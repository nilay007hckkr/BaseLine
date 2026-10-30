"""The LogSense engine: templates (Drain3) → 10 s windows → detectors → incidents → culprit ranking.

Events must arrive in time order (per run). The same engine serves the live collector, replays of
recorded logs (`hindsight replay`) and the evaluation campaign, so all three behave identically.

Detectors (DESIGN §6.3), per service:
- new template: first seen after the warm-up, with severity ≥ warn or ≥ 5 occurrences in a window;
- spike: error count with robust z ≥ 4 (median/MAD of the trailing windows) for 2 windows in a row.
Anomalies within 2 minutes of each other belong to one incident; it closes after 2 quiet minutes.

Culprit vs victim (DESIGN §6.4): each anomalous service gets a base score (error-rate increase plus
novelty). When its error messages name another service, half of that mass moves to the named
service, which may have no logs at all (it is down). A service that is called by another anomalous
service (static call graph) gets a small bonus. Highest score = culprit; anomalous services whose
errors name the culprit are victims."""

from __future__ import annotations

import json
import re
import sqlite3
import statistics
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from drain3 import TemplateMiner
from drain3.masking import MaskingInstruction
from drain3.template_miner_config import TemplateMinerConfig

from hindsight.core.db import get_meta
from hindsight.logsense.events import LogEvent

WINDOW = 10
HISTORY_WINDOWS = 30
SPIKE_Z = 4.0
SPIKE_RUN = 2
MERGE_GAP = 120
NEW_TEMPLATE_MIN = 5
SAMPLES_PER_TEMPLATE = 3

_MASKS = [
    (r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", "UUID"),
    (r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b", "IP"),
    (r"[\w.+-]+@[\w-]+\.[\w.-]+", "EMAIL"),
    (r"\b0x[0-9a-fA-F]+\b|\b[0-9a-fA-F]{16,}\b", "HEX"),
    (r"(?<![\w.])[-+]?\d+(?:\.\d+)?(?![\w.])", "NUM"),
]


def _miner() -> TemplateMiner:
    config = TemplateMinerConfig()
    config.masking_instructions = [MaskingInstruction(pattern, name) for pattern, name in _MASKS]
    config.mask_prefix, config.mask_suffix = "<", ">"
    config.drain_sim_th = 0.5
    config.profiling_enabled = False
    return TemplateMiner(config=config)


@dataclass
class Template:
    id: str
    service: str
    text: str
    severity: str
    first_seen: float
    last_seen: float
    count: int = 0
    samples: deque[str] = field(default_factory=lambda: deque(maxlen=SAMPLES_PER_TEMPLATE))


@dataclass
class Anomaly:
    ts: float
    service: str
    kind: str  # new_template | spike
    detail: dict[str, Any]


@dataclass
class Incident:
    id: int | None
    opened_at: float
    updated_at: float
    anomalies: list[Anomaly] = field(default_factory=list)
    closed_at: float | None = None
    data: dict[str, Any] = field(default_factory=dict)


def _sev_rank(severity: str) -> int:
    return {"debug": 0, "info": 1, "warn": 2, "error": 3}.get(severity, 1)


class Engine:
    def __init__(
        self,
        conn: sqlite3.Connection,
        services: dict[str, set[str]] | None = None,
        warmup: float = 600.0,
        on_incident: Callable[[Incident, bool], None] | None = None,
        source: str = "live",
    ) -> None:
        """services: service name → names that refer to it in messages (e.g. 'PaymentService')."""
        self.conn = conn
        self.warmup = warmup
        self.on_incident = on_incident
        # one label per run, so pattern ids (T1, T2, ...) from different runs never collide
        self.source = f"{source}-{time.time_ns()}"
        self.services = services or {}
        self._aliases = {
            alias.lower(): name
            for name, aliases in self.services.items()
            for alias in {name, *aliases}
        }
        self._calls = self._load_calls()
        self.miners: dict[str, TemplateMiner] = {}
        self.templates: dict[str, Template] = {}
        self._cluster_ids: dict[tuple[str, int], str] = {}
        self._first_event: dict[str, float] = {}
        self._window: int | None = None
        self._counts: dict[tuple[str, str], int] = defaultdict(int)
        self._errors: dict[str, int] = defaultdict(int)
        self._error_history: dict[str, deque[int]] = defaultdict(
            lambda: deque(maxlen=HISTORY_WINDOWS)
        )
        self._spike_run: dict[str, int] = defaultdict(int)
        # error lines that name another service, logged per window as (window, {(service, named): n},
        # {service: error lines}) and kept for MERGE_GAP before the open incident
        self._named: dict[tuple[str, str], int] = defaultdict(int)
        self._error_lines: dict[str, int] = defaultdict(int)
        self._named_log: deque[tuple[int, dict[tuple[str, str], int], dict[str, int]]] = deque()
        self._new_templates: set[str] = set()
        self.incident: Incident | None = None
        self.closed: list[Incident] = []

    # ---------- setup ----------
    def _load_calls(self) -> set[tuple[str, str]]:
        rows = self.conn.execute(
            "SELECT a.title, b.title FROM edges e JOIN nodes a ON a.id = e.src JOIN nodes b ON b.id = e.dst"
            " WHERE e.kind = 'CALLS'"
        ).fetchall()
        return {(r[0], r[1]) for r in rows}

    def _names_in(self, message: str, own: str) -> set[str]:
        lowered = message.lower()
        found = set()
        for alias, name in self._aliases.items():
            # digits count as part of a word so that one address never matches inside another
            if name != own and re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", lowered):
                found.add(name)
        return found

    # ---------- ingestion ----------
    def add(self, event: LogEvent) -> None:
        window = int(event.ts // WINDOW) * WINDOW
        if self._window is None:
            self._window = window
        while window > self._window:
            self._close_window(self._window)
            self._window += WINDOW
        self._first_event.setdefault(event.service, event.ts)
        tpl = self._template(event)
        self._counts[(event.service, tpl.id)] += 1
        if _sev_rank(event.severity) >= 3:
            self._errors[event.service] += 1
            self._error_lines[event.service] += 1
            for named in self._names_in(event.message, event.service):
                self._named[(event.service, named)] += 1

    def advance(self, ts: float) -> None:
        """Close every window that ends at or before ts, even when no events arrived (a service
        that went quiet or down must still be noticed)."""
        if self._window is None:
            return
        while self._window + WINDOW <= ts:
            self._close_window(self._window)
            self._window += WINDOW

    def flush(self) -> None:
        if self._window is not None:
            self._close_window(self._window)
            self._window += WINDOW
        if self.incident is not None:
            self._close_incident(self.incident, (self._window or 0))

    def _template(self, event: LogEvent) -> Template:
        miner = self.miners.setdefault(event.service, _miner())
        result = miner.add_log_message(event.message)
        key = (event.service, int(result["cluster_id"]))
        tid = self._cluster_ids.get(key)
        if tid is None:
            tid = f"T{len(self._cluster_ids) + 1}"
            self._cluster_ids[key] = tid
            self.templates[tid] = Template(
                tid, event.service, result["template_mined"], event.severity, event.ts, event.ts
            )
            if event.ts - self._first_event[event.service] >= self.warmup:
                self._new_templates.add(tid)
        tpl = self.templates[tid]
        tpl.text = result["template_mined"]
        tpl.count += 1
        tpl.last_seen = event.ts
        if _sev_rank(event.severity) > _sev_rank(tpl.severity):
            tpl.severity = event.severity
        tpl.samples.append(event.message[:300])
        return tpl

    # ---------- detection ----------
    def _close_window(self, window: int) -> None:
        end = window + WINDOW
        anomalies: list[Anomaly] = []
        for tid in sorted(self._new_templates):
            tpl = self.templates[tid]
            count = self._counts.get((tpl.service, tid), 0)
            if count and (_sev_rank(tpl.severity) >= 2 or count >= NEW_TEMPLATE_MIN):
                anomalies.append(
                    Anomaly(
                        end,
                        tpl.service,
                        "new_template",
                        {"template": tid, "text": tpl.text, "count": count},
                    )
                )
                self._new_templates.discard(tid)
        services = {s for s, _ in self._counts} | set(self._errors) | set(self._error_history)
        for service in sorted(services):
            history = self._error_history[service]
            errors = self._errors.get(service, 0)
            if len(history) >= 6:
                med = statistics.median(history)
                mad = statistics.median(abs(x - med) for x in history)
                z = (errors - med) / max(1.4826 * mad, 1.0)
                self._spike_run[service] = self._spike_run[service] + 1 if z >= SPIKE_Z else 0
                if self._spike_run[service] == SPIKE_RUN:
                    anomalies.append(
                        Anomaly(
                            end,
                            service,
                            "spike",
                            {"errors": errors, "median": med, "z": round(z, 2)},
                        )
                    )
            history.append(errors)
        self._log_named(window)
        self._persist_counts(window)
        self.conn.commit()  # never hold a write transaction across windows (others read and write too)
        if anomalies:
            self._record(anomalies, end)
        elif self.incident is not None and end - self.incident.updated_at >= MERGE_GAP:
            self._close_incident(self.incident, end)
        self._counts.clear()
        self._errors.clear()

    def _log_named(self, window: int) -> None:
        self._named_log.append((window, dict(self._named), dict(self._error_lines)))
        self._named.clear()
        self._error_lines.clear()
        keep_from = window - MERGE_GAP
        if self.incident is not None:
            keep_from = min(keep_from, int(self.incident.opened_at) - MERGE_GAP)
        while self._named_log and self._named_log[0][0] < keep_from:
            self._named_log.popleft()

    def _naming(self, since: float) -> tuple[dict[tuple[str, str], int], dict[str, int]]:
        named: dict[tuple[str, str], int] = defaultdict(int)
        errors: dict[str, int] = defaultdict(int)
        for window, by_target, lines in self._named_log:
            if window >= since:
                for key, count in by_target.items():
                    named[key] += count
                for service, count in lines.items():
                    errors[service] += count
        return named, errors

    def _persist_counts(self, window: int) -> None:
        rows = [(window, s, tid, n) for (s, tid), n in self._counts.items()]
        touched = [self.templates[tid] for tid in {tid for _, tid in self._counts}]
        if touched:
            self.conn.executemany(
                "INSERT INTO templates(id, source, service, text, severity, count, first_seen, last_seen)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id, source) DO UPDATE SET"
                " text = excluded.text, severity = excluded.severity, count = excluded.count,"
                " last_seen = excluded.last_seen",
                [
                    (
                        t.id,
                        self.source,
                        t.service,
                        t.text,
                        t.severity,
                        t.count,
                        int(t.first_seen),
                        int(t.last_seen),
                    )
                    for t in touched
                ],
            )
        if rows:
            self.conn.executemany(
                "INSERT INTO template_counts(win, service, template_id, count, source) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(win, service, template_id, source) DO UPDATE SET count = count + excluded.count",
                [(*r, self.source) for r in rows],
            )

    # ---------- incidents ----------
    def _record(self, anomalies: list[Anomaly], now: float) -> None:
        opened = False
        if self.incident is None or now - self.incident.updated_at > MERGE_GAP:
            if self.incident is not None:
                self._close_incident(self.incident, now)
            self.incident = Incident(id=None, opened_at=now, updated_at=now)
            opened = True
        incident = self.incident
        incident.anomalies.extend(anomalies)
        incident.updated_at = now
        incident.data = self._describe(incident)
        self._save(incident)
        if self.on_incident:
            self.on_incident(incident, opened)

    def _close_incident(self, incident: Incident, now: float) -> None:
        incident.closed_at = now
        incident.data = self._describe(incident)
        self._save(incident)
        self.closed.append(incident)
        if self.incident is incident:
            self.incident = None

    def ranking(self, incident: Incident) -> list[dict[str, Any]]:
        anomalous: dict[str, dict[str, Any]] = {}
        for a in incident.anomalies:
            entry = anomalous.setdefault(a.service, {"spikes": 0, "new": [], "templates": set()})
            if a.kind == "spike":
                entry["spikes"] += 1
                entry["z"] = max(entry.get("z", 0.0), float(a.detail["z"]))
            else:
                entry["new"].append(a.detail["template"])
                entry["templates"].add(a.detail["template"])
        max_z = max((e.get("z", 0.0) for e in anomalous.values()), default=0.0) or 1.0
        score: dict[str, float] = defaultdict(float)
        base: dict[str, float] = {}
        for s, e in anomalous.items():
            base[s] = (e.get("z", 0.0) / max_z if e["spikes"] else 0.0) + 0.5 * min(
                1.0, len(e["new"]) / 2
            )
            score[s] += base[s]
        transfers: dict[str, list[tuple[str, int]]] = defaultdict(list)
        naming, error_lines = self._naming(incident.opened_at - MERGE_GAP)
        for s in anomalous:
            named = {t: n for (src, t), n in naming.items() if src == s and n > 0}
            # the share of s's error lines that blame t moves that share of s's score to t
            total = max(error_lines.get(s, 0), sum(named.values()))
            for t, n in named.items():
                moved = base[s] * n / total
                score[s] -= moved
                score[t] += moved
                transfers[t].append((s, n))
        for s in list(score):
            if any((caller, s) in self._calls for caller in anomalous if caller != s):
                score[s] += 0.1
        # ties: the service blamed by more others first, then by name, so runs are repeatable
        ranked = sorted(score.items(), key=lambda kv: (-kv[1], -len(transfers[kv[0]]), kv[0]))
        culprit = ranked[0][0] if ranked else None
        out = []
        for s, value in ranked:
            names_culprit = (
                culprit is not None
                and s != culprit
                and any(src == s for src, _ in transfers[culprit])
            )
            role = "culprit" if s == culprit else "victim" if names_culprit else "affected"
            templates = sorted(anomalous.get(s, {}).get("templates", set()))
            err_templates = [
                t.id
                for t in self.templates.values()
                if t.service == s and _sev_rank(t.severity) >= 3
            ]
            ids = (templates or err_templates)[:5]
            samples = [m for tid in ids for m in list(self.templates[tid].samples)[:1]][:3]
            out.append(
                {
                    "service": s,
                    "role": role,
                    "score": round(value, 3),
                    "evidence": {
                        "spike_z": anomalous.get(s, {}).get("z"),
                        "new_templates": anomalous.get(s, {}).get("new", []),
                        "named_by": [
                            {"service": src, "errors": n} for src, n in transfers.get(s, [])
                        ],
                        "in_call_graph_as_callee_of": sorted(
                            c for c in anomalous if (c, s) in self._calls
                        ),
                    },
                    "templates": ids,
                    "samples": samples,
                }
            )
        return out

    def _describe(self, incident: Incident) -> dict[str, Any]:
        ranking = self.ranking(incident)
        tids = {t for r in ranking for t in r["templates"]}
        return {
            "source": self.source,
            "window": [incident.opened_at - 60, incident.updated_at],
            "ranking": ranking,
            "anomalies": [
                {"ts": a.ts, "service": a.service, "kind": a.kind, "detail": a.detail}
                for a in incident.anomalies
            ],
            "templates": [
                {
                    "id": t,
                    "service": self.templates[t].service,
                    "text": self.templates[t].text,
                    "severity": self.templates[t].severity,
                    "count": self.templates[t].count,
                    "samples": list(self.templates[t].samples),
                }
                for t in sorted(tids)
            ],
        }

    def _save(self, incident: Incident) -> None:
        payload = json.dumps(incident.data)
        if incident.id is None:
            cur = self.conn.execute(
                "INSERT INTO incidents(opened_at, updated_at, closed_at, source, data, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    int(incident.opened_at),
                    int(incident.updated_at),
                    int(incident.closed_at) if incident.closed_at else None,
                    self.source,
                    payload,
                    int(time.time()),
                ),
            )
            incident.id = int(cur.lastrowid or 0)
        else:
            self.conn.execute(
                "UPDATE incidents SET updated_at = ?, closed_at = ?, data = ? WHERE id = ?",
                (
                    int(incident.updated_at),
                    int(incident.closed_at) if incident.closed_at else None,
                    payload,
                    incident.id,
                ),
            )
        self.conn.commit()


def service_aliases(conn: sqlite3.Connection) -> dict[str, set[str]]:
    """Service names plus the names that refer to them in messages: gRPC service names (from CALLS
    edge evidence) and cluster IPs (saved by the collector under meta 'service_addresses')."""
    aliases: dict[str, set[str]] = defaultdict(set)
    for (name,) in conn.execute("SELECT title FROM nodes WHERE kind = 'Service'"):
        aliases[name].add(name)
    for callee, evidence in conn.execute(
        "SELECT b.title, e.evidence FROM edges e JOIN nodes b ON b.id = e.dst WHERE e.kind = 'CALLS'"
    ):
        proto = json.loads(evidence).get("proto")
        if proto:
            aliases[callee].add(proto)
    for name, ips in (get_meta(conn, "service_addresses") or {}).items():
        if name in aliases:
            aliases[name].update(ips)
    return dict(aliases)
