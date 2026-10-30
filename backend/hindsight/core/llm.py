"""Optional LLM chain (Gemini → Groq → Ollama, in LLM_PROVIDER_ORDER) with a SQLite cache.

The model only phrases evidence that code has already selected; callers validate every citation.
Cache modes: off (always live), record (live, then store), replay (cache only, never live),
read-through (cache first, live on a miss). Text is scrubbed of keys, tokens and email addresses
before it leaves the process."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from typing import Any, Protocol

import httpx

from hindsight.core.config import Settings

TEMPLATE_VERSION = "ask-v1"
_SECRET = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}"
    r"|AIza[0-9A-Za-z_-]{30,}|[\w.+-]+@[\w-]+\.[\w.-]+"
)
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


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
            timeout=60,
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
            timeout=60,
        )
        resp.raise_for_status()
        return str(resp.json()["choices"][0]["message"]["content"])


class Ollama:
    name = "ollama"

    def __init__(self, host: str, model: str) -> None:
        self.host, self.model = host.rstrip("/"), model

    def complete(self, prompt: str) -> str:
        resp = httpx.post(
            f"{self.host}/api/chat",
            json={
                "model": self.model,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0},
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=120,
        )
        resp.raise_for_status()
        return str(resp.json()["message"]["content"])


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
                providers.append(Ollama(settings.ollama_host, settings.ollama_chat_model))
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
                continue
            try:
                text = provider.complete(prompt)
            except (httpx.HTTPError, KeyError, IndexError, ValueError):
                continue
            data = _parse_json(text)
            if data is None:
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
