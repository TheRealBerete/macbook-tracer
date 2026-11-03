"""Aiguillage des alertes vers Telegram.

Séparé du `runner` pour que le cycle de surveillance reste sans effet de bord
réseau autre que l'API Best Buy. Réutilisé par le scheduler (Sprint 4).
"""

from __future__ import annotations

import logging

from bot.alerts import Alert, AlertKind, AlertLevel
from bot.config import Settings
from bot.format import format_alert, format_digest
from bot.telegram import TelegramError, TelegramNotifier

logger = logging.getLogger(__name__)


def send_alerts(alerts: list[Alert], settings: Settings) -> int:
    """Envoie chaque alerte sur Telegram. Retourne le nombre d'envois réussis.

    Ne lève pas : un échec d'envoi est loggé mais ne casse pas le cycle
    (les autres alertes doivent quand même partir).
    """
    if not alerts:
        return 0
    if not settings.telegram_ready:
        logger.warning("%d alerte(s) non envoyée(s) : Telegram non configuré", len(alerts))
        return 0

    messages = build_messages(alerts, settings)
    sent = 0
    with TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id) as tg:
        for text in messages:
            try:
                tg.send_message(text)
                sent += 1
            except TelegramError as exc:
                logger.error("Échec envoi Telegram : %s", exc)
    return sent


def build_messages(alerts: list[Alert], settings: Settings) -> list[str]:
    """Ordonne les alertes (urgentes d'abord) et regroupe les vagues de pépites.

    Au Black Friday, la découverte peut sortir 30 pépites d'un coup : on envoie les
    N meilleures en détail, le reste dans UN message récapitulatif.
    """
    def priority(alert: Alert) -> tuple[int, float]:
        rank = {
            AlertKind.FAILURE: 0,
        }.get(alert.kind, 1)
        if alert.level is AlertLevel.CRITICAL:
            rank = -1
        return rank, alert.price or 0.0

    ordered = sorted(alerts, key=priority)
    limit = max(1, settings.discovery_max_alerts_per_cycle)

    detailed: list[Alert] = []
    overflow: list[Alert] = []
    discoveries = 0
    for alert in ordered:
        if alert.kind is AlertKind.DISCOVERY and alert.level is not AlertLevel.CRITICAL:
            discoveries += 1
            if discoveries > limit:
                overflow.append(alert)
                continue
        detailed.append(alert)

    messages = [format_alert(a) for a in detailed]
    for start in range(0, len(overflow), 15):  # ~15 lignes / message (limite 4096 car.)
        messages.append(format_digest(overflow[start:start + 15]))
    return messages
