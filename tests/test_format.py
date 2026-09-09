"""Tests de la mise en forme Telegram (`bot/format.py`)."""

from __future__ import annotations

from bot.alerts import Alert, AlertKind, AlertLevel
from bot.format import format_alert


def test_price_alert_contains_key_fields() -> None:
    alert = Alert(
        kind=AlertKind.PRICE_DROP,
        name="MacBook Pro 14 M4",
        reason="test",
        sku="111111",
        price=1950.0,
        regular_price=2500.0,
        discount_amount=550.0,
        discount_pct=22.0,
        lowest_ever=1950.0,
        level=AlertLevel.RED,
        seller="Best Buy",
        condition="Neuf",
        url="https://www.bestbuy.ca/x",
        tax_rate=0.13,
    )
    msg = format_alert(alert)

    assert "🔴" in msg
    assert "MacBook Pro 14 M4" in msg
    assert "1 950,00 $" in msg          # format FR
    assert "2 203,50 $ TTC" in msg      # 1950 * 1.13
    assert "−22 %" in msg
    assert "nouveau plancher" in msg
    assert '<a href="https://www.bestbuy.ca/x">' in msg


def test_open_box_alert_hides_percent_block() -> None:
    alert = Alert(
        kind=AlertKind.PRICE_DROP,
        name="(Boîte ouverte) MacBook Pro",
        reason="nouveau plus bas",
        price=2100.0,
        regular_price=None,       # pas de vrai prix barré
        discount_pct=0.0,
        lowest_ever=2100.0,
        level=AlertLevel.YELLOW,
        condition="Boîte ouverte",
    )
    msg = format_alert(alert)
    assert "vs" not in msg          # pas de ligne "−x % vs ..."
    assert "Boîte ouverte" in msg


def test_failure_alert_format() -> None:
    alert = Alert(
        kind=AlertKind.FAILURE,
        name="MBP 14",
        sku="111111",
        reason="3 échecs consécutifs — HTTP 403",
    )
    msg = format_alert(alert)
    assert "BOT EN ERREUR" in msg
    assert "<code>111111</code>" in msg


def test_html_is_escaped_in_name() -> None:
    alert = Alert(
        kind=AlertKind.PRICE_DROP, name="Mac & <b>hack</b>", reason="x", price=10.0,
        level=AlertLevel.GREEN,
    )
    msg = format_alert(alert)
    assert "&amp;" in msg
    assert "&lt;b&gt;hack" in msg     # les balises injectées sont neutralisées
