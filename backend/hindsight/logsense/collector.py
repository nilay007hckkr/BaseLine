"""Live log collector (DESIGN §6.1): one `kubectl logs -f --timestamps` stream per deployment →
normalised events → data/logs/<date>.jsonl (the replay source) and the LogSense engine.

Events from different services arrive slightly out of order, so they are held for LAG seconds and
released in time order; windows keep closing on the clock even when a service goes silent."""

from __future__ import annotations

import heapq
import json
import os
import queue
import sqlite3
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from hindsight.core.db import set_meta
from hindsight.logsense.engine import Engine
from hindsight.logsense.events import LogEvent, is_stream_noise, normalise

LAG = 3.0


def kubeconfig() -> str:
    """kind writes ~/.kube/config; some kubectl builds (e.g. k3s) default to another file."""
    return os.environ.get("KUBECONFIG") or str(Path.home() / ".kube" / "config")


def kubectl(context: str, *args: str) -> list[str]:
    return ["kubectl", "--kubeconfig", kubeconfig(), "--context", context, *args]


def deployments(context: str, namespace: str = "default") -> list[str]:
    out = subprocess.run(
        kubectl(
            context,
            "-n",
            namespace,
            "get",
            "deployments",
            "-o",
            "jsonpath={.items[*].metadata.name}",
        ),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return sorted(out.split())


def service_addresses(context: str, namespace: str = "default") -> dict[str, list[str]]:
    """Service name → cluster IPs, so an error such as "dial tcp 10.96.233.126:50051: connection
    refused" can be traced to the service behind that address."""
    out = subprocess.run(
        kubectl(context, "-n", namespace, "get", "services", "-o", "json"),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    addresses: dict[str, list[str]] = {}
    for item in json.loads(out).get("items", []):
        ips = [
            ip
            for ip in item.get("spec", {}).get("clusterIPs")
            or [item.get("spec", {}).get("clusterIP")]
            if ip and ip != "None"
        ]
        if ips:
            addresses[item["metadata"]["name"]] = ips
    return addresses


def remember_addresses(conn: sqlite3.Connection, context: str) -> None:
    """Save the current cluster IPs for service_aliases(); keep the last known ones on failure."""
    try:
        set_meta(conn, "service_addresses", service_addresses(context))
        conn.commit()
    except (subprocess.CalledProcessError, OSError, json.JSONDecodeError):
        pass


class Collector:
    def __init__(
        self,
        engine: Engine,
        context: str,
        logs_dir: Path,
        namespace: str = "default",
        skip: tuple[str, ...] = ("loadgenerator",),
    ) -> None:
        self.engine, self.context, self.namespace = engine, context, namespace
        self.logs_dir = logs_dir
        self.skip = skip
        self.events: queue.Queue[LogEvent] = queue.Queue()
        self.stop = threading.Event()
        self._recent: dict[str, deque[tuple[float, str]]] = {}
        self.noise = 0  # lines from Kubernetes itself, not from the service (see is_stream_noise)

    def _stream(self, name: str) -> None:
        since = "10s"
        while not self.stop.is_set():
            proc = subprocess.Popen(
                kubectl(
                    self.context,
                    "-n",
                    self.namespace,
                    "logs",
                    "-f",
                    "--timestamps",
                    "--all-containers=true",
                    f"--since={since}",
                    f"deployment/{name}",
                ),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                if self.stop.is_set():
                    break
                if is_stream_noise(line):
                    self.noise += 1
                    continue
                event = normalise(line, name, pod=name, fallback_ts=time.time())
                if event is not None:
                    self.events.put(event)
            proc.terminate()
            since = "5s"  # the pod restarted or went away: reconnect, skipping what we already saw
            self.stop.wait(2.0)

    def _duplicate(self, event: LogEvent) -> bool:
        seen = self._recent.setdefault(event.service, deque(maxlen=2000))
        key = (event.ts, event.raw)
        if key in seen:
            return True
        seen.append(key)
        return False

    def run(
        self, seconds: float | None = None, on_tick: Callable[[float], None] | None = None
    ) -> None:
        names = [d for d in deployments(self.context, self.namespace) if d not in self.skip]
        threads = [threading.Thread(target=self._stream, args=(n,), daemon=True) for n in names]
        for t in threads:
            t.start()
        heap: list[tuple[float, int, LogEvent]] = []
        counter = 0
        deadline = time.time() + seconds if seconds else None
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        try:
            while not self.stop.is_set() and (deadline is None or time.time() < deadline):
                try:
                    while True:
                        e = self.events.get(timeout=0.5)
                        if not self._duplicate(e):
                            heapq.heappush(heap, (e.ts, counter, e))
                            counter += 1
                except queue.Empty:
                    pass
                watermark = time.time() - LAG
                released = []
                while heap and heap[0][0] <= watermark:
                    released.append(heapq.heappop(heap)[2])
                if released:
                    day = datetime.now(UTC).strftime("%Y-%m-%d")
                    with (self.logs_dir / f"{day}.jsonl").open("a") as fh:
                        for e in released:
                            fh.write(e.to_json() + "\n")
                            self.engine.add(e)
                self.engine.advance(watermark)
                if on_tick:
                    on_tick(watermark)
        finally:
            self.stop.set()


def read_jsonl(paths: list[Path]) -> list[LogEvent]:
    """Recorded events, re-read from their raw line so a replay uses the current normaliser."""
    events = []
    for path in paths:
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                stored = LogEvent.from_json(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if is_stream_noise(stored.raw):
                continue
            event = normalise(stored.raw, stored.service, stored.pod, fallback_ts=stored.ts)
            events.append(event or stored)
    return sorted(events, key=lambda e: e.ts)
