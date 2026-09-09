"""Tests du module de découverte (`bot/discovery.py`)."""

from __future__ import annotations

import httpx

from bot.bestbuy import BestBuyClient
from bot.config import Settings
from bot.discovery import discover
from bot.state import StateStore

SETTINGS = Settings(_env_file="none.env")  # discovery_max_price=2000, min_pct=15


def _search_client(products: list[dict], total_pages: int = 1) -> BestBuyClient:
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        body = {
            "currentPage": page,
            "totalPages": total_pages,
            "products": products if page == 1 else [],
        }
        return httpx.Response(200, json=body)

    mock = httpx.Client(
        base_url="https://www.bestbuy.ca", transport=httpx.MockTransport(handler)
    )
    return BestBuyClient(client=mock)


MBP_CATEGORY = "12746019"


def _p(sku, name, regular, sale, category=MBP_CATEGORY) -> dict:
    return {
        "sku": sku,
        "name": name,
        "regularPrice": regular,
        "salePrice": sale,
        "productUrl": f"/fr-CA/produit/x/{sku}",
        "primaryParentCategoryId": category,
    }


def test_finds_open_box_under_max_price(tmp_path) -> None:
    products = [
        _p("A1", "(Boîte ouverte) MacBook Pro 14 M3", 2200, 1899),          # open box, sous 2000
        _p("A2", "MacBook Pro 16 M4", 3500, 3200),                          # trop cher
        _p("A3", "Housse pour MacBook Pro", 60, 40, category="20356"),      # accessoire -> autre catégorie
    ]
    client = _search_client(products)
    store = StateStore(tmp_path / "s.json")

    alerts = discover(client, SETTINGS, store, known_skus=[], now=None)

    assert [a.sku for a in alerts] == ["A1"]
    assert alerts[0].kind.value == "discovery"


def test_skips_watchlist_skus(tmp_path) -> None:
    products = [_p("A1", "(Boîte ouverte) MacBook Pro 14 M3", 2200, 1899)]
    client = _search_client(products)
    store = StateStore(tmp_path / "s.json")

    alerts = discover(client, SETTINGS, store, known_skus=["A1"], now=None)
    assert alerts == []


def test_new_item_without_real_discount_is_ignored(tmp_path) -> None:
    # Neuf, plein tarif sous 2000 mais aucune baisse -> pas une pépite
    products = [_p("N1", "MacBook Pro 13 M2", 1699, 1699)]
    client = _search_client(products)
    store = StateStore(tmp_path / "s.json")

    assert discover(client, SETTINGS, store, known_skus=[], now=None) == []


def test_new_item_with_big_discount_is_found(tmp_path) -> None:
    products = [_p("N2", "MacBook Pro 14 M3", 1999, 1599)]  # -20 %
    client = _search_client(products)
    store = StateStore(tmp_path / "s.json")

    alerts = discover(client, SETTINGS, store, known_skus=[], now=None)
    assert [a.sku for a in alerts] == ["N2"]
    assert alerts[0].discount_pct == 20.0


def test_no_respam_same_price(tmp_path) -> None:
    products = [_p("A1", "(Boîte ouverte) MacBook Pro 14 M3", 2200, 1899)]
    store = StateStore(tmp_path / "s.json")

    first = discover(_search_client(products), SETTINGS, store, [], now=None)
    assert len(first) == 1
    second = discover(_search_client(products), SETTINGS, store, [], now=None)
    assert second == []


def test_refurbished_is_excluded_by_default(tmp_path) -> None:
    products = [_p("R1", "(Remis à neuf) MacBook Pro 16 M1 Max", 3000, 1499)]
    store = StateStore(tmp_path / "s.json")
    assert discover(_search_client(products), SETTINGS, store, [], now=None) == []


def test_refurbished_included_when_opted_in(tmp_path) -> None:
    settings = Settings(_env_file="none.env")
    object.__setattr__(settings, "discovery_include_refurbished", True)
    products = [_p("R1", "(Remis à neuf) MacBook Pro 16 M1 Max", 3000, 1499)]
    store = StateStore(tmp_path / "s.json")
    alerts = discover(_search_client(products), settings, store, [], now=None)
    assert [a.sku for a in alerts] == ["R1"]


def test_intel_macbook_is_excluded(tmp_path) -> None:
    products = [_p("I1", "(Boîte ouverte) MacBook Pro 15 Core i7 (2019)", 3000, 1500)]
    store = StateStore(tmp_path / "s.json")
    assert discover(_search_client(products), SETTINGS, store, [], now=None) == []


def test_price_below_floor_is_excluded(tmp_path) -> None:
    products = [_p("C1", "(Boîte ouverte) MacBook Pro 13 M1", 1500, 999)]  # < 1200
    store = StateStore(tmp_path / "s.json")
    assert discover(_search_client(products), SETTINGS, store, [], now=None) == []


def test_disabled_discovery_returns_nothing(tmp_path) -> None:
    settings = Settings(_env_file="none.env")
    object.__setattr__(settings, "discovery_enabled", False)
    products = [_p("A1", "(Boîte ouverte) MacBook Pro 14 M3", 2200, 1899)]
    store = StateStore(tmp_path / "s.json")

    assert discover(_search_client(products), settings, store, [], now=None) == []
