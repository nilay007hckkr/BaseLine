# FIXTURE: recorded pages of the local demo shop

Saved with `curl` from Online Boutique v0.10.7 running on the local kind cluster
(http://localhost:8081) on 2026-10-30, while the shop was healthy:

| File | Page |
|---|---|
| `home.html` | `/` |
| `product.html` | `/product/OLJCESPC7Z` (Sunglasses, $19.99) |
| `cart.html` | `/cart` with one item |
| `done.html` | the order confirmation after `POST /cart/checkout` |
| `empty.html` | `/cart` after the order (empty cart) |

The checkout form holds the shop's own built-in sample address and card values; no personal data.
The SentinelQA tests use these pages to check the mutation rewrites and to run the browser
tests without the cluster (`tests/test_sentinel.py`).
