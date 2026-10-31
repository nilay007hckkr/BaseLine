"""Components and static service-to-service calls.

A file belongs to a service when it sits in `<container>/<service>/` (container names in any
case):
- children of `src/`, `services/`, `microservices/` and `svc/` are services (Online Boutique,
  eShop and the OpenTelemetry Demo use `src/`);
- children of `app/`, `apps/` and `cmd/` are services only when they hold a build or entry file of
  their own (a Dockerfile, go.mod, main.go, package.json and the like), so that layers such as a
  Rails `app/models/` stay one component;
- children of `packages/` and `modules/` are services only with their own Dockerfile (these
  folders hold libraries as often as services).
A file also belongs to a service when the service is a top-level folder: two or more top-level
folders that each have their own Dockerfile (account-service/, auth-service/ … as in Spring
microservice repositories). Every other file belongs to its top-level directory. A service calls another when its own
(non-generated) source uses the other's gRPC client, stub or address variable, with the service
names taken from the repository's `.proto` files."""

from __future__ import annotations

import posixpath
import re
from pathlib import Path

from hindsight.brain.gitlog import is_generated

_SERVICE_DECL = re.compile(r"^\s*service\s+(\w+)\s*\{", re.MULTILINE)
_SOURCE_EXT = (".go", ".py", ".js", ".ts", ".java", ".cs", ".rb", ".kt")
SERVICE_DIRS = ("src", "services", "microservices", "svc")
MARKED_SERVICE_DIRS = ("app", "apps", "cmd")
DEPLOYED_SERVICE_DIRS = ("packages", "modules")
CONTAINERS = SERVICE_DIRS + MARKED_SERVICE_DIRS + DEPLOYED_SERVICE_DIRS
_DEPLOY = frozenset({"dockerfile", "containerfile"})
_MARKERS = frozenset(
    {
        "dockerfile",
        "containerfile",
        "go.mod",
        "main.go",
        "package.json",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "cargo.toml",
        "pyproject.toml",
        "setup.py",
        "requirements.txt",
        "gemfile",
        "program.cs",
    }
)


def _is_marker(path: str) -> bool:
    name = posixpath.basename(path).lower()
    return name in _MARKERS or name.endswith(".csproj")


def _is_deploy(path: str) -> bool:
    return posixpath.basename(path).lower() in _DEPLOY


def _top_level_services(head: set[str]) -> set[str]:
    """Top-level folders with their own Dockerfile, when there are two or more of them."""
    found = {
        parts[0]
        for p in head
        if len(parts := p.split("/")) == 2
        and _is_deploy(p)
        and parts[0].lower() not in CONTAINERS
        and not parts[0].startswith(".")
    }
    return found if len(found) >= 2 else set()


def discover_services(head: set[str]) -> set[str]:
    services: set[str] = set()
    for p in head:
        parts = p.split("/")
        if len(parts) < 3:
            continue
        first = parts[0].lower()
        if (
            first in SERVICE_DIRS
            or (first in MARKED_SERVICE_DIRS and _is_marker(p))
            or (first in DEPLOYED_SERVICE_DIRS and len(parts) == 3 and _is_deploy(p))
        ):
            services.add(parts[1])
    return services | _top_level_services(head)


def component_of(path: str, services: set[str]) -> tuple[str, str]:
    """(kind, name) of the component a path belongs to."""
    parts = path.split("/")
    if len(parts) >= 3 and parts[0].lower() in CONTAINERS and parts[1] in services:
        return "Service", parts[1]
    if len(parts) >= 2 and parts[0] in services and parts[0].lower() not in CONTAINERS:
        return "Service", parts[0]  # a top-level service folder
    if len(parts) >= 2:
        return "Component", parts[0]
    return "Component", "(root)"


def proto_services(repo: Path, head: set[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for path in sorted(p for p in head if p.endswith(".proto")):
        try:
            text = (repo / path).read_text(errors="replace")
        except OSError:
            continue
        for name in _SERVICE_DECL.findall(text):
            found.setdefault(name, path)
    return found


def _key(name: str) -> str:
    """'CartService', 'cartservice', 'cart', 'Basket.API' and 'product-catalog' compare equal to their
    peers: lower case, letters and digits only, without a trailing 'service' or 'api'."""
    key = re.sub(r"[^a-z0-9]", "", name.lower())
    for suffix in ("service", "api"):
        if key.endswith(suffix) and len(key) > len(suffix):
            key = key[: -len(suffix)]
    return key


def _owner(proto_name: str, services: set[str]) -> str | None:
    if proto_name.lower() in services:
        return proto_name.lower()
    matches = [s for s in services if _key(s) == _key(proto_name)]
    return matches[0] if len(matches) == 1 else None  # ambiguous or unknown: no owner


def _address_vars(proto_name: str) -> list[str]:
    """CartService → CART_SERVICE_ADDR and CART_ADDR (both conventions are common)."""
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", proto_name).upper()
    names = [f"{snake}_ADDR"]
    if snake.endswith("_SERVICE"):
        names.append(f"{snake.removesuffix('_SERVICE')}_ADDR")
    return names


def service_calls(
    repo: Path, head: set[str], services: set[str]
) -> list[tuple[str, str, dict[str, str | int]]]:
    """(caller, callee, evidence) for every service whose source references another's client."""
    targets = {
        name: owner
        for name in proto_services(repo, head)
        if (owner := _owner(name, services)) is not None
    }
    patterns = {
        name: re.compile(
            rf"\b(New{name}Client|{name}Client|{name}Stub|{name}Grpc|{'|'.join(_address_vars(name))})\b"
        )
        for name in targets
    }
    calls: dict[tuple[str, str], dict[str, str | int]] = {}
    for path in sorted(head):
        if not path.endswith(_SOURCE_EXT) or is_generated(path):
            continue
        kind, caller = component_of(path, services)
        if kind != "Service":
            continue
        try:
            text = (repo / path).read_text(errors="replace")
        except OSError:
            continue
        if len(text) > 400_000:
            continue
        for name, pattern in patterns.items():
            callee = targets[name]
            if callee == caller or (caller, callee) in calls:
                continue
            m = pattern.search(text)
            if m:
                line = text.count("\n", 0, m.start()) + 1
                calls[(caller, callee)] = {
                    "file": path,
                    "line": line,
                    "match": m.group(0),
                    "proto": name,
                }
    return [(a, b, ev) for (a, b), ev in sorted(calls.items())]
