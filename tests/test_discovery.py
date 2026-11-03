"""Tests du module de découverte (`bot/discovery.py`)."""

from __future__ import annotations

import httpx

from bot.bestbuy import BestBuyClient
from bot.config import Settings
from bot.discovery import discover
from bot.state import StateStore

SETTINGS = Settings(_env_file="none.env")  # budget 2000, M4→M6, ≥16 Go / ≥512 Go


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


OK_SPECS = "(Apple M4 / 16GB RAM / 512GB SSD)"


def _with(**overrides) -> Settings:
    settings = Settings(_env_file="none.env")
    for key, value in overrides.items():
        object.__setattr__(settings, key, value)
    return settings


def test_finds_open_box_under_max_price(tmp_path) -> None:
    products = [
        _p("A1", f"(Boîte ouverte - Excellent) MacBook Pro 14 {OK_SPECS}", 2200, 1899),
        _p("A2", "MacBook Pro 16 (Apple M4 Pro / 24GB RAM / 1TB SSD)", 3500, 3200),  # trop cher
        _p("A3", "Housse pour MacBook Pro", 60, 40, category="20356"),             # accessoire
    ]
    store = StateStore(tmp_path / "s.json")

    alerts = discover(_search_client(products), SETTINGS, store, known_skus=[], now=None)

    assert [a.sku for a in alerts] == ["A1"]
    assert alerts[0].kind.value == "discovery"
    assert alerts[0].condition == "Boîte ouverte – Excellent"
    assert alerts[0].specs == "M4 · 16 Go · 512 Go"
    assert alerts[0].level.value == "red"           # sous le budget


def test_skips_watchlist_skus(tmp_path) -> None:
    products = [_p("A1", f"(Boîte ouverte) MacBook Pro 14 {OK_SPECS}", 2200, 1899)]
    store = StateStore(tmp_path / "s.json")
    assert discover(_search_client(products), SETTINGS, store, known_skus=["A1"]) == []


def test_new_full_price_under_budget_is_found(tmp_path) -> None:
    # Neuf, sans rabais, mais sous le budget et dans les specs -> signalé
    products = [_p("N1", f"MacBook Pro 14 {OK_SPECS}", 1999, 1999)]
    store = StateStore(tmp_path / "s.json")
    assert [a.sku for a in discover(_search_client(products), SETTINGS, store, [])] == ["N1"]


def test_above_budget_needs_discount(tmp_path) -> None:
    settings = _with(discovery_max_price=2500)
    products = [
        _p("H1", f"MacBook Pro 14 {OK_SPECS}", 2299, 2299),   # au-dessus, sans promo
        _p("H2", f"MacBook Pro 14 {OK_SPECS}", 2899, 2399),   # au-dessus, −17 %
    ]
    store = StateStore(tmp_path / "s.json")
    alerts = discover(_search_client(products), settings, store, [])
    assert [a.sku for a in alerts] == ["H2"]
    assert alerts[0].level.value == "yellow"


def test_new_item_with_big_discount_is_found(tmp_path) -> None:
    products = [_p("N2", f"MacBook Pro 14 {OK_SPECS}", 1999, 1599)]  # -20 %
    store = StateStore(tmp_path / "s.json")
    alerts = discover(_search_client(products), SETTINGS, store, known_skus=[])
    assert [a.sku for a in alerts] == ["N2"]
    assert alerts[0].discount_pct == 20.0


def test_no_respam_same_price(tmp_path) -> None:
    products = [_p("A1", f"(Boîte ouverte) MacBook Pro 14 {OK_SPECS}", 2200, 1899)]
    store = StateStore(tmp_path / "s.json")
    assert len(discover(_search_client(products), SETTINGS, store, [])) == 1
    assert discover(_search_client(products), SETTINGS, store, []) == []


def test_refurbished_included_by_default(tmp_path) -> None:
    # Le cas réel de la capture Best Buy : M4 remis à neuf (Fair) à 1 699,99 $
    name = ('Refurbished (Fair) - Apple MacBook Pro 14.2" (Fall 2024) - Space Black '
            "(Apple M4 / 16GB RAM / 512GB SSD) - English")
    products = [_p("R1", name, 2099.99, 1699.99)]
    store = StateStore(tmp_path / "s.json")
    alerts = discover(_search_client(products), SETTINGS, store, [])
    assert [a.sku for a in alerts] == ["R1"]
    assert alerts[0].condition == "Remis à neuf – Passable"


def test_refurbished_can_be_disabled(tmp_path) -> None:
    settings = _with(discovery_include_refurbished=False)
    products = [_p("R1", f"(Remis à neuf) MacBook Pro 14 {OK_SPECS}", 3000, 1499)]
    store = StateStore(tmp_path / "s.json")
    assert discover(_search_client(products), settings, store, []) == []


def test_m5_french_label_is_found(tmp_path) -> None:
    name = ("Remis à neuf (Très bon état) - MacBook Pro 14 po d'Apple (automne 2025) - "
            "Noir infini (M5 d'Apple/RAM 16 Go/SSD 1 To) - Anglais")
    products = [_p("M5", name, 2799, 1950)]
    store = StateStore(tmp_path / "s.json")
    alerts = discover(_search_client(products), SETTINGS, store, [])
    assert [a.sku for a in alerts] == ["M5"]
    assert alerts[0].specs == "M5 · 16 Go · 1 To"


def test_old_chips_are_excluded(tmp_path) -> None:
    products = [
        _p("O1", "(Boîte ouverte) MacBook Pro 14 (Apple M3 / 18GB RAM / 512GB SSD)", 2200, 1500),
        _p("O2", "(Remis à neuf) MacBook Pro 16 M1 Max 32GB/1TB", 3000, 1499),
    ]
    store = StateStore(tmp_path / "s.json")
    assert discover(_search_client(products), SETTINGS, store, []) == []


def test_too_little_ram_or_storage_is_excluded(tmp_path) -> None:
    products = [
        _p("S1", "MacBook Pro 14 (Apple M4 / 16GB RAM / 256GB SSD)", 1999, 1499),
        _p("S2", "MacBook Pro 14 (Apple M4 / 8GB RAM / 512GB SSD)", 1999, 1499),
    ]
    store = StateStore(tmp_path / "s.json")
    assert discover(_search_client(products), SETTINGS, store, []) == []


def test_unknown_specs_flagged_not_dropped(tmp_path) -> None:
    products = [_p("U1", "(Boîte ouverte) MacBook Pro 14 po M4 noir sidéral", 2199, 1799)]
    store = StateStore(tmp_path / "s.json")
    alerts = discover(_search_client(products), SETTINGS, store, [])
    assert [a.sku for a in alerts] == ["U1"]
    assert alerts[0].specs_unverified is True

    strict = _with(allow_unknown_specs=False)
    assert discover(_search_client(products), strict, StateStore(tmp_path / "t.json"), []) == []


def test_price_error_is_critical(tmp_path) -> None:
    products = [_p("E1", f"MacBook Pro 14 {OK_SPECS}", 2899, 1199)]  # −58 %
    store = StateStore(tmp_path / "s.json")
    alerts = discover(_search_client(products), SETTINGS, store, [])
    assert alerts[0].level.value == "critical"


def test_scans_all_pages(tmp_path) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        calls.append(page)
        product = _p(f"P{page}", f"MacBook Pro 14 {OK_SPECS}", 1999, 1900 - page)
        return httpx.Response(200, json={"currentPage": page, "totalPages": 8, "products": [product]})

    client = BestBuyClient(client=httpx.Client(
        base_url="https://www.bestbuy.ca", transport=httpx.MockTransport(handler)))
    alerts = discover(client, SETTINGS, StateStore(tmp_path / "s.json"), [])
    assert calls == list(range(1, 9))
    assert [a.sku for a in alerts][:2] == ["P8", "P7"]     # triées du moins cher au plus cher


def test_intel_macbook_is_excluded(tmp_path) -> None:
    products = [_p("I1", "(Boîte ouverte) MacBook Pro 15 Core i7 16GB/512GB (2019)", 3000, 1500)]
    store = StateStore(tmp_path / "s.json")
    assert discover(_search_client(products), SETTINGS, store, []) == []


def test_price_below_floor_is_excluded(tmp_path) -> None:
    products = [_p("C1", f"(Boîte ouverte) MacBook Pro 14 {OK_SPECS}", 1500, 799)]  # < 900
    store = StateStore(tmp_path / "s.json")
    assert discover(_search_client(products), SETTINGS, store, []) == []


def test_disabled_discovery_returns_nothing(tmp_path) -> None:
    settings = _with(discovery_enabled=False)
    products = [_p("A1", f"(Boîte ouverte) MacBook Pro 14 {OK_SPECS}", 2200, 1899)]
    store = StateStore(tmp_path / "s.json")
    assert discover(_search_client(products), settings, store, []) == []
