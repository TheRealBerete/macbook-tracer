"""Configuration des logs.

- console : niveau INFO (ou DEBUG avec -v)
- fichier `bot.log` : tout, avec rotation (5 fichiers de 1 Mo)

Rotation = quand `bot.log` atteint 1 Mo, il devient `bot.log.1`, etc. Ça évite
qu'un bot qui tourne des mois ne remplisse le disque.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from bot.config import PROJECT_ROOT

LOG_PATH = PROJECT_ROOT / "bot.log"
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(*, verbose: bool = False, to_file: bool = True) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(console)

    if to_file:
        file_handler = RotatingFileHandler(
            LOG_PATH, maxBytes=1_000_000, backupCount=5, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(file_handler)

    # Ces libs sont très bavardes (une ligne par étape TCP/TLS) -> WARNING.
    for noisy in ("httpx", "httpcore", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
