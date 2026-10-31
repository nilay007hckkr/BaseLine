"""Service folders across repository layouts, and matching .proto service names to them
(table-driven; the small repositories below are FIXTURES written by the tests)."""

from pathlib import Path

import pytest

from hindsight.brain.static import (
    _address_vars,
    _owner,
    component_of,
    discover_services,
    service_calls,
)


@pytest.mark.parametrize(
    ("head", "services"),
    [
        # src/<svc>/: Online Boutique, eShop, OpenTelemetry Demo (unchanged: no marker needed)
        (
            {"src/cartservice/main.go", "src/frontend/x/handlers.go", "src/README.md"},
            {"cartservice", "frontend"},
        ),
        (
            {"services/billing/handler.py", "microservices/auth/a.ts", "svc/ledger/l.go"},
            {"billing", "auth", "ledger"},
        ),
        # app/<svc>/ with an entry file of its own (go-kratos beer-shop)
        (
            {
                "app/user/service/cmd/server/main.go",
                "app/user/service/internal/biz/user.go",
                "app/catalog/service/cmd/server/main.go",
                "api/user/service/v1/user.proto",
            },
            {"user", "catalog"},
        ),
        (
            {"apps/web/package.json", "apps/web/src/index.ts", "cmd/worker/main.go"},
            {"web", "worker"},
        ),
        # a Rails or Next.js app/ holds layers, not services: no entry file, no service
        ({"app/models/user.rb", "app/controllers/users_controller.rb", "Gemfile"}, set()),
        ({"app/dashboard/page.tsx", "package.json"}, set()),
        ({"README.md", "lib/slugify.js"}, set()),
        # top-level service folders, each with its own Dockerfile (Spring "piggymetrics" style)
        (
            {
                "account-service/Dockerfile",
                "account-service/pom.xml",
                "account-service/src/main/java/App.java",
                "auth-service/Dockerfile",
                "gateway/Dockerfile",
                "docs/README.md",
                "pom.xml",
            },
            {"account-service", "auth-service", "gateway"},
        ),
        # one deployable top-level folder is just an app, not a set of services
        ({"web/Dockerfile", "web/src/index.ts", "docs/a.md"}, set()),
        # workspace folders: only packages with their own Dockerfile are services
        (
            {"packages/api/Dockerfile", "packages/api/src/a.ts", "packages/ui/package.json"},
            {"api"},
        ),
        # container names in any case
        ({"Services/Orders/Program.cs", "Services/Orders/Orders.csproj"}, {"Orders"}),
    ],
)
def test_services_are_found_in_common_layouts(head, services):
    assert discover_services(head) == services


def test_files_outside_a_service_keep_their_top_level_component():
    services = {"user", "cartservice"}
    assert component_of("app/user/service/internal/biz/user.go", services) == ("Service", "user")
    assert component_of("src/cartservice/main.go", services) == ("Service", "cartservice")
    assert component_of("app/models/user.rb", services) == ("Component", "app")
    assert component_of("api/user/service/v1/user.proto", services) == ("Component", "api")
    assert component_of("Makefile", services) == ("Component", "(root)")


def test_files_in_a_top_level_service_folder_belong_to_it():
    services = discover_services(
        {"account-service/Dockerfile", "auth-service/Dockerfile", "account-service/src/App.java"}
    )
    assert component_of("account-service/src/App.java", services) == ("Service", "account-service")
    assert component_of("docs/README.md", services) == ("Component", "docs")


def test_calls_are_found_between_app_services(tmp_path: Path):
    files = {
        "api/user/service/v1/user.proto": "syntax = 'proto3';\nservice User {\n  rpc Get (Req) returns (Res);\n}\n",
        "app/user/service/cmd/server/main.go": "package main\nfunc main() {}\n",
        "app/shop/interface/cmd/server/main.go": "package main\nfunc main() {}\n",
        "app/shop/interface/internal/data/data.go": "package data\n\nfunc NewUserRepo(conn Conn) {\n\tc := userv1.NewUserClient(conn)\n}\n",
    }
    for path, text in files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text)
    head = set(files)
    services = discover_services(head)
    assert services == {"user", "shop"}
    calls = service_calls(tmp_path, head, services)
    assert [(a, b) for a, b, _ in calls] == [("shop", "user")]
    assert calls[0][2] == {
        "file": "app/shop/interface/internal/data/data.go",
        "line": 4,
        "match": "NewUserClient",
        "proto": "User",
    }


SERVICES = {
    "cartservice",  # Online Boutique
    "cart",  # OpenTelemetry Demo
    "product-catalog",
    "Basket.API",  # eShop
    "Ordering.API",
    "WebApp",
}


@pytest.mark.parametrize(
    ("proto", "services", "owner"),
    [
        ("CartService", {"cartservice", "frontend"}, "cartservice"),  # exact, as before
        ("CartService", {"cart", "frontend"}, "cart"),
        ("ProductCatalogService", {"product-catalog", "cart"}, "product-catalog"),
        ("Basket", {"Basket.API", "WebApp"}, "Basket.API"),
        ("OrderingService", {"Ordering.API", "WebApp"}, "Ordering.API"),
        ("CartService", SERVICES, "cartservice"),  # an exact folder name wins
        ("CartService", {"cart", "cart-service"}, None),  # two folders match: ambiguous
        ("PaymentService", SERVICES, None),  # no such folder
    ],
)
def test_proto_service_owner(proto, services, owner):
    assert _owner(proto, services) == owner


def test_address_variables_cover_both_conventions():
    assert _address_vars("CartService") == ["CART_SERVICE_ADDR", "CART_ADDR"]
    assert _address_vars("Basket") == ["BASKET_ADDR"]
