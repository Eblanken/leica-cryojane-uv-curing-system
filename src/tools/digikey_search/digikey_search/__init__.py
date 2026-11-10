"""Read-only DigiKey part search (Product Information v4).

Credentials come only from the environment: DIGIKEY_CLIENT_ID and DIGIKEY_CLIENT_SECRET,
from the `parts-search` production app. The client uses the two-legged
client-credentials flow, so it can search and read prices but cannot order.
"""

from .client import DigiKeyClient, DigiKeyError, flatten_product, price_at

__all__ = ["DigiKeyClient", "DigiKeyError", "flatten_product", "price_at"]
