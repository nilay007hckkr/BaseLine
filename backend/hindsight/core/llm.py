"""Optional LLM chain (Gemini → Groq → Ollama, in LLM_PROVIDER_ORDER) with a SQLite cache.

The model only phrases evidence that code has already selected; callers validate every citation.
Cache modes: off (always live), record (live, then store), replay (cache only, never live),
read-through (cache first, live on a miss). Text is scrubbed of keys, tokens and email addresses
before it leaves the process."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import time
from typing import Any, Protocol

import httpx

from hindsight.core.config import Settings

TEMPLATE_VERSION = "ask-v2"
log = logging.getLogger(__name__)
_SECRET = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}"
    r"|AIza[0-9A-Za-z_-]{30,}|[\w.+-]+@[\w-]+\.[\w.-]+"
)
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)
# A provider that is down must fail fast: a dropped connection otherwise waits out the whole
# timeout before the chain moves on. Generation itself keeps the longer read timeout.
CONNECT_TIMEOUT = 5.0


def scrub(text: str) -> str:
    return _SECRET.sub("[redacted]", text)


class Provider(Protocol):
    name: str
    model: str

    def complete(self, prompt: str) -> str: ...


class Gemini:
    name = "gemini"

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key, self.model = api_key, model

    def complete(self, prompt: str) -> str:
        resp = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
            headers={"x-goog-api-key": self.api_key},
            json={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
            },
            timeout=httpx.Timeout(60, connect=CONNECT_TIMEOUT),
        )
        resp.raise_for_status()
        return str(resp.json()["candidates"][0]["content"]["parts"][0]["text"])


class Groq:
    name = "groq"

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key, self.model = api_key, model

    def complete(self, prompt: str) -> str:
        resp = httpx.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=httpx.Timeout(60, connect=CONNECT_TIMEOUT),
        )
        resp.raise_for_status()
        return str(resp.json()["choices"][0]["message"]["content"])


class Ollama:
    name = "ollama"

    def __init__(self, host: str, model: str, timeout: float = 300.0, num_ctx: int = 8192) -> None:
        self.host, self.model = host.rstrip("/"), model
        # a local model on a laptop CPU is slow: allow it time, and a context window that holds the
        # whole prompt (Ollama's default window is smaller and silently cuts the prompt)
        self.timeout, self.num_ctx = timeout, num_ctx

    def complete(self, prompt: str) -> str:
        resp = httpx.post(
            f"{self.host}/api/chat",
            json={
                "model": self.model,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0, "num_ctx": self.num_ctx},
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=httpx.Timeout(self.timeout, connect=CONNECT_TIMEOUT),
        )
        resp.raise_for_status()
        return str(resp.json()["message"]["content"])


_LABEL = {"gemini": "Gemini", "groq": "Groq", "ollama": "Ollama"}


def why_failed(provider: Provider, exc: Exception) -> str:
    """One plain sentence on why a provider gave no answer, for the notes shown with an answer."""
    who = f"{_LABEL.get(provider.name, provider.name)} ({provider.model})"
    if isinstance(exc, httpx.TimeoutException) and not isinstance(exc, httpx.ConnectTimeout):
        seconds = getattr(provider, "timeout", 60)
        return f"{who} did not answer in time (timed out after {seconds:g} s)"
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        where = getattr(provider, "host", "")
        return f"{who} is not reachable" + (
            f" at {where} (is it running?)" if where else " (offline?)"
        )
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if provider.name == "ollama" and code == 404:
            return f"{who} is not available: pull it with `ollama pull {provider.model}`"
        if code in (401, 403):
            return f"{who} rejected the API key"
        if code == 429:
            return f"{who} is rate-limited"
        return f"{who} answered with HTTP {code}"
    return f"{who} failed ({type(exc).__name__})"


def _parse_json(text: str) -> dict[str, Any] | None:
    for candidate in (text, *(m.group(0) for m in [_JSON_OBJECT.search(text)] if m)):
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            return data
    return None


class Chain:
    def __init__(
        self, conn: sqlite3.Connection, providers: list[Provider], mode: str = "read-through"
    ) -> None:
        self.conn, self.providers, self.mode = conn, providers, mode
        self.last_provider: str | None = None
        # why each provider gave no answer on the last call (empty when one answered)
        self.failures: list[str] = []

    @classmethod
    def from_settings(cls, conn: sqlite3.Connection, settings: Settings) -> Chain:
        providers: list[Provider] = []
        for name in settings.llm_provider_order:
            if (
                name == "gemini"
                and settings.llm_allow_external
                and settings.gemini_api_key
                and settings.gemini_model
            ):
                providers.append(Gemini(settings.gemini_api_key, settings.gemini_model))
            elif (
                name == "groq"
                and settings.llm_allow_external
                and settings.groq_api_key
                and settings.groq_model
            ):
                providers.append(Groq(settings.groq_api_key, settings.groq_model))
            elif name == "ollama" and settings.ollama_chat_model:
                providers.append(
                    Ollama(
                        settings.ollama_host,
                        settings.ollama_chat_model,
                        settings.ollama_timeout,
                        settings.ollama_num_ctx,
                    )
                )
        return cls(conn, providers, settings.llm_cache_mode)

    @property
    def configured(self) -> bool:
        return bool(self.providers)

    @property
    def has_external(self) -> bool:
        """True when a prompt might leave this machine (any provider other than local Ollama)."""
        return any(p.name != "ollama" for p in self.providers)

    def _key(self, provider: Provider, prompt: str) -> str:
        raw = f"{provider.name}|{provider.model}|{TEMPLATE_VERSION}|{prompt}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def complete_json(self, prompt: str) -> dict[str, Any] | None:
        prompt = scrub(prompt)
        self.last_provider = None
        self.failures = []
        for provider in self.providers:
            key = self._key(provider, prompt)
            if self.mode in ("replay", "read-through"):
                row = self.conn.execute(
                    "SELECT response FROM llm_cache WHERE key = ?", (key,)
                ).fetchone()
                if row:
                    self.last_provider = f"{provider.name} (cached)"
                    return _parse_json(row[0])
            if self.mode == "replay":
                self.failures.append(f"{provider.name}: no saved answer (LLM_CACHE_MODE=replay)")
                continue
            try:
                text = provider.complete(prompt)
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
                reason = why_failed(provider, exc)
                log.warning("LLM provider gave no answer: %s", reason)
                self.failures.append(reason)
                continue
            data = _parse_json(text)
            if data is None:
                reason = (
                    f"{_LABEL.get(provider.name, provider.name)} ({provider.model}) "
                    "replied with something other than JSON"
                )
                log.warning("LLM provider gave no answer: %s", reason)
                self.failures.append(reason)
                continue
            if self.mode in ("record", "read-through"):
                with self.conn:
                    self.conn.execute(
                        "INSERT OR REPLACE INTO llm_cache(key, provider, model, response, created_at)"
                        " VALUES (?, ?, ?, ?, ?)",
                        (key, provider.name, provider.model, text, int(time.time())),
                    )
            self.last_provider = provider.name
            return data
        return None
