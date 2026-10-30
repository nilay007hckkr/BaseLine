"""Model choice from the dashboard: provider order and model per provider.

API keys stay in .env (AGENTS.md rule 7): this module reports whether a key is set, never its value,
and never writes one. The non-secret choices are stored per instance in `<DATA_DIR>/settings.json`
and override the .env defaults. The model only phrases evidence that code selected, so the choice
changes wording, never rankings or verdicts."""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, replace
from pathlib import Path

import httpx

from hindsight.core.config import Settings
from hindsight.core.schemas import (
    LlmProvider,
    LlmSettings,
    LlmSettingsUpdate,
    ModelList,
    ProviderName,
)

_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
NAMES: tuple[ProviderName, ...] = ("gemini", "groq", "ollama")


def provider_name(value: str) -> ProviderName | None:
    return next((n for n in NAMES if n == value), None)


@dataclass(frozen=True)
class _Spec:
    label: str
    external: bool
    key_env: str | None
    model_env: str


SPECS = {
    "gemini": _Spec("Gemini", True, "GEMINI_API_KEY", "GEMINI_MODEL"),
    "groq": _Spec("Groq", True, "GROQ_API_KEY", "GROQ_MODEL"),
    "ollama": _Spec("Ollama (local)", False, None, "OLLAMA_CHAT_MODEL"),
}


def _key(settings: Settings, name: str) -> str:
    return {"gemini": settings.gemini_api_key, "groq": settings.groq_api_key}.get(name, "")


def _model(settings: Settings, name: str) -> str:
    return {
        "gemini": settings.gemini_model,
        "groq": settings.groq_model,
        "ollama": settings.ollama_chat_model,
    }[name]


class Prefs:
    """The dashboard's model choices for one instance (a small JSON file, no secrets)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._data = self._read()

    def _read(self) -> dict[str, object]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def apply(self, settings: Settings) -> Settings:
        with self._lock:
            data = dict(self._data)
        order = data.get("order")
        models = data.get("models")
        if isinstance(order, list):
            settings = replace(
                settings, llm_provider_order=tuple(str(n) for n in order if n in NAMES)
            )
        if isinstance(models, dict):
            pick = {str(k): str(v) for k, v in models.items() if k in NAMES and v}
            settings = replace(
                settings,
                gemini_model=pick.get("gemini", settings.gemini_model),
                groq_model=pick.get("groq", settings.groq_model),
                ollama_chat_model=pick.get("ollama", settings.ollama_chat_model),
            )
        return settings

    def save(self, update: LlmSettingsUpdate) -> None:
        if len(set(update.order)) != len(update.order):
            raise ValueError("each provider may appear once in the order")
        for name, model in update.models.items():
            if model and not _MODEL.match(model):
                raise ValueError(f"not a model name: {model!r}")
            if name not in NAMES:
                raise ValueError(f"unknown provider: {name}")
        with self._lock:
            current = self._data.get("models")
            models = dict(current) if isinstance(current, dict) else {}
            models.update({k: v for k, v in update.models.items() if v})
            self._data = {"order": list(update.order), "models": models}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._data, indent=2) + "\n")


def view(settings: Settings) -> LlmSettings:
    """The providers as the chain will see them (`settings` already has the prefs applied)."""
    providers: list[LlmProvider] = []
    for name in NAMES:
        spec = SPECS[name]
        key_set = bool(_key(settings, name)) if spec.key_env else True
        model = _model(settings, name)
        ready = bool(model) and key_set and (settings.llm_allow_external or not spec.external)
        providers.append(
            LlmProvider(
                name=name,
                label=spec.label,
                external=spec.external,
                key_env=spec.key_env,
                key_set=key_set if spec.key_env else False,
                model=model,
                model_env=spec.model_env,
                ready=ready,
            )
        )
    order = [n for n in map(provider_name, settings.llm_provider_order) if n]
    ready = {p.name for p in providers if p.ready}
    active = next((n for n in order if n in ready), None)
    return LlmSettings(
        order=order,
        providers=providers,
        allow_external=settings.llm_allow_external,
        active=active,
    )


def list_models(settings: Settings, name: ProviderName, client: httpx.Client) -> ModelList:
    """Ask the provider which models this key can use (Ollama: the models pulled locally)."""
    spec = SPECS[name]
    if spec.external and not settings.llm_allow_external:
        return ModelList(provider=name, models=[], error="LLM_ALLOW_EXTERNAL is false")
    if spec.key_env and not _key(settings, name):
        return ModelList(provider=name, models=[], error=f"set {spec.key_env} in .env")
    try:
        if name == "gemini":
            resp = client.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                params={"pageSize": 1000},
                headers={"x-goog-api-key": settings.gemini_api_key},
            )
            resp.raise_for_status()
            models = [
                str(m["name"]).removeprefix("models/")
                for m in resp.json().get("models", [])
                if "generateContent" in m.get("supportedGenerationMethods", [])
            ]
        elif name == "groq":
            resp = client.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            )
            resp.raise_for_status()
            models = [str(m["id"]) for m in resp.json().get("data", []) if m.get("active", True)]
        else:
            resp = client.get(f"{settings.ollama_host.rstrip('/')}/api/tags")
            resp.raise_for_status()
            models = [str(m["name"]) for m in resp.json().get("models", [])]
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        reason = (
            f"{exc.response.status_code} from {spec.label}"
            if isinstance(exc, httpx.HTTPStatusError)
            else f"{spec.label} unreachable"
        )
        return ModelList(provider=name, models=[], error=reason)
    return ModelList(provider=name, models=sorted(set(models)))
