"""Orchestration d'un cycle de surveillance.

`run_once()` : un passage complet sur la watchlist. Réutilisé tel quel par
l'ordonnanceur du Sprint 4. Ne fait AUCUN envoi — il retourne la liste des
alertes ; c'est l'appelant qui décide quoi en faire (afficher, Telegram...).
"""

from __future__ import annotations

import logging
from datetime import datetime

from bot.alerts import Alert
from bot.bestbuy import BestBuyApiError, BestBuyClient
from bot.config import Settings, Target
from bot.detector import (
    evaluate_target,
    register_failure,
    register_success,
)
from bot.discovery import discover
from bot.state import StateStore, utcnow

logger = logging.getLogger(__name__)


def full_cycle(
    client: BestBuyClient,
    targets: list[Target],
    store: StateStore,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> list[Alert]:
    """Watchlist + découverte. C'est ce qu'appellent la CLI `run` et le scheduler."""
    now = now or utcnow()
    alerts = run_once(client, targets, store, settings, now=now)

    if settings.discovery_enabled:
        try:
            alerts += discover(
                client, settings, store, [t.web_code for t in targets], now=now
            )
        except Exception:  # noqa: BLE001 — la découverte ne doit pas casser le cycle
            logger.exception("Module de découverte en échec (watchlist non affectée)")

    return alerts


def run_once(
    client: BestBuyClient,
    targets: list[Target],
    store: StateStore,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> list[Alert]:
    now = now or utcnow()
    alerts: list[Alert] = []
    skus = [t.web_code for t in targets]

    try:
        products = client.get_prices(skus)
    except BestBuyApiError as exc:
        logger.error("catalog/query a échoué : %s", exc)
        for target in targets:
            failure = register_failure(store.get(target.web_code), target, settings, str(exc))
            if failure:
                alerts.append(failure)
        store.save()
        return alerts

    by_sku = {p.sku: p for p in products}

    for target in targets:
        state = store.get(target.web_code)
        product = by_sku.get(target.web_code)

        if product is None:
            logger.warning("SKU %s absent de la réponse catalog/query", target.web_code)
            failure = register_failure(
                state, target, settings, "SKU absent de catalog/query (délisté ?)"
            )
            if failure:
                alerts.append(failure)
            continue

        register_success(state)
        alert = evaluate_target(target, product, state, settings, now=now)
        if alert is None:
            continue

        _enrich_with_stock(client, target.web_code, alert)
        alerts.append(alert)
        logger.info("ALERTE %s : %s", target.web_code, alert.reason)

    store.save()
    return alerts


def _enrich_with_stock(client: BestBuyClient, sku: str, alert: Alert) -> None:
    """Best effort : un appel `product/<sku>` en plus pour connaître le stock."""
    try:
        detail = client.get_product(sku)
    except BestBuyApiError as exc:
        logger.debug("détail stock %s indisponible : %s", sku, exc)
        return
    avail = detail.availability
    if avail is None:
        return
    label = avail.online_availability or "?"
    if avail.online_availability_count is not None:
        label += f" ({avail.online_availability_count} en stock)"
    alert.stock = label
