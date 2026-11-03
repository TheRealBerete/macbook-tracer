"""Module de découverte (F10) : repérer des MacBook Pro intéressants qui ne sont
PAS dans la watchlist — neufs en promo, boîte ouverte, remis à neuf, liquidation.

Principe : on parcourt la recherche Best Buy (« macbook pro », toutes les pages
jusqu'à `DISCOVERY_MAX_PAGES`), puis on garde ce qui passe les critères d'achat :
- catégorie MacBook Pro (pas les housses ni AppleCare) ;
- puce M4 → M6, ≥ 16 Go de RAM, ≥ 512 Go de stockage (`bot/criteria.py`) ;
- prix entre `DISCOVERY_MIN_PRICE` et `DISCOVERY_MAX_PRICE` ;
- sous le budget → toujours signalé ; au-dessus → seulement boîte ouverte /
  remis à neuf ou rabais ≥ `DISCOVERY_MIN_DISCOUNT_PCT`.

Dé-doublonnage via `state.json` : on ne re-signale une pépite que si son prix
baisse encore d'au moins `REALERT_DELTA`.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime

from bot.alerts import Alert, AlertKind, level_for
from bot.bestbuy import BestBuyApiError, BestBuyClient
from bot.config import Settings
from bot.criteria import SpecCheck, check_specs
from bot.state import StateStore, utcnow

logger = logging.getLogger(__name__)

# Catégorie "MacBook Pro d'Apple" — filtre les accessoires (housses, AppleCare...).
MACBOOK_PRO_CATEGORY_ID = "12746019"


def _passes_filters(product, settings: Settings) -> tuple[str | None, SpecCheck | None]:
    """(None, specs) si le produit passe, sinon (raison du rejet, specs)."""
    category = product.primary_parent_category_id or ""
    if MACBOOK_PRO_CATEGORY_ID not in category:
        return "hors catégorie MacBook Pro", None

    if product.condition_label == "Remis à neuf" and not settings.discovery_include_refurbished:
        return "reconditionné (désactivé)", None

    if not (settings.discovery_min_price <= product.sale_price <= settings.discovery_max_price):
        return f"prix {product.sale_price:.0f}$ hors fourchette", None

    specs = check_specs(product.display_name, settings)
    if not specs.ok:
        return specs.reason, specs

    under_budget = product.sale_price <= settings.budget
    is_deal = under_budget or product.is_open_box or (
        product.has_real_discount
        and product.discount_pct >= settings.discovery_min_discount_pct
    )
    if not is_deal:
        return "au-dessus du budget sans promo suffisante", specs

    return None, specs


def discover(
    client: BestBuyClient,
    settings: Settings,
    store: StateStore,
    known_skus: Sequence[str],
    *,
    queries: Sequence[str] | None = None,
    now: datetime | None = None,
) -> list[Alert]:
    if not settings.discovery_enabled:
        return []

    now = now or utcnow()
    known = set(known_skus)
    seen_this_run: set[str] = set()
    alerts: list[Alert] = []
    scanned = 0

    for query in queries or settings.queries:
        for page in range(1, settings.discovery_max_pages + 1):
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
                scanned += 1

                rejection, specs = _passes_filters(product, settings)
                if rejection is not None:
                    logger.debug("découverte : %s écarté (%s)", product.sku, rejection)
                    continue

                alert = _maybe_alert(product, specs, store, settings, now)
                if alert is not None:
                    alerts.append(alert)

            if page >= result.total_pages:
                break

    logger.info("découverte : %d produit(s) analysé(s), %d pépite(s)", scanned, len(alerts))
    store.save()
    alerts.sort(key=lambda a: a.price or 0)
    return alerts


def _maybe_alert(
    product, specs: SpecCheck, store: StateStore, settings: Settings, now: datetime
) -> Alert | None:
    state = store.get(product.sku)
    state.record_price(product.sale_price, product.regular_price, now=now)
    state.describe(
        name=product.display_name,
        url=product.full_url,
        condition=product.condition_full,
        specs=specs.summary,
        source="découverte",
    )

    # anti-spam : déjà signalée et pas de nouvelle baisse significative -> silence
    if (
        state.last_alerted_price is not None
        and product.sale_price > state.last_alerted_price - settings.realert_delta
    ):
        return None

    first_time = state.last_alerted_price is None
    state.record_alert(product.sale_price, now=now)
    logger.info("découverte : %s à %.0f$ (%s)", product.sku, product.sale_price, product.condition_full)

    return Alert(
        kind=AlertKind.DISCOVERY,
        name=product.display_name,
        sku=product.sku,
        reason=("nouvelle pépite" if first_time else "prix en baisse")
        + f" — {product.condition_full}",
        price=product.sale_price,
        regular_price=product.regular_price if product.has_real_discount else None,
        discount_amount=product.discount_amount,
        discount_pct=product.discount_pct,
        lowest_ever=state.lowest_ever,
        level=level_for(
            product.discount_pct,
            under_budget=product.sale_price <= settings.budget,
            price_error_pct=settings.price_error_pct,
        ),
        seller=None if product.sold_by_bestbuy else f"vendeur tiers (id {product.seller_id})",
        condition=product.condition_full,
        url=product.full_url,
        tax_rate=settings.tax_rate,
        budget=settings.budget,
        specs=specs.summary,
        specs_unverified=specs.unknown_specs,
    )
