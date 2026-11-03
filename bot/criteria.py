"""Critères d'achat communs : puce, RAM, stockage.

Les specs ne sont pas des champs de l'API : on les lit dans le libellé du produit
(« Apple M4 / 16GB RAM / 512GB SSD », « M5 d'Apple/RAM 16 Go/SSD 1 To »).
Utilisé par la découverte (F10) et par les variantes boîte ouverte / remis à neuf
des produits de la watchlist.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bot.config import Settings
from bot.models import chip_from_text, specs_from_text


@dataclass
class SpecCheck:
    ok: bool
    reason: str | None = None          # raison du rejet (logs debug)
    chip: str | None = None
    ram_gb: int | None = None
    storage_gb: int | None = None
    unknown_specs: bool = False        # RAM ou stockage illisible → « à vérifier »

    @property
    def summary(self) -> str | None:
        parts = []
        if self.chip:
            parts.append(self.chip)
        parts.append(f"{self.ram_gb} Go" if self.ram_gb else "RAM ?")
        parts.append(_storage_label(self.storage_gb) if self.storage_gb else "SSD ?")
        return " · ".join(parts)


def _storage_label(gb: int) -> str:
    if gb >= 1024 and gb % 1024 == 0:
        return f"{gb // 1024} To"
    return f"{gb} Go"


def chip_generation(chip: str | None) -> int | None:
    if not chip:
        return None
    match = re.match(r"M(\d)", chip)
    return int(match.group(1)) if match else None


def check_specs(name: str, settings: Settings) -> SpecCheck:
    chip = chip_from_text(name)
    ram, storage = specs_from_text(name)
    check = SpecCheck(ok=True, chip=chip, ram_gb=ram, storage_gb=storage)

    gen = chip_generation(chip)
    if gen is None:
        if settings.discovery_apple_silicon_only:
            return _reject(check, "pas de puce Apple Silicon détectée")
    elif not (settings.discovery_min_chip <= gen <= settings.discovery_max_chip):
        return _reject(
            check,
            f"puce {chip} hors M{settings.discovery_min_chip}–M{settings.discovery_max_chip}",
        )

    if settings.min_ram_gb and ram is not None and ram < settings.min_ram_gb:
        return _reject(check, f"RAM {ram} Go < {settings.min_ram_gb} Go")
    if settings.min_storage_gb and storage is not None and storage < settings.min_storage_gb:
        return _reject(check, f"stockage {storage} Go < {settings.min_storage_gb} Go")

    missing = (settings.min_ram_gb and ram is None) or (
        settings.min_storage_gb and storage is None
    )
    if missing:
        if not settings.allow_unknown_specs:
            return _reject(check, "specs illisibles dans le nom")
        check.unknown_specs = True

    return check


def _reject(check: SpecCheck, reason: str) -> SpecCheck:
    check.ok = False
    check.reason = reason
    return check
