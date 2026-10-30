"""Mutation harness (EVALUATION §4). SYNTHETIC: every mutation is applied on purpose, in the test's
own browser only (Playwright request routing rewrites the shop's HTML or answers a request with an
error); the shop itself is never changed.

Cosmetic mutations change how a control looks or is labelled but not what it does; the healer
should keep the test green. Defect mutations break the feature; the test must fail and the healer
must refuse. Cosmetic mutations mark the element a heal should land on with data-hs-truth, which
the healer never reads (it is only used to score the result)."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlparse

from playwright.sync_api import Error, Page, Route

from hindsight.sentinel.spec import Spec

Rewrite = Callable[[str], str]
TRUTH = ' data-hs-truth="1"'


def _control(text: str) -> re.Pattern[str]:
    """A <button> or <a> whose only content is `text` (surrounding whitespace allowed)."""
    return re.compile(rf"<(button|a)\b([^>]*)>(\s*){re.escape(text)}(\s*)</\1>")


def _edit(text: str, fn: Callable[[str, str, str, str], str]) -> Rewrite:
    pattern = _control(text)
    return lambda html: pattern.sub(
        lambda m: fn(m.group(1), m.group(2), m.group(3), m.group(4)), html
    )


def class_rename(text: str) -> Rewrite:
    def fn(tag: str, attrs: str, a: str, b: str) -> str:
        attrs = attrs.replace("cymbal-button-primary", "btn-main").replace(
            "cymbal-button-secondary", "btn-alt"
        )
        return f"<{tag}{attrs}{TRUTH}>{a}{text}{b}</{tag}>"

    return _edit(text, fn)


def id_added(text: str) -> Rewrite:
    slug = re.sub(r"[^a-z]+", "-", text.lower()).strip("-")
    return _edit(text, lambda t, at, a, b: f'<{t}{at} id="hs-{slug}"{TRUTH}>{a}{text}{b}</{t}>')


def wrapped(text: str) -> Rewrite:
    return _edit(
        text, lambda t, at, a, b: f'<span class="hs-wrap"><{t}{at}{TRUTH}>{a}{text}{b}</{t}></span>'
    )


def relabelled(text: str, new: str) -> Rewrite:
    return _edit(text, lambda t, at, a, b: f"<{t}{at}{TRUTH}>{a}{new}{b}</{t}>")


def aria_label(text: str, label: str) -> Rewrite:
    return _edit(
        text, lambda t, at, a, b: f'<{t}{at} aria-label="{label}"{TRUTH}>{a}{text}{b}</{t}>'
    )


def as_input(text: str) -> Rewrite:
    def fn(tag: str, attrs: str, a: str, b: str) -> str:
        attrs = re.sub(r'\s*type="[^"]*"', "", attrs)
        return f'<input type="submit"{attrs} value="{text}"{TRUTH}>'

    return _edit(text, fn)


def removed(text: str) -> Rewrite:
    return _edit(text, lambda t, at, a, b: "")


def disabled(text: str) -> Rewrite:
    return _edit(text, lambda t, at, a, b: f"<{t}{at} disabled>{a}{text}{b}</{t}>")


def replaced(old: str, new: str) -> Rewrite:
    return lambda html: html.replace(old, new)


def _add_to_cart_first(html: str) -> str:
    """Move the Add To Cart button above the quantity picker (sibling reorder)."""
    return re.sub(
        r'(<div class="product-quantity-dropdown">.*?</div>)(\s*)(<button\b[^>]*>)(Add To Cart</button>)',
        lambda m: m.group(3).replace(">", TRUTH + ">", 1) + m.group(4) + m.group(2) + m.group(1),
        html,
        flags=re.DOTALL,
    )


def _swap_empty_and_continue(html: str) -> str:
    """Swap the Empty Cart button and the Continue Shopping link (sibling reorder)."""
    return re.sub(
        r"(<button\b)([^>]*>\s*Empty Cart\s*</button>)(\s*)(<a\b[^>]*>\s*Continue Shopping\s*</a>)",
        lambda m: m.group(4) + m.group(3) + m.group(1) + TRUTH + m.group(2),
        html,
    )


@dataclass(frozen=True)
class Mutation:
    id: str
    kind: Literal["cosmetic", "defect"]
    description: str
    target: str  # the UI text this mutation is about; selects the tests that exercise it
    page: str = ""  # regex on the URL path whose HTML is rewritten
    rewrite: Rewrite | None = None
    fail_post: str = ""  # answer this POST path with HTTP 500 instead
    after_order: bool = False  # rewrite only after this browser placed an order
    only: tuple[str, ...] = field(default=())

    def affects(self, spec: Spec) -> bool:
        if self.only:
            return spec.id in self.only
        return any(
            (s.target is not None and s.op == "click" and s.target.name == self.target)
            or (s.op.startswith("expect") and s.value == self.target)
            for s in spec.steps
        )

    def installer(self) -> tuple[Callable[[Page], None], dict[str, Any]]:
        """A function that installs this mutation on a page, and a dict counting rewrites (a mutation
        that never changed anything is reported, not silently counted as a pass)."""
        state: dict[str, Any] = {"applied": 0}

        def install(page: Page) -> None:
            ordered = {"done": False}

            def handle(route: Route) -> None:
                request = route.request
                path = urlparse(request.url).path
                try:
                    if self.fail_post and request.method == "POST" and path == self.fail_post:
                        state["applied"] += 1
                        route.fulfill(
                            status=500,
                            content_type="text/html",
                            body="<h1>Internal Server Error</h1><p>synthetic fault (mutation harness)</p>",
                        )
                        return
                    if request.resource_type != "document" or self.rewrite is None:
                        route.continue_()
                        return
                    response = route.fetch()
                    if path == "/cart/checkout" and response.ok:
                        ordered["done"] = True
                    final = urlparse(response.url).path
                    body = response.text()
                    if re.search(self.page, final) and (not self.after_order or ordered["done"]):
                        new = self.rewrite(body)
                        if new != body:
                            state["applied"] += 1
                        route.fulfill(response=response, body=new)
                        return
                    route.fulfill(response=response)
                except Error:
                    return  # the page went away mid-request; nothing to do

            page.route("**/*", handle)

        return install, state


PRODUCT, CART, ORDER = r"^/product/", r"^/cart$", r"^/cart/checkout$"


def catalogue() -> list[Mutation]:
    cosmetic: list[Mutation] = []
    controls = {
        # text: (page, case variant, synonym, aria label, can become <input>)
        "Add To Cart": (PRODUCT, "Add to cart", "Add to Bag", "Add this item to your cart", True),
        "Place Order": (
            CART,
            "Place order",
            "Complete Order",
            "Confirm and place your order",
            True,
        ),
        "Empty Cart": (CART, "Empty cart", "Clear Cart", "Remove all items from the cart", True),
        "Continue Shopping": (
            CART,
            "Continue shopping",
            "Keep Shopping",
            "Back to the shop",
            False,
        ),
    }
    for text, (page, case, synonym, label, inputable) in controls.items():
        slug = re.sub(r"[^a-z]+", "-", text.lower()).strip("-")
        variants: list[tuple[str, str, Rewrite]] = [
            ("class-rename", "CSS classes renamed", class_rename(text)),
            ("text-case", f'label "{text}" → "{case}"', relabelled(text, case)),
            ("synonym", f'label "{text}" → "{synonym}"', relabelled(text, synonym)),
            ("aria-label", f'accessible name set to "{label}"', aria_label(text, label)),
        ]
        if text != "Continue Shopping":
            variants += [
                ("id-added", "an id attribute added", id_added(text)),
                ("wrapped", "wrapped in an extra <span>", wrapped(text)),
            ]
        if inputable:
            variants.append(
                ("as-input", "<button> replaced by <input type=submit>", as_input(text))
            )
        for name, description, rewrite in variants:
            cosmetic.append(
                Mutation(
                    f"{slug}:{name}", "cosmetic", f"{text}: {description}", text, page, rewrite
                )
            )
    cosmetic.append(
        Mutation(
            "add-to-cart:reorder",
            "cosmetic",
            "Add To Cart: moved above the quantity picker",
            "Add To Cart",
            PRODUCT,
            _add_to_cart_first,
        )
    )
    cosmetic.append(
        Mutation(
            "empty-cart:reorder",
            "cosmetic",
            "Empty Cart and Continue Shopping swapped",
            "Empty Cart",
            CART,
            _swap_empty_and_continue,
        )
    )
    defects = [
        Mutation(
            "add-to-cart:removed",
            "defect",
            "Add To Cart button removed",
            "Add To Cart",
            PRODUCT,
            removed("Add To Cart"),
        ),
        Mutation(
            "place-order:removed",
            "defect",
            "Place Order button removed",
            "Place Order",
            CART,
            removed("Place Order"),
        ),
        Mutation(
            "place-order:disabled",
            "defect",
            "Place Order button disabled",
            "Place Order",
            CART,
            disabled("Place Order"),
        ),
        Mutation(
            "empty-cart:removed",
            "defect",
            "Empty Cart button removed",
            "Empty Cart",
            CART,
            removed("Empty Cart"),
        ),
        Mutation(
            "checkout:http-500",
            "defect",
            "checkout answers HTTP 500",
            "Your order is complete!",
            fail_post="/cart/checkout",
        ),
        Mutation(
            "product:wrong-price",
            "defect",
            "product page shows $29.99 instead of $19.99",
            "$19.99",
            PRODUCT,
            replaced("$19.99", "$29.99"),
        ),
        Mutation(
            "order:wrong-heading",
            "defect",
            "order page says the order could not be placed",
            "Your order is complete!",
            ORDER,
            replaced("Your order is complete!", "Your order could not be placed"),
        ),
        Mutation(
            "cart:kept-after-order",
            "defect",
            "the cart still holds the items after a confirmed order (the bug in issue #3578)",
            "Your shopping cart is empty!",
            CART,
            replaced("<h3>Your shopping cart is empty!</h3>", "<h3>Cart (1)</h3>"),
            after_order=True,
            only=("OB-3578",),
        ),
    ]
    return cosmetic + defects
