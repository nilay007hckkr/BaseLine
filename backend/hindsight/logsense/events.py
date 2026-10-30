"""Normalised log events (DESIGN §6.1): {ts, service, pod, severity, message, raw}.

Lines come from `kubectl logs --timestamps`, so each starts with an RFC 3339 timestamp. The rest is
JSON when the service logs JSON (most Online Boutique services do), otherwise plain text."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime

SEVERITIES = ("debug", "info", "warn", "error")
_TS = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:?\d\d))\s+(.*)$")
_PLAIN_ERROR = re.compile(
    r"\b(error|exception|fatal|panic|failed|failure|unavailable)\b", re.IGNORECASE
)
_PLAIN_WARN = re.compile(r"\b(warn|warning|retry|timeout|deprecated)\b", re.IGNORECASE)
# Written into the log stream by the kubelet, not by the service, when the node runs out of file
# watches (raise fs.inotify.max_user_instances). Counting it would blame every service at once.
_STREAM_NOISE = re.compile(r"^failed to create fsnotify watcher: ")


def is_stream_noise(line: str) -> bool:
    return bool(_STREAM_NOISE.match(line.strip()))


@dataclass
class LogEvent:
    ts: float
    service: str
    pod: str
    severity: str
    message: str
    raw: str

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, line: str) -> LogEvent:
        return cls(**json.loads(line))


def parse_ts(value: str) -> float:
    value = value.strip().replace("Z", "+00:00")
    m = re.match(r"^(.*\.\d{6})\d*(.*)$", value)  # Python takes at most microseconds
    if m:
        value = m.group(1) + m.group(2)
    return datetime.fromisoformat(value).timestamp()


def _severity(value: object, message: str) -> str:
    if isinstance(value, int | float):  # pino-style numeric levels
        return (
            "error"
            if value >= 50
            else "warn"
            if value >= 40
            else "info"
            if value >= 30
            else "debug"
        )
    text = str(value or "").lower()
    if text.startswith(("err", "crit", "fatal", "alert", "emerg", "panic")):
        return "error"
    if text.startswith("warn"):
        return "warn"
    if text.startswith(("debug", "trace")):
        return "debug"
    if text.startswith(("info", "notice")):
        return "info"
    if _PLAIN_ERROR.search(message):
        return "error"
    if _PLAIN_WARN.search(message):
        return "warn"
    return "info"


def normalise(
    line: str, service: str, pod: str = "", fallback_ts: float | None = None
) -> LogEvent | None:
    line = line.rstrip("\n")
    if not line.strip():
        return None
    m = _TS.match(line)
    ts: float | None = None
    body = line
    if m:
        try:
            ts = parse_ts(m.group(1))
            body = m.group(2)
        except ValueError:
            ts = None
    message, level = body, None
    if body.lstrip().startswith("{"):
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            message = str(data.get("message") or data.get("msg") or data.get("error") or body)
            detail = data.get("error") or data.get("err")
            if isinstance(detail, str) and detail and detail not in message:
                # e.g. frontend: {"message": "request error", "error": "rpc error: ... dial tcp ..."}
                message = f"{message}: {detail}"
            level = data.get("severity") or data.get("level")
            if ts is None:
                stamp = data.get("timestamp") or data.get("time")
                if isinstance(stamp, str):
                    try:
                        ts = parse_ts(stamp)
                    except ValueError:
                        ts = None
    if ts is None:
        if fallback_ts is None:
            return None
        ts = fallback_ts
    return LogEvent(
        ts=ts,
        service=service,
        pod=pod,
        severity=_severity(level, message),
        message=message[:2000],
        raw=line[:4000],
    )
