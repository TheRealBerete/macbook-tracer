"""Exécution en continu du bot.

`watch()` fait tourner deux choses en parallèle :
- un **ordonnanceur** (`BackgroundScheduler`) qui lance un cycle immédiatement
  puis toutes les `INTERVAL_MINUTES` ;
- si Telegram est configuré, un **écouteur de commandes** (long-polling) sur le
  thread principal.

Un `threading.Lock` sérialise : cycle planifié, cycle manuel (/run) et
modifications de la watchlist (/add, /remove) ne se marchent jamais dessus.

Arrêt propre sur Ctrl+C (SIGINT) et SIGTERM (Docker/Dokploy stop).
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from bot.bestbuy import BestBuyClient
from bot.config import DATA_DIR, Settings, load_targets
from bot.notify import send_alerts
from bot.runner import full_cycle
from bot.state import StateStore

logger = logging.getLogger(__name__)

HEARTBEAT_PATH = DATA_DIR / "last_run.txt"

# Sérialise cycle planifié / cycle manuel / édition de la watchlist.
_cycle_lock = threading.Lock()

# Début du dernier cycle (monotonic) et mode Black Friday au dernier tick.
_last_cycle_at: float | None = None
_last_rush: bool | None = None


def tick_interval(settings: Settings) -> int:
    """Fréquence du « tick » de l'ordonnanceur : la plus courte des deux périodes."""
    base = max(1, settings.interval_minutes)
    if settings.rush_enabled:
        return max(1, min(base, settings.rush_interval_minutes))
    return base


def tick(settings: Settings, now: datetime | None = None) -> bool:
    """Appelé à chaque tick : lance un cycle s'il est dû. Retourne True si lancé.

    Hors Black Friday on ne cycle que toutes les `INTERVAL_MINUTES` ; pendant la
    plage `RUSH_START`→`RUSH_END`, toutes les `RUSH_INTERVAL_MINUTES`.
    """
    global _last_cycle_at, _last_rush
    now = now or datetime.now(tz=timezone.utc)
    rush = settings.in_rush(now)
    if _last_rush is not None and rush != _last_rush:
        _announce_rush(settings, rush)
    _last_rush = rush

    due_after = settings.effective_interval(now) * 60 - 30  # 30 s de marge (dérive du tick)
    if _last_cycle_at is not None and time.monotonic() - _last_cycle_at < due_after:
        return False
    run_cycle(settings)
    return True


def _announce_rush(settings: Settings, rush: bool) -> None:
    if not settings.telegram_ready:
        return
    from bot.telegram import TelegramError, TelegramNotifier

    text = (
        f"🛍️ <b>Mode Black Friday activé</b> : un cycle toutes les "
        f"{settings.rush_interval_minutes} min jusqu'au {settings.rush_end}."
        if rush
        else f"😴 Fin du mode Black Friday : retour à un cycle toutes les "
        f"{settings.interval_minutes} min."
    )
    try:
        with TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id) as tg:
            tg.send_message(text)
    except TelegramError as exc:
        logger.warning("Annonce du mode Black Friday : %s", exc)


def run_cycle(settings: Settings) -> None:
    """Un cycle complet. Ne lève jamais : tout est attrapé et loggé."""
    global _last_cycle_at
    with _cycle_lock:
        _last_cycle_at = time.monotonic()
        try:
            targets = load_targets()
            store = StateStore()
            with BestBuyClient(
                postal_code=settings.postal_code,
                user_agent=settings.user_agent,
                request_delay=(settings.request_delay_min, settings.request_delay_max),
            ) as client:
                alerts = full_cycle(client, targets, store, settings)

            sent = send_alerts(alerts, settings) if alerts else 0
            logger.info(
                "Cycle terminé : %d produit(s), %d alerte(s), %d envoyée(s)",
                len(targets), len(alerts), sent,
            )
            _write_heartbeat()
        except Exception:  # noqa: BLE001 — la boucle doit survivre à tout
            logger.exception("Cycle en échec (la surveillance continue)")


def _write_heartbeat() -> None:
    try:
        HEARTBEAT_PATH.write_text(
            datetime.now(tz=timezone.utc).isoformat(), encoding="utf-8"
        )
    except OSError as exc:
        logger.warning("Impossible d'écrire le heartbeat : %s", exc)


def watch(settings: Settings | None = None) -> None:
    settings = settings or Settings()
    interval = tick_interval(settings)

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        tick,
        trigger="interval",
        minutes=interval,
        args=[settings],
        id="watch_cycle",
        max_instances=1,
        coalesce=True,
        # premier cycle immédiat ; None = on l'exécute même si l'ordonnanceur
        # a démarré avec quelques secondes de retard (sinon APScheduler le "rate")
        next_run_time=datetime.now(tz=timezone.utc),
        misfire_grace_time=None,
    )

    def trigger_cycle_now() -> None:
        scheduler.add_job(
            run_cycle,
            args=[settings],
            id="manual_run",
            replace_existing=True,
            misfire_grace_time=60,
            next_run_time=datetime.now(tz=timezone.utc),
        )

    command_bot = _build_command_bot(settings, trigger_cycle_now)

    stop_event = threading.Event()

    def _stop(signum, _frame):
        logger.info("Signal %s reçu, arrêt.", signal.Signals(signum).name)
        stop_event.set()
        if command_bot is not None:
            command_bot.stop()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    scheduler.start()
    logger.info(
        "Démarrage : cycle immédiat, puis toutes les %d min (%d min en mode Black Friday %s→%s%s).",
        settings.interval_minutes, settings.rush_interval_minutes,
        settings.rush_start, settings.rush_end,
        ", ACTIF" if settings.in_rush() else "",
    )

    try:
        if command_bot is not None:
            command_bot.run()          # bloque jusqu'à stop()
        else:
            stop_event.wait()          # pas de Telegram : on dort jusqu'au signal
    finally:
        logger.info("Extinction propre.")
        scheduler.shutdown(wait=False)


def _build_command_bot(settings: Settings, trigger_cycle_now):
    if not settings.telegram_ready:
        logger.warning("Telegram non configuré : les commandes sont désactivées.")
        return None

    from bot.commands import COMMAND_MENU, CommandBot
    from bot.telegram import TelegramNotifier

    notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    notifier.set_my_commands(COMMAND_MENU)
    return CommandBot(
        settings,
        notifier,
        cycle_lock=_cycle_lock,
        trigger_cycle=trigger_cycle_now,
        heartbeat_path=HEARTBEAT_PATH,
    )
