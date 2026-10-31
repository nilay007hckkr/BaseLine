"""The provider chain: falling through to the next provider, saying why each one gave no answer,
and the settings that let a slow local model finish. No network: providers are fakes, and the
Ollama request is captured instead of sent."""

import json
import sqlite3

import httpx

from hindsight.core.config import load_settings
from hindsight.core.llm import Chain, Ollama, why_failed

REQUEST = httpx.Request("POST", "http://localhost:11434/api/chat")


class Fails:
    def __init__(self, name: str, model: str, exc: Exception, timeout: float = 60) -> None:
        self.name, self.model, self.exc, self.timeout = name, model, exc, timeout
        if name == "ollama":
            self.host = "http://localhost:11434"  # only the local provider has a host

    def complete(self, prompt: str) -> str:
        raise self.exc


class Answers:
    def __init__(self, name: str, reply: str) -> None:
        self.name, self.model, self.reply = name, f"{name}-model", reply

    def complete(self, prompt: str) -> str:
        return self.reply


def _chain(*providers: object) -> Chain:
    return Chain(sqlite3.connect(":memory:"), list(providers), "off")  # type: ignore[arg-type]


def test_an_offline_cloud_provider_falls_through_to_the_local_model():
    chain = _chain(
        Fails("gemini", "gemini-x", httpx.ConnectError("no route to host", request=REQUEST)),
        Answers("ollama", '{"claims": []}'),
    )
    assert chain.complete_json("question") == {"claims": []}
    assert chain.last_provider == "ollama"
    assert chain.failures == ["Gemini (gemini-x) is not reachable (offline?)"]


def test_each_failure_is_named_in_plain_words():
    slow = Fails("ollama", "qwen2.5:7b", httpx.ReadTimeout("slow", request=REQUEST), timeout=300)
    missing = Fails(
        "ollama",
        "qwen2.5:7b",
        httpx.HTTPStatusError(
            "404", request=REQUEST, response=httpx.Response(404, request=REQUEST)
        ),
    )
    assert why_failed(slow, slow.exc) == (
        "Ollama (qwen2.5:7b) did not answer in time (timed out after 300 s)"
    )
    assert "ollama pull qwen2.5:7b" in why_failed(missing, missing.exc)


def test_when_nothing_answers_the_chain_says_why_and_forgets_the_last_provider():
    chain = _chain(Answers("groq", '{"claims": []}'))
    chain.complete_json("first")
    assert chain.last_provider == "groq"
    chain.providers = [Answers("ollama", "Sure! Here is my answer.")]  # type: ignore[list-item]
    assert chain.complete_json("second") is None
    assert chain.last_provider is None
    assert chain.failures == ["Ollama (ollama-model) replied with something other than JSON"]


def test_replay_mode_without_a_saved_answer_is_reported():
    chain = Chain(sqlite3.connect(":memory:"), [Answers("ollama", "{}")], "replay")  # type: ignore[list-item]
    chain.conn.execute(
        "CREATE TABLE llm_cache(key TEXT PRIMARY KEY, provider, model, response, created_at)"
    )
    assert chain.complete_json("question") is None
    assert "LLM_CACHE_MODE=replay" in chain.failures[0]


def test_the_local_model_gets_time_and_a_context_window_that_holds_the_prompt(monkeypatch):
    sent: dict[str, object] = {}

    def post(url: str, **kwargs: object) -> httpx.Response:
        sent.update(kwargs, url=url)
        return httpx.Response(
            200, request=REQUEST, content=json.dumps({"message": {"content": "{}"}}).encode()
        )

    monkeypatch.setattr(httpx, "post", post)
    Ollama("http://localhost:11434", "qwen2.5:7b", timeout=300, num_ctx=8192).complete("hi")
    assert sent["json"]["options"]["num_ctx"] == 8192  # type: ignore[index]
    timeout = sent["timeout"]
    # a local model either connects at once or is not running: fail fast (1 s), read for long
    assert isinstance(timeout, httpx.Timeout) and timeout.read == 300 and timeout.connect == 1.0
    Ollama("http://gpu-box.lan:11434", "qwen2.5:7b", timeout=300).complete("hi")
    remote = sent["timeout"]
    assert isinstance(remote, httpx.Timeout) and remote.connect == 5.0


def test_settings_carry_the_local_model_limits_into_the_chain():
    settings = load_settings(
        {
            "LLM_PROVIDER_ORDER": "gemini,ollama",
            "OLLAMA_CHAT_MODEL": "qwen2.5:7b",
            "OLLAMA_TIMEOUT": "120",
            "OLLAMA_NUM_CTX": "4096",
        }
    )
    chain = Chain.from_settings(sqlite3.connect(":memory:"), settings)
    (local,) = chain.providers  # no Gemini key: only Ollama joins
    assert isinstance(local, Ollama)
    assert (local.model, local.timeout, local.num_ctx) == ("qwen2.5:7b", 120.0, 4096)
