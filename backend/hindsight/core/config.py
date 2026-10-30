"""Settings from environment variables, with the repository's .env file as a fallback."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPO = "https://github.com/GoogleCloudPlatform/microservices-demo"


def _dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.is_file():
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (ROOT / path).resolve()


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    db_path: Path
    repo_url: str
    github_token: str
    forge_max_requests: int
    llm_provider_order: tuple[str, ...]
    llm_cache_mode: str
    llm_allow_external: bool
    gemini_api_key: str
    gemini_model: str
    groq_api_key: str
    groq_model: str
    ollama_host: str
    ollama_chat_model: str
    docs_allow_external: bool = False
    kind_cluster: str = "hs-target"
    target_url: str = "http://localhost:8081"  # the demo shop
    alert_webhook: str = ""
    embed_model: str = "nomic-embed-text"  # local (Ollama); semantic search once indexed
    answer_support_check: bool = True  # a configured model re-checks claims (can only remove)
    # auto: Neo4j when it is running and in sync with SQLite, else SQLite | sqlite | neo4j
    graph_backend: str = "auto"
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""

    @property
    def repos_dir(self) -> Path:
        return self.data_dir / "repos"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def forge_dir(self) -> Path:
        return self.data_dir / "forge"


def load_settings(env: dict[str, str] | None = None) -> Settings:
    source = {**_dotenv(ROOT / ".env"), **os.environ} if env is None else env

    def get(key: str, default: str = "") -> str:
        return source.get(key) or default

    data_dir = _path(get("DATA_DIR", "./data"))
    return Settings(
        data_dir=data_dir,
        db_path=_path(get("DB_PATH", str(data_dir / "hindsight.db"))),
        repo_url=get("TARGET_REPO_URL", DEFAULT_REPO),
        github_token=get("GITHUB_TOKEN"),
        forge_max_requests=int(get("FORGE_MAX_REQUESTS", "50")),
        llm_provider_order=tuple(
            p.strip()
            for p in get("LLM_PROVIDER_ORDER", "gemini,groq,ollama").split(",")
            if p.strip()
        ),
        llm_cache_mode=get("LLM_CACHE_MODE", "read-through"),
        llm_allow_external=get("LLM_ALLOW_EXTERNAL", "true").lower() == "true",
        gemini_api_key=get("GEMINI_API_KEY"),
        gemini_model=get("GEMINI_MODEL"),
        groq_api_key=get("GROQ_API_KEY"),
        groq_model=get("GROQ_MODEL"),
        ollama_host=get("OLLAMA_HOST", "http://localhost:11434"),
        ollama_chat_model=get("OLLAMA_CHAT_MODEL"),
        docs_allow_external=get("DOCS_ALLOW_EXTERNAL", "false").lower() == "true",
        kind_cluster=get("KIND_CLUSTER_NAME", "hs-target"),
        target_url=get("TARGET_URL", "http://localhost:8081"),
        alert_webhook=get("ALERT_WEBHOOK_URL"),
        embed_model=get("OLLAMA_EMBED_MODEL", "nomic-embed-text"),
        answer_support_check=get("ANSWER_SUPPORT_CHECK", "true").lower() == "true",
        graph_backend=get("GRAPH_BACKEND", "auto").lower(),
        neo4j_uri=get("NEO4J_URI", "bolt://localhost:7687"),
        neo4j_user=get("NEO4J_USER", "neo4j"),
        neo4j_password=get("NEO4J_PASSWORD") or _local_secret(NEO4J_PASSWORD_FILE),
    )


# scripts/graph.sh writes a random password here (git-ignored) when NEO4J_PASSWORD is not set
NEO4J_PASSWORD_FILE = ROOT / "data" / "neo4j" / "password"


def _local_secret(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""
