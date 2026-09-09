"""Exécution périodique du bot (Sprint 4).

`watch()` :
- lance un cycle tout de suite, puis toutes les `interval_minutes` ;
- chaque cycle est isolé : une exception est loggée mais ne tue pas la boucle ;
- écrit un heartbeat (`last_run.txt`) après chaque cycle réussi -> un moniteur
  externe (systemd, UptimeRobot via un petit script) peut détecter un bot figé ;
- s'arrête proprement sur Ctrl+C (SIGINT) / SIGTERM (systemd stop).
"""

from __future__ import annotations

import logging
import signal
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler

from bot.bestbuy import BestBuyClient
from bot.config import PROJECT_ROOT, Settings, load_targets
from bot.notify import send_alerts
from bot.runner import full_cycle
from bot.state import StateStore

logger = logging.getLogger(__name__)

HEARTBEAT_PATH = PROJECT_ROOT / "last_run.txt"


def run_cycle(settings: Settings) -> None:
    """Un cycle complet. Ne lève jamais : tout est attrapé et loggé."""
    try:
        targets = load_targets()
        store = StateStore()
        with BestBuyClient(
            postal_code=settings.postal_code, user_agent=settings.user_agent
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
    interval = max(5, settings.interval_minutes)

    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        run_cycle,
        trigger="interval",
        minutes=interval,
        args=[settings],
        id="watch_cycle",
        max_instances=1,          # jamais deux cycles en parallèle
        coalesce=True,            # si on a pris du retard, un seul rattrapage
        next_run_time=datetime.now(tz=timezone.utc),  # -> premier cycle immédiat
    )

    # systemd envoie SIGTERM à l'arrêt : on le transforme en KeyboardInterrupt
    # pour sortir proprement de scheduler.start().
    def _stop(signum, _frame):
        logger.info("Signal %s reçu.", signal.Signals(signum).name)
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _stop)

    logger.info(
        "Démarrage : un cycle maintenant, puis toutes les %d min. Ctrl+C pour arrêter.",
        interval,
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Arrêt demandé, extinction propre.")
        scheduler.shutdown(wait=False)
