"""Tests du client API (`bot/bestbuy.py`), sans réseau."""

from __future__ import annotations

import httpx
import pytest

from bot.bestbuy import BestBuyApiError, BestBuyClient


def test_get_prices_returns_products(mock_http: httpx.Client) -> None:
    client = BestBuyClient(client=mock_http)
    products = client.get_prices(["19376540", "16653776"])

    skus = {p.sku for p in products}
    assert skus == {"19376540", "16653776"}


def test_get_prices_chunks_over_20_skus() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        ids = request.url.params.get("ids", "").split(",")
        calls.append(len(ids))
        items = [
            {"sku": s, "name": f"p{s}", "regularPrice": 10, "salePrice": 9, "productUrl": f"/p/{s}"}
            for s in ids
        ]
        return httpx.Response(200, json={"items": items})

    mock = httpx.Client(base_url="https://www.bestbuy.ca", transport=httpx.MockTransport(handler))
    client = BestBuyClient(client=mock)

    products = client.get_prices([str(i) for i in range(45)])

    assert len(products) == 45
    assert calls == [20, 20, 5]  # découpage en lots de 20


def test_get_product_includes_availability(mock_http: httpx.Client) -> None:
    client = BestBuyClient(client=mock_http)
    product = client.get_product("20009307")

    assert product.availability is not None
    assert product.availability.online_availability_count == 29
    assert product.availability.is_purchasable is True


def test_get_conditions_flattens_all_groups(mock_http: httpx.Client) -> None:
    client = BestBuyClient(client=mock_http)
    conditions = client.get_conditions("20009307")

    assert len(conditions) >= 8
    assert any(c.product_condition == "Boîte ouverte" for c in conditions)


def test_non_retryable_http_error_raises_immediately() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={})

    mock = httpx.Client(base_url="https://www.bestbuy.ca", transport=httpx.MockTransport(handler))
    client = BestBuyClient(client=mock)

    with pytest.raises(BestBuyApiError, match="404"):
        client.get_product("does-not-exist")


def test_retryable_error_is_retried_then_gives_up() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(403, text="blocked")

    mock = httpx.Client(base_url="https://www.bestbuy.ca", transport=httpx.MockTransport(handler))
    client = BestBuyClient(client=mock, max_retries=3, backoff_base=1.0)

    # backoff_base=1.0 -> sleep(1**n)=1s ; on patche sleep pour ne pas ralentir le test
    import bot.bestbuy as mod

    original_sleep = mod.time.sleep
    mod.time.sleep = lambda _s: None
    try:
        with pytest.raises(BestBuyApiError, match="après 3 tentatives"):
            client.get_product("x")
    finally:
        mod.time.sleep = original_sleep

    assert len(attempts) == 3


def test_contract_violation_raises_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # 'items' présent mais un item sans prix -> ValidationError -> BestBuyApiError
        return httpx.Response(200, json={"items": [{"sku": "1", "name": "x"}]})

    mock = httpx.Client(base_url="https://www.bestbuy.ca", transport=httpx.MockTransport(handler))
    client = BestBuyClient(client=mock)

    with pytest.raises(BestBuyApiError, match="contrat"):
        client.get_prices(["1"])
