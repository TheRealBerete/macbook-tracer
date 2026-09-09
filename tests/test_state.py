"""Tests de l'état persistant (`bot/state.py`)."""

from __future__ import annotations

import json

from bot.state import ProductState, StateStore


def test_record_price_tracks_lowest_ever() -> None:
    state = ProductState()
    state.record_price(2000, 2500)
    assert state.lowest_ever == 2000
    state.record_price(2100, 2500)
    assert state.lowest_ever == 2000          # ne remonte pas
    state.record_price(1900, 2500)
    assert state.lowest_ever == 1900          # nouveau plancher


def test_rearm_clears_last_alert() -> None:
    state = ProductState(last_alerted_price=1999)
    state.rearm()
    assert state.last_alerted_price is None
    assert state.last_alerted_at is None


def test_store_roundtrip_atomic(tmp_path) -> None:
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.get("20009307").record_price(2187, 2187)
    store.save()

    assert path.exists()
    reloaded = StateStore(path)
    assert reloaded.get("20009307").current_price == 2187


def test_store_recovers_from_corrupt_file(tmp_path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{ ceci n'est pas du JSON", encoding="utf-8")

    store = StateStore(path)                  # ne doit pas lever
    assert store.all() == {}
    assert path.with_suffix(".json.bak").exists()


def test_save_excludes_none_fields(tmp_path) -> None:
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.get("x").consecutive_failures = 2
    store.save()

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["x"]["consecutive_failures"] == 2
    assert "current_price" not in data["x"]           # les None ne sont pas écrits
