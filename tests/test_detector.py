"""Tests de la logique de détection + anti-spam (`bot/detector.py`)."""

from __future__ import annotations

from datetime import timedelta

from bot.alerts import AlertKind, AlertLevel
from bot.config import Settings, Target
from bot.detector import evaluate_target, register_failure, register_success
from bot.models import Product
from bot.state import ProductState, utcnow

SETTINGS = Settings(_env_file="none.env")  # valeurs par défaut


def make_product(
    *, sku="111111", name="MacBook Pro 14 M4", regular=2500.0, sale=2500.0
) -> Product:
    return Product.model_validate(
        {
            "sku": sku,
            "name": name,
            "regularPrice": regular,
            "salePrice": sale,
            "productUrl": f"/fr-CA/produit/x/{sku}",
            "sellerId": "bbyca",
        }
    )


def target(alert_price=2000.0, min_pct=None) -> Target:
    return Target(
        name="MBP14", web_code="111111", alert_price=alert_price, min_discount_pct=min_pct
    )


# --- déclenchement --------------------------------------------------------------


def test_no_alert_when_price_above_seuil_and_small_discount() -> None:
    state = ProductState()
    alert = evaluate_target(target(), make_product(regular=2500, sale=2400), state, SETTINGS)
    assert alert is None
    assert state.current_price == 2400          # état quand même mis à jour
    assert state.lowest_ever == 2400


def test_alert_when_under_budget() -> None:
    state = ProductState()
    alert = evaluate_target(target(alert_price=2000), make_product(sale=1950), state, SETTINGS)
    assert alert is not None
    assert alert.kind is AlertKind.PRICE_DROP
    assert alert.level is AlertLevel.RED       # sous le budget -> rouge


def test_alert_on_percent_drop_even_above_budget() -> None:
    state = ProductState()
    # 2500 -> 2000 = -20 %, au-dessus du seuil 1800 mais grosse promo
    alert = evaluate_target(
        target(alert_price=1800), make_product(regular=2500, sale=2000), state, SETTINGS
    )
    assert alert is not None
    assert alert.discount_pct == 20.0
    assert alert.level is AlertLevel.YELLOW


def test_openbox_alert_on_new_low_only() -> None:
    tgt = target(alert_price=1500)             # jamais sous le budget ici
    product = make_product(name="(Boîte ouverte - Très bon état) MacBook Pro 14", regular=2187, sale=2187)

    state = ProductState(lowest_ever=2200)
    alert = evaluate_target(tgt, product, state, SETTINGS)
    assert alert is not None                   # 2187 < 2200 -> nouveau plancher
    assert alert.condition == "Boîte ouverte"

    # deuxième passage même prix : plus de nouveau plancher -> pas d'alerte
    state2 = ProductState(lowest_ever=2187)
    assert evaluate_target(tgt, product, state2, SETTINGS) is None


# --- anti-spam ----------------------------------------------------------------


def test_no_respam_at_same_price() -> None:
    tgt = target(alert_price=2000)
    product = make_product(sale=1950)
    state = ProductState()

    first = evaluate_target(tgt, product, state, SETTINGS)
    assert first is not None
    second = evaluate_target(tgt, product, state, SETTINGS)
    assert second is None                       # même prix -> silence


def test_respam_when_price_drops_further() -> None:
    tgt = target(alert_price=2000)
    state = ProductState()

    evaluate_target(tgt, make_product(sale=1950), state, SETTINGS)
    # -60 $ > realert_delta (50) -> nouvelle alerte
    again = evaluate_target(tgt, make_product(sale=1890), state, SETTINGS)
    assert again is not None


def test_daily_reminder_after_24h() -> None:
    tgt = target(alert_price=2000)
    product = make_product(sale=1950)
    state = ProductState()

    now = utcnow()
    evaluate_target(tgt, product, state, SETTINGS, now=now)
    # 25 h plus tard, prix toujours sous le seuil
    later = now + timedelta(hours=25)
    reminder = evaluate_target(tgt, product, state, SETTINGS, now=later)
    assert reminder is not None
    assert "rappel 24h" in reminder.reason


def test_rearm_after_price_recovers() -> None:
    tgt = target(alert_price=2000)
    state = ProductState()

    evaluate_target(tgt, make_product(sale=1950), state, SETTINGS)
    assert state.last_alerted_price == 1950

    # le prix remonte au-dessus du seuil, pas de promo -> ré-armement
    evaluate_target(tgt, make_product(regular=2500, sale=2500), state, SETTINGS)
    assert state.last_alerted_price is None

    # nouvelle baisse -> ré-alerte
    assert evaluate_target(tgt, make_product(sale=1980), state, SETTINGS) is not None


# --- pannes (F2c) -----------------------------------------------------------------


def test_failure_alert_only_at_threshold() -> None:
    state = ProductState()
    tgt = target()

    assert register_failure(state, tgt, SETTINGS, "boom") is None   # 1
    assert register_failure(state, tgt, SETTINGS, "boom") is None   # 2
    alert = register_failure(state, tgt, SETTINGS, "boom")          # 3 == seuil
    assert alert is not None
    assert alert.kind is AlertKind.FAILURE

    # pas de spam de panne
    assert register_failure(state, tgt, SETTINGS, "boom") is None   # 4


def test_success_resets_failures() -> None:
    state = ProductState(consecutive_failures=5, failure_alerted=True)
    register_success(state)
    assert state.consecutive_failures == 0
    assert state.failure_alerted is False
