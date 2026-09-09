"""Modèles de contrat pour les réponses de l'API Best Buy Canada.

Chaque modèle décrit la forme *attendue* d'un bout de réponse JSON. Si Best Buy
change sa structure (champ renommé, type différent), pydantic lève une
`ValidationError` explicite au moment du parsing — c'est le signal qui
déclenchera l'alerte de panne (F2c) plutôt qu'un bug silencieux plus loin.

Voir `docs/bestbuy-api.md` pour les endpoints et exemples de réponses.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator


def clean_text(value: str) -> str:
    """Décode les entités HTML (`&nbsp;`, `&amp;`...) et normalise les espaces insécables."""
    return html.unescape(value).replace("\xa0", " ").strip()

BASE_URL = "https://www.bestbuy.ca"


class _ApiModel(BaseModel):
    """Base commune : accepte les noms camelCase de l'API et ignore les champs inconnus.

    - `populate_by_name` : on peut construire l'objet avec le nom Python OU l'alias API.
    - `extra="ignore"` : l'API renvoie des dizaines de champs qu'on n'utilise pas ;
      on ne veut pas planter à cause d'eux.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class Availability(_ApiModel):
    """Sous-objet `availability` renvoyé par `/api/v2/json/product/<sku>`."""

    is_available_online: bool = Field(default=False, alias="isAvailableOnline")
    online_availability: str | None = Field(default=None, alias="onlineAvailability")
    # Quantité en stock en ligne. Souvent `null` (Best Buy ne l'expose pas toujours).
    online_availability_count: int | None = Field(default=None, alias="onlineAvailabilityCount")
    # "AddToCart" quand le produit est achetable tout de suite.
    button_state: str | None = Field(default=None, alias="buttonState")

    @property
    def is_purchasable(self) -> bool:
        return self.button_state == "AddToCart"


class Product(_ApiModel):
    """Un produit Best Buy, tel que renvoyé par `catalog/query` ou `product/<sku>`.

    `catalog/query` ne renvoie PAS `availability` ni `seller` → ces champs sont
    optionnels et restent à `None` dans ce cas.
    """

    sku: str
    name: str
    # Prix "barré" / de référence. Sert de base au calcul du % de rabais.
    regular_price: float = Field(alias="regularPrice")
    # Prix effectif à surveiller.
    sale_price: float = Field(alias="salePrice")
    # Fin de promo. ISO string dans catalog/query, epoch ms dans search → normalisé ci-dessous.
    sale_end_date: datetime | None = Field(default=None, alias="saleEndDate")

    seller_id: str | None = Field(default=None, alias="sellerId")
    is_marketplace: bool = Field(default=False, alias="isMarketplace")
    is_clearance: bool = Field(default=False, alias="isClearance")
    # Si True : Best Buy masque les économies -> regular_price == sale_price,
    # le "% de rabais" n'a alors aucun sens (cas typique des articles boîte ouverte).
    hide_savings: bool = Field(default=False, alias="hideSavings")

    product_url: str = Field(alias="productUrl")
    availability: Availability | None = None

    @field_validator("sale_end_date", mode="before")
    @classmethod
    def _parse_sale_end_date(cls, value: object) -> object:
        """Accepte une string ISO (`catalog/query`) ou un timestamp epoch ms (`search`)."""
        if value is None or value == "":
            return None
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        return value

    @field_validator("sku", "seller_id", mode="before")
    @classmethod
    def _coerce_to_str(cls, value: object) -> object:
        """L'API renvoie parfois le SKU en nombre — on veut toujours une string."""
        return str(value) if value is not None else None

    @property
    def display_name(self) -> str:
        """Nom nettoyé (sans `&nbsp;` ni entités HTML), prêt à afficher."""
        return clean_text(self.name)

    @property
    def condition_label(self) -> str:
        """"Neuf" / "Boîte ouverte" / "Remis à neuf", déduit du nom du produit.

        Sur Best Buy CA, l'état n'a pas de champ dédié fiable dans `catalog/query` :
        il est encodé dans le libellé ("(Boîte ouverte - Très bon état) MacBook...").
        """
        name = self.display_name.lower()
        if "boîte ouverte" in name or "open box" in name or "open-box" in name:
            return "Boîte ouverte"
        if "remis à neuf" in name or "refurbished" in name or "reconditionné" in name:
            return "Remis à neuf"
        return "Neuf"

    @property
    def is_open_box(self) -> bool:
        return self.condition_label != "Neuf"

    @property
    def full_url(self) -> str:
        """URL absolue (catalog/query renvoie un chemin relatif, product/<sku> l'absolu)."""
        if self.product_url.startswith("http"):
            return self.product_url
        return BASE_URL + self.product_url

    @property
    def sold_by_bestbuy(self) -> bool:
        return self.seller_id in (None, "bbyca")

    @property
    def has_real_discount(self) -> bool:
        """True si `regular_price` est un vrai prix de référence exploitable."""
        return not self.hide_savings and self.regular_price > self.sale_price

    @property
    def discount_amount(self) -> float:
        return round(self.regular_price - self.sale_price, 2)

    @property
    def discount_pct(self) -> float:
        """Pourcentage de rabais vs `regular_price`. 0.0 si pas de vrai rabais."""
        if not self.has_real_discount or self.regular_price <= 0:
            return 0.0
        return round((self.regular_price - self.sale_price) / self.regular_price * 100, 1)


class Offer(_ApiModel):
    """Une offre vendeur pour un SKU, renvoyée par `/api/offers/v1/products/<sku>/offers`."""

    offer_id: str = Field(alias="offerId")
    sku: str
    seller_id: str | None = Field(default=None, alias="sellerId")
    seller_name: str | None = Field(default=None, alias="sellerNameFr")
    regular_price: float = Field(alias="regularPrice")
    sale_price: float = Field(alias="salePrice")
    is_winner: bool = Field(default=False, alias="isWinner")
    is_marketplace: bool = Field(default=False, alias="isMarketplace")

    @field_validator("sku", "seller_id", mode="before")
    @classmethod
    def _coerce_to_str(cls, value: object) -> object:
        return str(value) if value is not None else None


class ConditionProduct(_ApiModel):
    """Un article "état alternatif" (boîte ouverte / remis à neuf) d'un modèle,
    renvoyé par `/api/v2/json/product/<sku>/conditions`."""

    sku: str
    title: str
    product_condition: str = Field(alias="productCondition")
    regular_price: float = Field(alias="regularPrice")
    sale_price: float = Field(alias="salePrice")
    seller_id: str | None = Field(default=None, alias="sellerId")
    seller_name: str | None = Field(default=None, alias="sellerName")
    product_url: str = Field(alias="productUrl")

    @field_validator("sku", "seller_id", mode="before")
    @classmethod
    def _coerce_to_str(cls, value: object) -> object:
        return str(value) if value is not None else None

    @property
    def full_url(self) -> str:
        if self.product_url.startswith("http"):
            return self.product_url
        return BASE_URL + self.product_url
