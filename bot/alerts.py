"""Représentation d'une alerte, indépendante du canal d'envoi.

Le Sprint 2 produit ces objets ; le Sprint 3 les formate pour Telegram.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class AlertKind(str, Enum):
    PRICE_DROP = "price_drop"        # un produit surveillé a baissé
    DISCOVERY = "discovery"          # pépite trouvée hors watchlist (Sprint 6)
    BACK_IN_STOCK = "back_in_stock"  # produit surveillé de nouveau dispo
    FAILURE = "failure"              # le bot n'arrive plus à lire l'API (F2c)


class AlertLevel(str, Enum):
    GREEN = "green"    # baisse modérée
    YELLOW = "yellow"  # baisse importante
    RED = "red"        # baisse critique OU sous le budget cible
    CRITICAL = "critical"  # erreur de prix probable (F8) : agir tout de suite


def level_for(
    discount_pct: float, *, under_budget: bool, price_error_pct: float | None = None
) -> AlertLevel:
    if price_error_pct is not None and discount_pct >= price_error_pct:
        return AlertLevel.CRITICAL
    if under_budget or discount_pct >= 25:
        return AlertLevel.RED
    if discount_pct >= 15:
        return AlertLevel.YELLOW
    return AlertLevel.GREEN


@dataclass
class Alert:
    kind: AlertKind
    name: str
    reason: str                      # explication courte (pour les logs)
    sku: str | None = None
    price: float | None = None       # prix HT
    regular_price: float | None = None
    discount_amount: float = 0.0
    discount_pct: float = 0.0
    lowest_ever: float | None = None
    level: AlertLevel | None = None
    seller: str | None = None
    condition: str | None = None     # "Neuf" / "Boîte ouverte" / ...
    stock: str | None = None
    url: str | None = None
    tax_rate: float = 0.0
    budget: float | None = None      # budget cible HT -> ligne « 🎯 Budget »
    specs: str | None = None         # « M4 · 16 Go · 512 Go »
    specs_unverified: bool = False   # RAM / stockage illisibles dans le nom
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def price_with_tax(self) -> float | None:
        if self.price is None:
            return None
        return round(self.price * (1 + self.tax_rate), 2)
