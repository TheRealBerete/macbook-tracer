"""Mise en forme des alertes pour Telegram (HTML).

Telegram accepte un sous-ensemble de HTML : <b>, <i>, <a href>, <code>, <s>.
On utilise `parse_mode=HTML` (plus simple que MarkdownV2 qui exige d'échapper
une longue liste de caractères).
"""

from __future__ import annotations

import html

from bot.alerts import Alert, AlertKind, AlertLevel

_LEVEL_HEADER = {
    AlertLevel.GREEN: "🟢 <b>BAISSE DE PRIX</b>",
    AlertLevel.YELLOW: "🟡 <b>BAISSE IMPORTANTE</b>",
    AlertLevel.RED: "🔴 <b>ALERTE PRIX</b>",
}


def _esc(text: str) -> str:
    """Échappe le texte pour l'insérer dans du HTML Telegram."""
    return html.escape(text, quote=False)


def _money(value: float) -> str:
    # 2 471,31 $  (séparateur de milliers = espace fine, virgule décimale FR)
    return f"{value:,.2f}".replace(",", " ").replace(".", ",") + " $"


def format_alert(alert: Alert) -> str:
    if alert.kind is AlertKind.FAILURE:
        return _format_failure(alert)
    if alert.kind is AlertKind.BACK_IN_STOCK:
        return _format_back_in_stock(alert)
    return _format_price(alert)


def _format_price(alert: Alert) -> str:
    header = _LEVEL_HEADER.get(alert.level or AlertLevel.RED, "🔔 <b>ALERTE</b>")
    if alert.kind is AlertKind.DISCOVERY:
        header = "💎 <b>PÉPITE DÉTECTÉE</b>"

    lines = [header, "", f"📦 {_esc(alert.name)}"]

    if alert.price is not None:
        price_line = f"💰 <b>{_money(alert.price)}</b> HT"
        if alert.tax_rate and alert.price_with_tax is not None:
            price_line += f"  <i>(≈ {_money(alert.price_with_tax)} TTC)</i>"
        lines.append(price_line)

    if alert.regular_price and alert.discount_pct:
        lines.append(
            f"📉 −{_money(alert.discount_amount)} "
            f"(−{alert.discount_pct:g} % vs {_money(alert.regular_price)})"
        )

    if alert.lowest_ever is not None and alert.price is not None:
        tag = "  ← nouveau plancher" if alert.price <= alert.lowest_ever else ""
        lines.append(f"📊 Plus bas vu par le bot : {_money(alert.lowest_ever)}{tag}")

    if alert.condition:
        lines.append(f"🏷️ État : {_esc(alert.condition)}")
    if alert.seller:
        lines.append(f"🏪 Vendeur : {_esc(alert.seller)}")
    if alert.stock:
        lines.append(f"📦 Stock : {_esc(alert.stock)}")

    if alert.url:
        lines.append("")
        lines.append(f'🔗 <a href="{_esc(alert.url)}">Voir sur Best Buy</a>')

    return "\n".join(lines)


def _format_failure(alert: Alert) -> str:
    return (
        "⚠️ <b>BOT EN ERREUR</b>\n\n"
        f"Le suivi de <b>{_esc(alert.name)}</b> "
        f"(<code>{_esc(alert.sku or '?')}</code>) échoue.\n"
        f"{_esc(alert.reason)}\n\n"
        "→ Vérifier si l'API Best Buy a changé."
    )


def _format_back_in_stock(alert: Alert) -> str:
    lines = ["🔵 <b>DE RETOUR EN STOCK</b>", "", f"📦 {_esc(alert.name)}"]
    if alert.price is not None:
        lines.append(f"💰 {_money(alert.price)} HT")
    if alert.url:
        lines.append("")
        lines.append(f'🔗 <a href="{_esc(alert.url)}">Voir sur Best Buy</a>')
    return "\n".join(lines)
