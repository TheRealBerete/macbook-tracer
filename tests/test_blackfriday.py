"""Tests des améliorations Black Friday : specs, mode rapide, variantes, stock,
erreur de prix, regroupement des pépites, historique."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

import bot.scheduler as scheduler
from bot.alerts import Alert, AlertKind, AlertLevel
from bot.bestbuy import BestBuyClient
from bot.config import Settings, Target
from bot.criteria import check_specs
from bot.detector import evaluate_stock, evaluate_target
from bot.format import format_alert
from bot.models import Product, chip_from_text, grade_from_text, specs_from_text
from bot.notify import build_messages
from bot.runner import run_once
from bot.state import ProductState, StateStore

SETTINGS = Settings(_env_file="none.env")


def _settings(**overrides) -> Settings:
    settings = Settings(_env_file="none.env")
    for key, value in overrides.items():
        object.__setattr__(settings, key, value)
    return settings


# --- lecture des specs dans les libellés ----------------------------------------


@pytest.mark.parametrize(
    ("name", "ram", "storage"),
    [
        ('Apple MacBook Pro 14.2" (Fall 2024) - Space Black (Apple M4 / 16GB RAM / 512GB SSD)', 16, 512),
        ("MacBook Pro 14 po (M5 d'Apple/RAM 16 Go/SSD 1 To) - Anglais", 16, 1024),
        ("MacBook Pro 14 M4 24Go/1To", 24, 1024),
        ("MacBook Pro 16 po M4 Pro, 48 Go de RAM unifiée, SSD de 2 To", 48, 2048),
        ("MacBook Pro de 14 po (14 Go, noir infini) Portable M3", None, None),
    ],
)
def test_specs_from_text(name, ram, storage) -> None:
    assert specs_from_text(name) == (ram, storage)


@pytest.mark.parametrize(
    ("name", "chip"),
    [
        ("(Apple M4 / 16GB RAM / 512GB SSD)", "M4"),
        ("MacBook Pro 16 M4 Pro 24Go/512Go", "M4 Pro"),
        ("Noir infini (M5 d'Apple/RAM 16 Go/SSD 1 To)", "M5"),
        ("MacBook Pro M6 Max 64GB", "M6 Max"),
        ("MacBook Pro 15 Core i7", None),
    ],
)
def test_chip_from_text(name, chip) -> None:
    assert chip_from_text(name) == chip


def test_grades() -> None:
    assert grade_from_text("Refurbished (Fair) - Apple MacBook") == "Passable"
    assert grade_from_text("(Boîte ouverte - Très bon état) MacBook") == "Très bon état"
    assert grade_from_text("(Open Box - Excellent) Macbook") == "Excellent"
    assert grade_from_text("MacBook Pro neuf") is None


def test_criteria_m4_to_m6_only() -> None:
    assert check_specs("MacBook Pro (Apple M4 / 16GB RAM / 512GB SSD)", SETTINGS).ok
    assert check_specs("MacBook Pro M6 Pro 24GB RAM 1TB SSD", SETTINGS).ok
    assert not check_specs("MacBook Pro (Apple M3 Pro / 18GB RAM / 512GB SSD)", SETTINGS).ok
    assert not check_specs("MacBook Pro M7 32GB RAM 1TB SSD", SETTINGS).ok


# --- mode Black Friday -------------------------------------------------------------


def _toronto(month: int, day: int, hour: int = 12) -> datetime:
    # 17h UTC = midi à Toronto (EST) en novembre
    return datetime(2026, month, day, hour + 5, tzinfo=timezone.utc)


def test_rush_window_bounds() -> None:
    assert not SETTINGS.in_rush(_toronto(11, 19))
    assert SETTINGS.in_rush(_toronto(11, 20))
    assert SETTINGS.in_rush(_toronto(11, 27))     # Black Friday
    assert SETTINGS.in_rush(_toronto(12, 2))
    assert not SETTINGS.in_rush(_toronto(12, 3))
    assert SETTINGS.effective_interval(_toronto(11, 27)) == 10
    assert SETTINGS.effective_interval(_toronto(11, 3)) == 60


def test_rush_disabled() -> None:
    settings = _settings(rush_enabled=False)
    assert not settings.in_rush(_toronto(11, 27))
    assert scheduler.tick_interval(settings) == 60


def test_tick_respects_interval(monkeypatch) -> None:
    runs: list[int] = []
    monkeypatch.setattr(scheduler, "run_cycle", lambda s: runs.append(1))
    monkeypatch.setattr(scheduler, "_last_cycle_at", None)
    monkeypatch.setattr(scheduler, "_last_rush", None)
    clock = [1000.0]
    monkeypatch.setattr(scheduler.time, "monotonic", lambda: clock[0])

    def fake_run(settings):
        runs.append(1)
        scheduler._last_cycle_at = clock[0]

    monkeypatch.setattr(scheduler, "run_cycle", fake_run)

    normal_day = _toronto(11, 3)
    assert scheduler.tick(SETTINGS, normal_day)        # 1er tick : cycle
    clock[0] += 10 * 60
    assert not scheduler.tick(SETTINGS, normal_day)    # +10 min hors BF : rien
    clock[0] += 50 * 60
    assert scheduler.tick(SETTINGS, normal_day)        # +60 min : cycle

    bf = _toronto(11, 27)
    monkeypatch.setattr(scheduler, "_announce_rush", lambda s, r: None)
    clock[0] += 10 * 60
    assert scheduler.tick(SETTINGS, bf)                # BF : +10 min suffit
    assert len(runs) == 3


# --- watchlist : variantes, stock, erreur de prix -----------------------------------


def _product(sku="111", regular=2799.0, sale=2499.0, name="MacBook Pro 14 (Apple M4 / 16GB RAM / 512GB SSD)") -> Product:
    return Product.model_validate({
        "sku": sku, "name": name, "regularPrice": regular, "salePrice": sale,
        "productUrl": f"/fr-ca/produit/x/{sku}", "sellerId": "bbyca",
    })


def test_price_error_alert_is_critical() -> None:
    state = ProductState()
    tgt = Target(name="MBP", web_code="111", alert_price=1500)
    alert = evaluate_target(tgt, _product(regular=2799, sale=1299), state, SETTINGS)
    assert alert is not None and alert.level is AlertLevel.CRITICAL
    assert "ERREUR DE PRIX" in format_alert(alert)


def test_back_in_stock() -> None:
    state = ProductState(was_available=False)
    tgt = Target(name="MBP", web_code="111", alert_price=2000)
    alert = evaluate_stock(tgt, _product(sale=1999), state, True, SETTINGS)
    assert alert is not None and alert.kind is AlertKind.BACK_IN_STOCK
    # déjà dispo -> pas de nouvelle alerte
    assert evaluate_stock(tgt, _product(sale=1999), state, True, SETTINGS) is None
    # premier passage (état inconnu) -> pas d'alerte non plus
    assert evaluate_stock(tgt, _product(), ProductState(), True, SETTINGS) is None


def test_run_once_checks_variants_and_stock(mock_http, tmp_path) -> None:
    # 20009307 : la fixture conditions renvoie 7 boîtes ouvertes (2187 → 2599 $)
    # et 3 remis à neuf ; seuil 2200 -> seule la boîte ouverte à 2187 passe,
    # mais c'est le SKU surveillé lui-même : il est exclu des variantes.
    client = BestBuyClient(client=mock_http)
    store = StateStore(tmp_path / "state.json")
    targets = [Target(name="MBP 14 M3 Pro", web_code="20009307", alert_price=2350)]
    variants: set[str] = set()

    alerts = run_once(client, targets, store, SETTINGS, variant_skus=variants)

    variant_alerts = [a for a in alerts if a.sku != "20009307"]
    assert variant_alerts, "les variantes ≤ seuil doivent être signalées"
    assert all(a.price <= 2350 for a in variant_alerts)
    assert variants                                  # transmis à la découverte
    # 2e cycle : anti-spam, aucune nouvelle alerte de variante
    again = run_once(client, targets, store, SETTINGS, variant_skus=set())
    assert [a for a in again if a.sku != "20009307"] == []


def test_run_once_tracks_stock(mock_http, tmp_path) -> None:
    client = BestBuyClient(client=mock_http)
    store = StateStore(tmp_path / "state.json")
    targets = [Target(name="Tablette", web_code="19376540", alert_price=200, check_open_box=False)]
    store.get("19376540").was_available = False          # était en rupture
    alerts = run_once(client, targets, store, SETTINGS)
    assert store.get("19376540").was_available is True   # fixture product = AddToCart
    kinds = [a.kind for a in alerts]
    assert AlertKind.BACK_IN_STOCK in kinds and AlertKind.PRICE_DROP in kinds
    price_alert = next(a for a in alerts if a.kind is AlertKind.PRICE_DROP)
    assert price_alert.stock and "en stock" in price_alert.stock  # stock réutilisé, pas 2 appels

    # trop cher au retour (> seuil + 15 %) -> pas d'alerte de retour en stock
    store.get("19376540").was_available = False
    pricey = [Target(name="Tablette", web_code="19376540", alert_price=100, check_open_box=False)]
    assert AlertKind.BACK_IN_STOCK not in [a.kind for a in run_once(client, pricey, store, SETTINGS)]


# --- envoi : urgent d'abord, vague de pépites regroupée -----------------------------


def _discovery(sku: str, price: float, level=AlertLevel.RED) -> Alert:
    return Alert(kind=AlertKind.DISCOVERY, name=f"MBP {sku}", reason="x", sku=sku,
                 price=price, level=level, url=f"https://x/{sku}")


def test_build_messages_digest_and_priority() -> None:
    alerts = [_discovery(str(i), 1500 + i) for i in range(12)]
    alerts.append(_discovery("ERR", 999, level=AlertLevel.CRITICAL))
    messages = build_messages(alerts, SETTINGS)
    assert "ERREUR DE PRIX" in messages[0]           # l'urgence passe en premier
    # 1 critique + 5 détaillées + 1 récap pour les 7 autres
    assert len(messages) == 7
    assert "AUTRES PÉPITES" in messages[-1] and "(7)" in messages[-1]


def test_alert_shows_budget_and_specs() -> None:
    alert = Alert(kind=AlertKind.PRICE_DROP, name="MBP", reason="x", price=2199.77,
                  level=AlertLevel.YELLOW, budget=2000, specs="M5 · 16 Go · 1 To")
    text = format_alert(alert)
    assert "⚙️ M5 · 16 Go · 1 To" in text
    assert "au-dessus" in text


# --- historique ------------------------------------------------------------------


def test_history_records_changes_only(tmp_path) -> None:
    store = StateStore(tmp_path / "s.json")
    state = store.get("1")
    t0 = datetime(2026, 11, 27, tzinfo=timezone.utc)
    for i, price in enumerate([2199, 2199, 1999, 1999, 1899]):
        state.record_price(price, 2799, now=t0 + timedelta(minutes=10 * i))
    assert [p for _, p in state.history] == [2199, 1999, 1899]
    store.save()
    reloaded = StateStore(tmp_path / "s.json").get("1")
    assert [p for _, p in reloaded.history] == [2199, 1999, 1899]
