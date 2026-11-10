"""HTTP client for DigiKey's Product Information v4 API, standard library only."""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

API_ROOT = "https://api.digikey.com"
TOKEN_URL = f"{API_ROOT}/v1/oauth2/token"
SEARCH_ROOT = f"{API_ROOT}/products/v4/search"
PAGE_LIMIT = 50  # the API's maximum results per keyword request


class DigiKeyError(RuntimeError):
    """An API call failed. The message holds the HTTP status and DigiKey's reply."""


class DigiKeyClient:
    """Search and look up parts. Tokens are fetched on first use and renewed when stale.

    Reads DIGIKEY_CLIENT_ID and DIGIKEY_CLIENT_SECRET unless both are passed in.
    Locale defaults to the US site, English, USD.
    """

    def __init__(
        self,
        client_id=None,
        client_secret=None,
        site="US",
        language="en",
        currency="USD",
        timeout=30.0,
    ):
        self.client_id = client_id or os.environ.get("DIGIKEY_CLIENT_ID")
        self._secret = client_secret or os.environ.get("DIGIKEY_CLIENT_SECRET")
        if not self.client_id or not self._secret:
            raise DigiKeyError(
                "Set DIGIKEY_CLIENT_ID and DIGIKEY_CLIENT_SECRET (the parts-search app)."
            )
        self.locale = {"Site": site, "Language": language, "Currency": currency}
        self.timeout = timeout
        self._token = None
        self._token_expires = 0.0

    def _fetch_token(self):
        body = urllib.parse.urlencode(
            {
                "client_id": self.client_id,
                "client_secret": self._secret,
                "grant_type": "client_credentials",
            }
        ).encode()
        request = urllib.request.Request(TOKEN_URL, data=body, method="POST")
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        reply = self._send(request)
        self._token = reply["access_token"]
        # Renew a minute early so a request never goes out with an expiring token.
        self._token_expires = (
            time.monotonic() + float(reply.get("expires_in", 600)) - 60
        )

    def _headers(self):
        if self._token is None or time.monotonic() >= self._token_expires:
            self._fetch_token()
        return {
            "Authorization": f"Bearer {self._token}",
            "X-DIGIKEY-Client-Id": self.client_id,
            "X-DIGIKEY-Locale-Site": self.locale["Site"],
            "X-DIGIKEY-Locale-Language": self.locale["Language"],
            "X-DIGIKEY-Locale-Currency": self.locale["Currency"],
            "Accept": "application/json",
        }

    def _send(self, request):
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as err:
            detail = err.read().decode(errors="replace")[:500]
            raise DigiKeyError(
                f"HTTP {err.code} from {request.full_url}: {detail}"
            ) from err

    def _get(self, path):
        request = urllib.request.Request(
            f"{SEARCH_ROOT}/{path}", headers=self._headers()
        )
        return self._send(request)

    def _post(self, path, payload):
        request = urllib.request.Request(
            f"{SEARCH_ROOT}/{path}",
            data=json.dumps(payload).encode(),
            headers=self._headers() | {"Content-Type": "application/json"},
            method="POST",
        )
        return self._send(request)

    def keyword_search(
        self, keywords, limit=PAGE_LIMIT, offset=0, filters=None, sort=None
    ):
        """One page of a keyword search, as DigiKey's raw JSON reply.

        `filters` is passed through as FilterOptionsRequest (category, manufacturer and
        parametric filters use DigiKey's numeric ids, which the reply's FilterOptions
        lists). `sort` is passed through as SortOptions.
        """
        payload = {
            "Keywords": keywords,
            "Limit": min(limit, PAGE_LIMIT),
            "Offset": offset,
        }
        if filters:
            payload["FilterOptionsRequest"] = filters
        if sort:
            payload["SortOptions"] = sort
        return self._post("keyword", payload)

    def search_all(self, keywords, max_results=200, filters=None, sort=None):
        """Every product for a search, paging until max_results or the end."""
        products, offset = [], 0
        while len(products) < max_results:
            page = self.keyword_search(
                keywords,
                min(PAGE_LIMIT, max_results - len(products)),
                offset,
                filters,
                sort,
            )
            batch = page.get("Products") or []
            products.extend(batch)
            offset += len(batch)
            if not batch or offset >= page.get("ProductsCount", 0):
                break
        return products

    def product_details(self, part_number):
        """Full record for one DigiKey or manufacturer part number."""
        quoted = urllib.parse.quote(part_number, safe="")
        return self._get(f"{quoted}/productdetails")


def price_at(product, quantity):
    """Lowest unit price at `quantity` across the product's packaging variations.

    Uses standard price breaks only. Returns None if no variation sells that quantity.
    """
    best = None
    for variation in product.get("ProductVariations") or []:
        if quantity < (variation.get("MinimumOrderQuantity") or 1):
            continue
        breaks = [
            b
            for b in variation.get("StandardPricing") or []
            if b["BreakQuantity"] <= quantity
        ]
        if breaks:
            price = max(breaks, key=lambda b: b["BreakQuantity"])["UnitPrice"]
            best = price if best is None else min(best, price)
    if best is None and product.get("UnitPrice") and quantity == 1:
        best = product["UnitPrice"]
    return best


def flatten_product(product, quantity=100):
    """One flat dict per product, with every parametric value as its own column."""
    row = {
        "manufacturer": (product.get("Manufacturer") or {}).get("Name"),
        "mpn": product.get("ManufacturerProductNumber"),
        "description": (product.get("Description") or {}).get("ProductDescription"),
        "status": (product.get("ProductStatus") or {}).get("Status"),
        "stock": product.get("QuantityAvailable"),
        f"unit_price_at_{quantity}": price_at(product, quantity),
        "datasheet": product.get("DatasheetUrl"),
        "url": product.get("ProductUrl"),
    }
    for parameter in product.get("Parameters") or []:
        name = parameter.get("ParameterText")
        if name:
            row[name] = parameter.get("ValueText")
    return row
