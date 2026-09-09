"""Outillage partagé des tests : un faux transport HTTP qui répond avec les
fixtures JSON capturées depuis le vrai site (dossier `tests/fixtures/`).

Aucun test ne touche le réseau.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path

    if path == "/api/v1/catalog/query":
        ids = request.url.params.get("ids", "").split(",")
        full = load_fixture("catalog_query.json")
        items = [it for it in full["items"] if it["sku"] in ids]
        return httpx.Response(200, json={**full, "items": items, "total": len(items)})

    if path.startswith("/api/v2/json/product/") and path.endswith("/conditions"):
        return httpx.Response(200, json=load_fixture("conditions_20009307.json"))

    if path.startswith("/api/v2/json/product/"):
        return httpx.Response(200, json=load_fixture("product_20009307.json"))

    if path == "/api/v2/json/search":
        return httpx.Response(200, json=load_fixture("search_macbook.json"))

    return httpx.Response(404, json={"error": f"pas de fixture pour {path}"})


@pytest.fixture
def mock_http() -> httpx.Client:
    """Client httpx dont toutes les requêtes sont servies par les fixtures."""
    return httpx.Client(
        base_url="https://www.bestbuy.ca",
        transport=httpx.MockTransport(_handler),
    )
