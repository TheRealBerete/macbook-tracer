"""Aiguillage des alertes vers Telegram.

Séparé du `runner` pour que le cycle de surveillance reste sans effet de bord
réseau autre que l'API Best Buy. Réutilisé par le scheduler (Sprint 4).
"""

from __future__ import annotations

import logging

from bot.alerts import Alert
from bot.config import Settings
from bot.format import format_alert
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

    sent = 0
    with TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id) as tg:
        for alert in alerts:
            try:
                tg.send_message(format_alert(alert))
                sent += 1
            except TelegramError as exc:
                logger.error("Échec envoi Telegram (%s) : %s", alert.kind.value, exc)
    return sent
