"""Offline tests for the DigiKey reply helpers. No network or credentials needed."""

import pytest
from digikey_search import DigiKeyClient, DigiKeyError, flatten_product, price_at

PRODUCT = {
    "ManufacturerProductNumber": "LTPL-C034UVH365",
    "Manufacturer": {"Name": "Lite-On Inc."},
    "Description": {"ProductDescription": "UV emitter 365nm"},
    "QuantityAvailable": 7414,
    "ProductStatus": {"Status": "Active"},
    "Parameters": [{"ParameterText": "Wavelength", "ValueText": "365nm"}],
    "ProductVariations": [
        {
            "MinimumOrderQuantity": 1,
            "StandardPricing": [
                {"BreakQuantity": 1, "UnitPrice": 10.57},
                {"BreakQuantity": 10, "UnitPrice": 7.96},
                {"BreakQuantity": 100, "UnitPrice": 6.43},
            ],
        },
        {
            "MinimumOrderQuantity": 500,
            "StandardPricing": [{"BreakQuantity": 500, "UnitPrice": 5.76}],
        },
    ],
}


@pytest.mark.parametrize(
    ("qty", "price"),
    [(1, 10.57), (9, 10.57), (10, 7.96), (100, 6.43), (499, 6.43), (500, 5.76)],
)
def test_price_at_picks_highest_break_and_cheapest_variation(qty, price):
    assert price_at(PRODUCT, qty) == price


def test_price_at_none_when_no_variation_sells_that_quantity():
    product = {
        "ProductVariations": [{"MinimumOrderQuantity": 500, "StandardPricing": []}]
    }
    assert price_at(product, 10) is None


def test_flatten_product_spreads_parameters_into_columns():
    row = flatten_product(PRODUCT, 100)
    assert row["mpn"] == "LTPL-C034UVH365"
    assert row["unit_price_at_100"] == 6.43
    assert row["Wavelength"] == "365nm"


def test_client_requires_credentials(monkeypatch):
    monkeypatch.delenv("DIGIKEY_CLIENT_ID", raising=False)
    monkeypatch.delenv("DIGIKEY_CLIENT_SECRET", raising=False)
    with pytest.raises(DigiKeyError):
        DigiKeyClient()
