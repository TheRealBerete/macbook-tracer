"""Module de découverte (F10) : repérer des MacBook Pro intéressants qui ne sont
PAS dans la watchlist — typiquement des annonces boîte ouverte / liquidation qui
apparaissent sans qu'on connaisse leur Web Code à l'avance.

Principe : on interroge la recherche Best Buy pour "macbook pro", on garde les
résultats sous un prix plafond global (`DISCOVERY_MAX_PRICE`) qui sont soit
- en vraie promo (baisse ≥ `DISCOVERY_MIN_DISCOUNT_PCT` vs prix régulier), soit
- boîte ouverte / remis à neuf (déjà décotés par nature).

Dé-doublonnage via `state.json` : on ne re-signale une pépite que si son prix
baisse encore d'au moins `REALERT_DELTA`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from datetime import datetime

from bot.alerts import Alert, AlertKind, AlertLevel
from bot.bestbuy import BestBuyApiError, BestBuyClient
from bot.config import Settings
from bot.state import StateStore, utcnow

logger = logging.getLogger(__name__)

DEFAULT_QUERIES = ("macbook pro",)
MAX_PAGES = 3
# Catégorie "MacBook Pro d'Apple" — filtre les accessoires (housses, AppleCare...).
MACBOOK_PRO_CATEGORY_ID = "12746019"
# Puce Apple Silicon dans le libellé : "M1", "M2 Pro", "M4 Max"...
_APPLE_SILICON = re.compile(r"\bM[1-4]\b")


def _passes_filters(product, settings: Settings) -> str | None:
    """Retourne None si le produit passe, sinon la raison du rejet (pour les logs debug)."""
    category = product.primary_parent_category_id or ""
    if MACBOOK_PRO_CATEGORY_ID not in category:
        return "hors catégorie MacBook Pro"

    condition = product.condition_label
    if condition == "Remis à neuf" and not settings.discovery_include_refurbished:
        return "reconditionné (hors scope V1)"

    if not (settings.discovery_min_price <= product.sale_price <= settings.discovery_max_price):
        return f"prix {product.sale_price:.0f}$ hors fourchette"

    if settings.discovery_apple_silicon_only and not _APPLE_SILICON.search(product.display_name):
        return "pas de puce Apple Silicon détectée"

    is_deal = product.is_open_box or (
        product.has_real_discount
        and product.discount_pct >= settings.discovery_min_discount_pct
    )
    if not is_deal:
        return "ni promo suffisante ni boîte ouverte"

    return None


def discover(
    client: BestBuyClient,
    settings: Settings,
    store: StateStore,
    known_skus: Sequence[str],
    *,
    queries: Sequence[str] = DEFAULT_QUERIES,
    now: datetime | None = None,
) -> list[Alert]:
    if not settings.discovery_enabled:
        return []

    now = now or utcnow()
    known = set(known_skus)
    seen_this_run: set[str] = set()
    alerts: list[Alert] = []

    for query in queries:
        for page in range(1, MAX_PAGES + 1):
            try:
                result = client.search(query, page=page)
            except BestBuyApiError as exc:
                logger.warning("découverte : recherche '%s' p.%d a échoué : %s", query, page, exc)
                break

            if not result.products:
                break

            for product in result.products:
                if product.sku in known or product.sku in seen_this_run:
                    continue
                seen_this_run.add(product.sku)

                rejection = _passes_filters(product, settings)
                if rejection is not None:
                    logger.debug("découverte : %s écarté (%s)", product.sku, rejection)
                    continue

                alert = _maybe_alert(product, store, settings, now)
                if alert is not None:
                    alerts.append(alert)

            if page >= result.total_pages:
                break

    store.save()
    return alerts


def _maybe_alert(product, store: StateStore, settings: Settings, now: datetime) -> Alert | None:
    state = store.get(product.sku)
    state.record_price(product.sale_price, product.regular_price, now=now)

    # anti-spam : déjà signalée et pas de nouvelle baisse significative -> silence
    if (
        state.last_alerted_price is not None
        and product.sale_price > state.last_alerted_price - settings.realert_delta
    ):
        return None

    first_time = state.last_alerted_price is None
    state.record_alert(product.sale_price, now=now)
    logger.info("découverte : %s à %.0f$ (%s)", product.sku, product.sale_price, product.condition_label)

    return Alert(
        kind=AlertKind.DISCOVERY,
        name=product.display_name,
        sku=product.sku,
        reason=("nouvelle pépite" if first_time else "prix en baisse")
        + f" — {product.condition_label}",
        price=product.sale_price,
        regular_price=product.regular_price if product.has_real_discount else None,
        discount_amount=product.discount_amount,
        discount_pct=product.discount_pct,
        lowest_ever=state.lowest_ever,
        level=AlertLevel.YELLOW,
        condition=product.condition_label,
        url=product.full_url,
        tax_rate=settings.tax_rate,
    )
