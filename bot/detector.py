"""Logique de détection : à partir d'un produit + son historique, décider s'il
faut émettre une alerte, et laquelle.

Deux garde-fous distincts :
1. **Déclenchement** (F2) : le prix mérite-t-il une alerte ?
2. **Anti-spam** (F2b) : ne l'a-t-on pas déjà signalée récemment ?
"""

from __future__ import annotations

from datetime import datetime, timedelta

from bot.alerts import Alert, AlertKind, level_for
from bot.config import Settings, Target
from bot.models import Product
from bot.state import ProductState, utcnow

REALERT_WINDOW = timedelta(hours=24)


def evaluate_target(
    target: Target,
    product: Product,
    state: ProductState,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> Alert | None:
    """Retourne une `Alert` si le produit doit être signalé maintenant, sinon `None`.

    Met à jour `state` dans tous les cas (prix vu, plus bas, ré-armement...).
    """
    now = now or utcnow()
    price = product.sale_price
    previous_lowest = state.lowest_ever
    state.record_price(price, product.regular_price, now=now)

    min_pct = target.effective_min_discount_pct(settings)
    under_budget = price <= target.alert_price

    reasons: list[str] = []
    if under_budget:
        reasons.append(f"prix {price:.0f}$ ≤ seuil {target.alert_price:.0f}$")
    if product.has_real_discount and product.discount_pct >= min_pct:
        reasons.append(f"baisse {product.discount_pct:g}% ≥ {min_pct:g}%")
    if (
        product.is_open_box
        and previous_lowest is not None
        and price < previous_lowest
    ):
        reasons.append(f"nouveau plus bas ({price:.0f}$ < {previous_lowest:.0f}$)")

    if not reasons:
        state.rearm()
        return None

    allowed, why_send = _should_send(state, price, target, settings, now)
    if not allowed:
        return None

    state.record_alert(price, now=now)

    return Alert(
        kind=AlertKind.PRICE_DROP,
        name=product.display_name,
        sku=product.sku,
        reason=f"{'; '.join(reasons)} — {why_send}",
        price=price,
        regular_price=product.regular_price if product.has_real_discount else None,
        discount_amount=product.discount_amount,
        discount_pct=product.discount_pct,
        lowest_ever=state.lowest_ever,
        level=level_for(product.discount_pct, under_budget=under_budget),
        seller=_seller_label(product),
        condition=product.condition_label,
        url=product.full_url,
        tax_rate=settings.tax_rate,
    )


def _should_send(
    state: ProductState,
    price: float,
    target: Target,
    settings: Settings,
    now: datetime,
) -> tuple[bool, str]:
    """Anti-spam : True seulement si l'alerte apporte une info nouvelle."""
    if state.last_alerted_price is None:
        return True, "première alerte"

    if price <= state.last_alerted_price - settings.realert_delta:
        return True, f"nouvelle baisse ≥ {settings.realert_delta:.0f}$"

    if (
        settings.daily_reminder
        and state.last_alerted_at is not None
        and now - state.last_alerted_at >= REALERT_WINDOW
        and price <= target.alert_price
    ):
        return True, "rappel 24h (toujours sous le seuil)"

    return False, "déjà signalé à ce prix"


def register_failure(
    state: ProductState,
    target: Target,
    settings: Settings,
    detail: str,
) -> Alert | None:
    """Incrémente le compteur d'échecs ; émet UNE alerte de panne au franchissement du seuil."""
    state.consecutive_failures += 1
    if state.consecutive_failures < settings.failure_threshold or state.failure_alerted:
        return None

    state.failure_alerted = True
    return Alert(
        kind=AlertKind.FAILURE,
        name=target.name,
        sku=target.web_code,
        reason=f"{state.consecutive_failures} échecs consécutifs — {detail}",
    )


def register_success(state: ProductState) -> None:
    """Réinitialise le compteur d'échecs après un cycle réussi."""
    state.consecutive_failures = 0
    state.failure_alerted = False


def _seller_label(product: Product) -> str:
    if product.sold_by_bestbuy:
        return "Best Buy"
    return f"vendeur tiers (id {product.seller_id})"
