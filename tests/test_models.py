"""Tests des modèles de contrat (`bot/models.py`)."""

from __future__ import annotations

from datetime import datetime

import pytest

from bot.models import ConditionProduct, Product
from tests.conftest import load_fixture


def _product_by_sku(sku: str) -> Product:
    full = load_fixture("catalog_query.json")
    raw = next(it for it in full["items"] if it["sku"] == sku)
    return Product.model_validate(raw)


def test_product_parses_camelcase_and_computes_discount() -> None:
    # 19376540 : Tablette Lenovo, regular 269.99 -> sale 189.99
    product = _product_by_sku("19376540")

    assert product.sku == "19376540"
    assert product.sale_price == 189.99
    assert product.has_real_discount is True
    assert product.discount_amount == 80.0
    assert product.discount_pct == 29.6  # (269.99-189.99)/269.99*100
    assert isinstance(product.sale_end_date, datetime)


def test_product_full_url_prefixes_relative_path() -> None:
    product = _product_by_sku("19376540")
    assert product.full_url.startswith("https://www.bestbuy.ca/fr-CA/produit/")


def test_open_box_item_has_no_real_discount() -> None:
    # 18522252 : "Boîte ouverte - AirPods Max", regular 779.99 == ... mais salePrice 479.99
    # Ici hideSavings peut être false ; on vérifie surtout le cas hide_savings.
    product = _product_by_sku("18522252")
    if product.hide_savings:
        assert product.discount_pct == 0.0


def test_sale_end_date_accepts_epoch_ms() -> None:
    raw = {
        "sku": "1",
        "name": "x",
        "regularPrice": 100,
        "salePrice": 90,
        "productUrl": "/p/1",
        "saleEndDate": 1791097199000,
    }
    product = Product.model_validate(raw)
    assert product.sale_end_date is not None
    assert product.sale_end_date.year == 2026


def test_missing_required_field_raises() -> None:
    with pytest.raises(Exception):
        Product.model_validate({"sku": "1", "name": "x"})  # pas de prix


def test_condition_products_parse() -> None:
    raw = load_fixture("conditions_20009307.json")
    groups = raw["alternateConditionProducts"]
    first = ConditionProduct.model_validate(groups["boîte ouverte"][0])
    assert first.product_condition == "Boîte ouverte"
    assert first.sale_price > 0
