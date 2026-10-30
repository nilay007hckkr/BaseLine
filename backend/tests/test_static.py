"""Matching .proto service names to service folders across repository conventions (table-driven)."""

import pytest

from hindsight.brain.static import _address_vars, _owner

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
