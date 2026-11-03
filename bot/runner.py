"""Orchestration d'un cycle de surveillance.

`run_once()` : un passage complet sur la watchlist. Réutilisé tel quel par
l'ordonnanceur du Sprint 4. Ne fait AUCUN envoi — il retourne la liste des
alertes ; c'est l'appelant qui décide quoi en faire (afficher, Telegram...).
"""

from __future__ import annotations

import logging
from datetime import datetime

from bot.alerts import Alert, AlertKind, level_for
from bot.bestbuy import BestBuyApiError, BestBuyClient
from bot.config import Settings, Target
from bot.criteria import check_specs
from bot.models import specs_from_text
from bot.detector import (
    evaluate_stock,
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
    variant_skus: set[str] = set()
    alerts = run_once(client, targets, store, settings, now=now, variant_skus=variant_skus)

    if settings.discovery_enabled:
        try:
            known = [t.web_code for t in targets] + sorted(variant_skus)
            alerts += discover(client, settings, store, known, now=now)
        except Exception:  # noqa: BLE001 — la découverte ne doit pas casser le cycle
            logger.exception("Module de découverte en échec (watchlist non affectée)")

    removed = store.prune({t.web_code for t in targets}, now=now)
    if removed:
        logger.info("state.json : %d annonce(s) périmée(s) oubliée(s)", removed)
        store.save()

    return alerts


def run_once(
    client: BestBuyClient,
    targets: list[Target],
    store: StateStore,
    settings: Settings,
    *,
    now: datetime | None = None,
    variant_skus: set[str] | None = None,
) -> list[Alert]:
    now = now or utcnow()
    alerts: list[Alert] = []
    variant_skus = variant_skus if variant_skus is not None else set()
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
            # Le neuf a disparu (rupture / délisté) : ses boîtes ouvertes restent à voir.
            if target.check_open_box:
                alerts += _check_variants(
                    client, target, store, settings, now,
                    watchlist=set(skus), variant_skus=variant_skus,
                )
            continue

        register_success(state)
        alert = evaluate_target(target, product, state, settings, now=now)

        # Stock : à chaque cycle si activé (alerte retour en stock), sinon
        # seulement pour enrichir une alerte de prix.
        avail = None
        if settings.stock_check_enabled or alert is not None:
            avail = _fetch_availability(client, target.web_code)
        if avail is not None and settings.stock_check_enabled:
            back = evaluate_stock(target, product, state, avail.is_purchasable, settings)
            if back is not None:
                alerts.append(back)
                logger.info("RETOUR EN STOCK %s", target.web_code)

        if alert is not None:
            if avail is not None:
                alert.stock = _stock_label(avail)
            alerts.append(alert)
            logger.info("ALERTE %s : %s", target.web_code, alert.reason)

        if target.check_open_box:
            alerts += _check_variants(
                client, target, store, settings, now,
                watchlist=set(skus), variant_skus=variant_skus,
            )

    store.save()
    return alerts


def _fetch_availability(client: BestBuyClient, sku: str):
    """Best effort : un appel `product/<sku>` pour connaître le stock."""
    try:
        return client.get_product(sku).availability
    except BestBuyApiError as exc:
        logger.debug("détail stock %s indisponible : %s", sku, exc)
        return None


def _stock_label(avail) -> str:
    label = avail.online_availability or "?"
    if avail.online_availability_count is not None:
        label += f" ({avail.online_availability_count} en stock)"
    return label


def _gb(value: int) -> str:
    return f"{value // 1024} To" if value >= 1024 and value % 1024 == 0 else f"{value} Go"


def _variant_extra(
    target_name: str,
    ref: tuple[int | None, int | None],
    got: tuple[int | None, int | None],
) -> dict[str, str]:
    """`variant_of` + `config_diff` (« 512 Go au lieu de 1 To ») si la config diffère."""
    diffs = []
    for label, ref_v, got_v in (("RAM", ref[0], got[0]), ("SSD", ref[1], got[1])):
        if ref_v is not None and got_v is not None and ref_v != got_v:
            diffs.append(f"{label} {_gb(got_v)} au lieu de {_gb(ref_v)}")
    extra = {"variant_of": target_name}
    if diffs:
        extra["config_diff"] = ", ".join(diffs)
    return extra


def _check_variants(
    client: BestBuyClient,
    target: Target,
    store: StateStore,
    settings: Settings,
    now: datetime,
    *,
    watchlist: set[str],
    variant_skus: set[str],
) -> list[Alert]:
    """Boîte ouverte / remis à neuf du même modèle : alerte si ≤ seuil du target.

    C'est souvent là que se cache le meilleur prix d'un modèle précis.
    """
    try:
        variants = client.get_conditions(target.web_code)
    except BestBuyApiError as exc:
        logger.debug("variantes %s indisponibles : %s", target.web_code, exc)
        return []

    # Specs du produit surveillé (nom Best Buy si connu, sinon le nom donné au /add)
    ref_name = store.get(target.web_code).name or target.name
    ref_ram, ref_storage = specs_from_text(ref_name)

    alerts: list[Alert] = []
    for variant in variants:
        if variant.sku in watchlist or variant.sku in variant_skus:
            continue
        variant_skus.add(variant.sku)

        # `conditions` renvoie aussi d'autres configs du modèle (ex. 512 Go pour
        # un 1 To surveillé) : on leur applique les minimums RAM / stockage.
        ram, storage = specs_from_text(variant.display_name)
        if settings.min_ram_gb and ram is not None and ram < settings.min_ram_gb:
            logger.debug("variante %s écartée : RAM %s Go", variant.sku, ram)
            continue
        if settings.min_storage_gb and storage is not None and storage < settings.min_storage_gb:
            logger.debug("variante %s écartée : stockage %s Go", variant.sku, storage)
            continue
        specs = check_specs(variant.display_name, settings)

        state = store.get(variant.sku)
        state.record_price(variant.sale_price, variant.regular_price, now=now)
        state.describe(
            name=variant.display_name,
            url=variant.full_url,
            condition=variant.condition_full,
            source=f"variante de {target.web_code}",
        )

        price_error = variant.discount_pct >= settings.price_error_pct
        if variant.sale_price > target.alert_price and not price_error:
            state.rearm()
            continue
        if (
            state.last_alerted_price is not None
            and variant.sale_price > state.last_alerted_price - settings.realert_delta
        ):
            continue

        state.record_alert(variant.sale_price, now=now)
        alerts.append(
            Alert(
                kind=AlertKind.PRICE_DROP,
                name=variant.display_name,
                sku=variant.sku,
                reason=f"variante {variant.condition_full} de {target.web_code} "
                f"≤ seuil {target.alert_price:.0f}$",
                price=variant.sale_price,
                regular_price=variant.regular_price if variant.has_real_discount else None,
                discount_amount=variant.discount_amount,
                discount_pct=variant.discount_pct,
                lowest_ever=state.lowest_ever,
                level=level_for(
                    variant.discount_pct,
                    under_budget=True,
                    price_error_pct=settings.price_error_pct,
                ),
                seller=variant.seller_name or (
                    None if variant.seller_id in (None, "bbyca") else f"vendeur tiers (id {variant.seller_id})"
                ),
                condition=variant.condition_full,
                url=variant.full_url,
                tax_rate=settings.tax_rate,
                budget=settings.budget,
                specs=specs.summary,
                specs_unverified=specs.unknown_specs,
                extra=_variant_extra(target.name, (ref_ram, ref_storage), (ram, storage)),
            )
        )
        logger.info("VARIANTE %s (%s) à %.0f$", variant.sku, variant.condition_full, variant.sale_price)
    return alerts
