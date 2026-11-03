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
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Dossier des données mutables (state.json, bot.log, last_run.txt, targets.json).
# En local : la racine du projet. En conteneur : un volume monté (DATA_DIR=/data)
# pour que l'état survive aux redéploiements.
DATA_DIR = Path(os.environ.get("DATA_DIR", PROJECT_ROOT))

DEFAULT_TARGETS_PATH = DATA_DIR / "targets.json"
DEFAULT_STATE_PATH = DATA_DIR / "state.json"

# Les promos Best Buy CA basculent à l'heure de l'Est.
STORE_TZ = ZoneInfo("America/Toronto")


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

    # Budget cible (HT) : sert au badge 🔴 et à la ligne « 🎯 Budget » des alertes.
    budget: float = 2000.0
    # Baisse ≥ ce % vs prix régulier = erreur de prix probable -> alerte 🚨 prioritaire.
    price_error_pct: float = 40.0
    # Surveille le stock de chaque produit de la watchlist (alerte 🔵 retour en stock).
    stock_check_enabled: bool = True

    # --- Découverte (Sprint 6) ---
    discovery_enabled: bool = True
    discovery_max_price: float = 2000.0
    discovery_min_price: float = 900.0         # sous ce prix = accessoire / annonce douteuse
    # Au-dessus du budget (et ≤ max_price), il faut au moins ce rabais pour alerter.
    # Sous le budget : tout produit qui passe les filtres de specs est signalé.
    discovery_min_discount_pct: float = 15.0
    discovery_apple_silicon_only: bool = True  # puce M1…M9 (pas d'Intel)
    discovery_include_refurbished: bool = True  # remis à neuf inclus (souvent les meilleurs prix)
    # Génération de puce acceptée : M4 → M6 par défaut.
    discovery_min_chip: int = 4
    discovery_max_chip: int = 6
    # Specs minimales (lues dans le nom du produit). 0 = pas de filtre.
    min_ram_gb: int = 16
    min_storage_gb: int = 512
    # Specs illisibles dans le nom : True = on signale quand même (marqué « à vérifier »).
    allow_unknown_specs: bool = True
    # Requêtes de recherche, séparées par des virgules.
    discovery_queries: str = "macbook pro"
    discovery_max_pages: int = 15              # 24 résultats/page
    # Au-delà de N pépites dans un même cycle : les N meilleures en détail + 1 récap.
    discovery_max_alerts_per_cycle: int = 5

    # --- Mode Black Friday (F13) : cycles plus rapprochés sur une plage de dates ---
    rush_enabled: bool = True
    rush_start: str = "11-20"                  # MM-JJ, inclus (heure de Toronto)
    rush_end: str = "12-02"                    # MM-JJ, inclus
    rush_interval_minutes: int = 10

    @property
    def telegram_ready(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def queries(self) -> list[str]:
        return [q.strip() for q in self.discovery_queries.split(",") if q.strip()]

    def in_rush(self, now: datetime | None = None) -> bool:
        """True si `now` tombe dans la plage Black Friday (bornes incluses)."""
        if not self.rush_enabled:
            return False
        local = (now or datetime.now(tz=timezone.utc)).astimezone(STORE_TZ)
        today = (local.month, local.day)
        start = _parse_month_day(self.rush_start)
        end = _parse_month_day(self.rush_end)
        if start <= end:
            return start <= today <= end
        return today >= start or today <= end   # plage à cheval sur le 1er janvier

    def effective_interval(self, now: datetime | None = None) -> int:
        """Intervalle (min) à appliquer maintenant : rapide en période Black Friday."""
        if self.in_rush(now):
            return max(1, min(self.rush_interval_minutes, self.interval_minutes))
        return max(1, self.interval_minutes)


def _parse_month_day(raw: str) -> tuple[int, int]:
    month, day = raw.strip().split("-")
    return int(month), int(day)


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


def save_targets(targets: list[Target], path: Path | str | None = None) -> Path:
    """Écrit la watchlist dans `DATA_DIR/targets.json` (écriture atomique).

    Utilisé par les commandes Telegram (/add, /remove...). On écrit toujours dans
    `DATA_DIR` : en conteneur c'est le volume, donc les modifications survivent
    aux redéploiements (et prennent le pas sur le `targets.json` de l'image).
    """
    path = Path(path or DEFAULT_TARGETS_PATH)
    payload = [t.model_dump(exclude_none=True) for t in targets]
    text = json.dumps(payload, ensure_ascii=False, indent=2)

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path
