"""Client de l'API JSON interne de Best Buy Canada.

Pas d'authentification requise. On envoie juste des headers de navigateur réalistes.
Endpoints et champs : voir `docs/bestbuy-api.md`.

Le client :
- regroupe les appels prix par lots de 20 SKU (`catalog/query`) ;
- réessaie avec un backoff exponentiel sur 403 / 429 / 5xx (Akamai Bot Manager) ;
- lève `BestBuyApiError` sur échec définitif ou réponse illisible → l'appelant
  (le bot) transforme ça en alerte de panne (F2c).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import httpx
from pydantic import ValidationError

from bot.models import ConditionProduct, Offer, Product


@dataclass
class SearchPage:
    products: list[Product]
    current_page: int
    total_pages: int

logger = logging.getLogger(__name__)

BASE_URL = "https://www.bestbuy.ca"
CATALOG_QUERY_MAX_IDS = 20  # plafond observé dans les réponses (`pageSize: 20`)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
)

# Codes HTTP qui valent la peine d'être réessayés (blocage temporaire / anti-bot).
RETRYABLE_STATUS = {403, 429, 500, 502, 503, 504}


class BestBuyApiError(RuntimeError):
    """Échec d'appel à l'API Best Buy (réseau, HTTP non récupérable, JSON invalide)."""


def _chunked(items: Sequence[str], size: int) -> Iterable[Sequence[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


class BestBuyClient:
    def __init__(
        self,
        *,
        lang: str = "fr-CA",
        region: str = "ON",
        postal_code: str | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 15.0,
        max_retries: int = 4,
        backoff_base: float = 2.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.lang = lang
        self.region = region
        self.postal_code = postal_code
        self.max_retries = max_retries
        self.backoff_base = backoff_base

        headers = {
            "User-Agent": user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": f"{lang},{lang.split('-')[0]};q=0.9",
            "Referer": f"{BASE_URL}/{lang.lower()}",
        }
        # `client` injectable pour les tests (transport bouchonné).
        self._client = client or httpx.Client(
            base_url=BASE_URL, headers=headers, timeout=timeout, follow_redirects=True
        )
        self._owns_client = client is None

    # --- gestion du cycle de vie (context manager) -------------------------------------

    def __enter__(self) -> "BestBuyClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # --- appel HTTP bas niveau avec retry ---------------------------------------------

    def _get_json(self, path: str, params: dict[str, str] | None = None) -> object:
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                response = self._client.get(path, params=params)
            except httpx.HTTPError as exc:  # timeout, DNS, connexion refusée...
                last_error = exc
                logger.warning("GET %s : erreur réseau (tentative %d) : %s", path, attempt, exc)
            else:
                if response.status_code == 200:
                    try:
                        return response.json()
                    except ValueError as exc:  # corps non-JSON (page de challenge Akamai ?)
                        raise BestBuyApiError(
                            f"Réponse non-JSON de {path} (HTTP 200, "
                            f"{response.headers.get('content-type')})"
                        ) from exc

                if response.status_code not in RETRYABLE_STATUS:
                    raise BestBuyApiError(f"GET {path} → HTTP {response.status_code}")

                last_error = BestBuyApiError(f"HTTP {response.status_code}")
                logger.warning(
                    "GET %s → HTTP %d (tentative %d/%d)",
                    path, response.status_code, attempt, self.max_retries,
                )

            if attempt < self.max_retries:
                delay = self.backoff_base ** attempt  # 2s, 4s, 8s, ...
                time.sleep(delay)

        raise BestBuyApiError(f"GET {path} a échoué après {self.max_retries} tentatives") from last_error

    # --- endpoints ------------------------------------------------------------------

    def get_prices(self, skus: Sequence[str]) -> list[Product]:
        """Prix + infos de base pour une liste de SKU (`/api/v1/catalog/query`).

        Découpe automatiquement en lots de 20. Un SKU introuvable est simplement
        absent de la réponse (pas d'erreur).
        """
        if not skus:
            return []

        products: list[Product] = []
        for batch in _chunked(list(skus), CATALOG_QUERY_MAX_IDS):
            payload = self._get_json(
                "/api/v1/catalog/query",
                params={"ids": ",".join(batch), "lang": self.lang},
            )
            products.extend(self._parse_items(payload, "catalog/query"))
        return products

    def get_product(self, sku: str) -> Product:
        """Détail complet d'un produit, avec stock (`/api/v2/json/product/<sku>`)."""
        payload = self._get_json(
            f"/api/v2/json/product/{sku}",
            params={"currentRegion": self.region, "include": "all", "lang": self.lang},
        )
        return self._parse_model(Product, payload, f"product/{sku}")

    def get_offers(self, sku: str) -> list[Offer]:
        """Toutes les offres vendeur d'un SKU (`/api/offers/v1/products/<sku>/offers`)."""
        params = {}
        if self.postal_code:
            params["postalCode"] = self.postal_code
        payload = self._get_json(f"/api/offers/v1/products/{sku}/offers", params=params)
        if not isinstance(payload, list):
            raise BestBuyApiError(f"offers/{sku} : liste attendue, reçu {type(payload).__name__}")
        return [self._parse_model(Offer, item, f"offers/{sku}") for item in payload]

    def get_conditions(self, sku: str) -> list[ConditionProduct]:
        """Variantes boîte ouverte / remis à neuf d'un modèle (`.../conditions`)."""
        payload = self._get_json(
            f"/api/v2/json/product/{sku}/conditions",
            params={"currentRegion": self.region, "include": "all", "lang": self.lang},
        )
        if not isinstance(payload, dict):
            raise BestBuyApiError(f"conditions/{sku} : objet attendu")
        groups = payload.get("alternateConditionProducts") or {}
        results: list[ConditionProduct] = []
        for entries in groups.values():
            for entry in entries:
                results.append(self._parse_model(ConditionProduct, entry, f"conditions/{sku}"))
        return results

    def search(self, query: str, *, page: int = 1, page_size: int = 24) -> SearchPage:
        """Recherche catalogue (`/api/v2/json/search`) — utilisé par la découverte (F10)."""
        payload = self._get_json(
            "/api/v2/json/search",
            params={
                "query": query,
                "lang": self.lang,
                "currentRegion": self.region,
                "page": str(page),
                "pageSize": str(page_size),
                "sortBy": "",
            },
        )
        if not isinstance(payload, dict) or "products" not in payload:
            raise BestBuyApiError("search : champ 'products' absent de la réponse")
        products = [
            self._parse_model(Product, item, "search") for item in payload["products"]
        ]
        return SearchPage(
            products=products,
            current_page=int(payload.get("currentPage", page)),
            total_pages=int(payload.get("totalPages", page)),
        )

    # --- helpers de parsing -------------------------------------------------------

    @staticmethod
    def _parse_items(payload: object, source: str) -> list[Product]:
        if not isinstance(payload, dict) or "items" not in payload:
            raise BestBuyApiError(f"{source} : champ 'items' absent de la réponse")
        return [BestBuyClient._parse_model(Product, item, source) for item in payload["items"]]

    @staticmethod
    def _parse_model(model: type, raw: object, source: str):
        try:
            return model.model_validate(raw)
        except ValidationError as exc:
            raise BestBuyApiError(
                f"{source} : la réponse ne correspond plus au contrat {model.__name__}\n{exc}"
            ) from exc
