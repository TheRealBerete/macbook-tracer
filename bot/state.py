"""État persistant du bot (`state.json`).

On y garde, par SKU :
- le dernier prix vu et le plus bas jamais vu (`lowest_ever`) ;
- ce qu'on a déjà alerté (`last_alerted_price` / `last_alerted_at`) -> anti-spam (F2b) ;
- le compteur d'échecs API consécutifs -> alerte de panne (F2c).

Écriture **atomique** : on écrit dans un fichier temporaire puis on le renomme.
Ainsi un crash en plein milieu ne corrompt jamais `state.json`.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from bot.config import DEFAULT_STATE_PATH


def utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class ProductState(BaseModel):
    current_price: float | None = None
    regular_price: float | None = None
    lowest_ever: float | None = None
    last_seen_at: datetime | None = None

    last_alerted_price: float | None = None
    last_alerted_at: datetime | None = None

    consecutive_failures: int = 0
    failure_alerted: bool = False

    # Vu disponible au dernier passage ? (pour l'alerte "de retour en stock")
    was_available: bool | None = None

    def record_price(self, price: float, regular: float | None, *, now: datetime | None = None) -> None:
        now = now or utcnow()
        self.current_price = price
        self.regular_price = regular
        self.last_seen_at = now
        if self.lowest_ever is None or price < self.lowest_ever:
            self.lowest_ever = price

    def record_alert(self, price: float, *, now: datetime | None = None) -> None:
        self.last_alerted_price = price
        self.last_alerted_at = now or utcnow()

    def rearm(self) -> None:
        """Le produit n'est plus en situation d'alerte : on oublie la dernière alerte
        pour qu'une prochaine baisse re-déclenche."""
        self.last_alerted_price = None
        self.last_alerted_at = None


class StateStore:
    """Dictionnaire SKU -> ProductState, chargé depuis / sauvé vers un fichier JSON."""

    def __init__(self, path: Path | str = DEFAULT_STATE_PATH) -> None:
        self.path = Path(path)
        self._states: dict[str, ProductState] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # Fichier corrompu : on repart de zéro plutôt que de planter.
            # (une sauvegarde .bak est laissée pour inspection)
            self.path.replace(self.path.with_suffix(".json.bak"))
            return
        self._states = {sku: ProductState.model_validate(data) for sku, data in raw.items()}

    def get(self, sku: str) -> ProductState:
        return self._states.setdefault(sku, ProductState())

    def all(self) -> dict[str, ProductState]:
        return self._states

    def save(self) -> None:
        payload = {
            sku: state.model_dump(mode="json", exclude_none=True)
            for sku, state in self._states.items()
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2)

        # écriture atomique : fichier temp dans le même dossier, puis os.replace
        fd, tmp_path = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.replace(tmp_path, self.path)
        except BaseException:
            Path(tmp_path).unlink(missing_ok=True)
            raise
