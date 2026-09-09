"""Configuration du bot.

Deux sources :
- `.env`  -> réglages globaux + secrets  (classe `Settings`, via pydantic-settings)
- `targets.json` -> la watchlist (liste de `Target`)

Tout est validé au démarrage : une valeur absente ou mal typée lève une erreur
claire tout de suite, pas au milieu d'un cycle à 3h du matin.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Dossier des données mutables (state.json, bot.log, last_run.txt, targets.json).
# En local : la racine du projet. En conteneur : un volume monté (DATA_DIR=/data)
# pour que l'état survive aux redéploiements.
DATA_DIR = Path(os.environ.get("DATA_DIR", PROJECT_ROOT))

DEFAULT_TARGETS_PATH = DATA_DIR / "targets.json"
DEFAULT_STATE_PATH = DATA_DIR / "state.json"


class Settings(BaseSettings):
    """Réglages globaux, chargés depuis les variables d'environnement / `.env`."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Telegram (utilisé au Sprint 3) ---
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # --- Scraping ---
    interval_minutes: int = 60
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
    )
    postal_code: str = "M6N 5G8"
    request_delay_min: float = 1.0
    request_delay_max: float = 3.0

    # --- Détection / alertes ---
    tax_rate: float = 0.13
    realert_delta: float = 50.0        # $ de baisse en plus avant de ré-alerter
    daily_reminder: bool = True        # rappel 1x/24h si toujours sous le seuil
    failure_threshold: int = 3         # échecs API consécutifs avant alerte de panne
    default_min_discount_pct: float = 15.0

    # --- Découverte (Sprint 6) ---
    discovery_enabled: bool = True
    discovery_max_price: float = 2000.0
    discovery_min_price: float = 1200.0        # sous ce prix = vieux matériel, on ignore
    discovery_min_discount_pct: float = 15.0
    discovery_apple_silicon_only: bool = True  # M1/M2/M3/M4 seulement (pas d'Intel)
    discovery_include_refurbished: bool = False  # PRD V1 : reconditionné hors scope

    @property
    def telegram_ready(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)


class Target(BaseModel):
    """Un produit surveillé (une entrée de `targets.json`)."""

    model_config = {"extra": "forbid"}  # une clé en trop = typo -> on veut le savoir

    name: str
    web_code: str = Field(min_length=3)
    alert_price: float = Field(gt=0)
    # Si absent : on utilisera `Settings.default_min_discount_pct`.
    min_discount_pct: float | None = Field(default=None, ge=0, le=100)
    check_open_box: bool = True

    @field_validator("web_code", mode="before")
    @classmethod
    def _coerce_str(cls, value: object) -> str:
        return str(value).strip()

    def effective_min_discount_pct(self, settings: Settings) -> float:
        if self.min_discount_pct is not None:
            return self.min_discount_pct
        return settings.default_min_discount_pct


def load_targets(path: Path | str | None = None) -> list[Target]:
    """Lit et valide `targets.json`. Lève une erreur si le fichier est absent/invalide.

    Ordre de recherche si `path` n'est pas fourni :
    1. `DATA_DIR/targets.json` (volume monté en conteneur) ;
    2. `PROJECT_ROOT/targets.json` (celui embarqué dans l'image / le repo).
    """
    if path is None:
        path = DEFAULT_TARGETS_PATH
        if not Path(path).exists() and (PROJECT_ROOT / "targets.json").exists():
            path = PROJECT_ROOT / "targets.json"

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Watchlist introuvable : {path} (copie/renseigne targets.json)")

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path} doit contenir une liste JSON d'objets, pas {type(raw).__name__}")

    targets = [Target.model_validate(item) for item in raw]

    seen: set[str] = set()
    for target in targets:
        if target.web_code in seen:
            raise ValueError(f"web_code en double dans {path} : {target.web_code}")
        seen.add(target.web_code)

    return targets
