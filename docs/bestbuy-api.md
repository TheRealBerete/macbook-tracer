# Best Buy Canada — API interne (résultats Sprint 0)

**Source** : capture réseau `www.bestbuy.ca.har` (2026-09-09), page produit
« Boîte ouverte MacBook Pro 14" M3 Pro » (SKU `20009307`) + recherche « macbook pro m3 pro ».

**Verdict** : l'API est utilisable directement. **Aucune authentification, aucun cookie,
aucune clé API** nécessaires sur les endpoints qui nous intéressent. Réponses JSON propres.

> ⚠️ Best Buy utilise **Akamai Bot Manager** (visible via les POST vers des URLs
> obfusquées type `/NQ4_rl3P/.../...`) + Dynatrace. À basse fréquence (1 passage/heure)
> avec des headers réalistes, aucun blocage observé. Si ça se durcit → voir « Risques » plus bas.

---

## 1. Headers à envoyer (tous les appels)

```
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36
Accept: application/json, text/plain, */*
Accept-Language: fr-CA,fr;q=0.9
Referer: https://www.bestbuy.ca/fr-ca
```

Pas de `Cookie`, pas d'`Authorization`. Le header `x-dtpc` vu dans la capture = tracking
Dynatrace, **pas requis**.

---

## 2. Endpoints retenus

### 2.1. 🎯 Prix + infos de base, en LOT — `catalog/query`

```
GET https://www.bestbuy.ca/api/v1/catalog/query?ids=<sku1>,<sku2>,...&lang=fr-CA
```

- **Jusqu'à 20 SKU par appel** (`pageSize` plafonné à 20 dans la réponse).
- Ne demande **pas** de code postal.
- Champs utiles par item :

| Champ | Exemple | Usage |
|-------|---------|-------|
| `sku` | `"19775246"` | identifiant |
| `name` | `"Portable Chromebook..."` | libellé alerte |
| `regularPrice` | `549.99` | prix de référence (pour le %) |
| `salePrice` | `229.99` | **prix courant** à surveiller |
| `saleEndDate` | `"2026-09-11T07:00:00Z"` ou `null` | fin de promo |
| `sellerId` | `"bbyca"` (Best Buy) ou id numérique (marketplace) | vendeur |
| `isClearance` | `false` | liquidation |
| `isMarketplace` | `false` | vendu par un tiers |
| `hideSavings` | `false` | si `true`, `regularPrice` == `salePrice` (pas de vrai rabais) |
| `productUrl` | `/fr-CA/produit/.../19775246` | lien (préfixer `https://www.bestbuy.ca`) |
| `customerRating` / `customerRatingCount` | `4.52` / `61` | info |

> **C'est l'endpoint principal de la watchlist.** Un seul appel pour toute la liste
> (tant qu'elle fait ≤ 20 produits ; sinon découper en paquets de 20).

### 2.2. Détail d'UN produit — `product/<sku>`

```
GET https://www.bestbuy.ca/api/v2/json/product/<sku>?currentRegion=ON&include=all&lang=fr-CA
```

Tout ce que renvoie `catalog/query`, **plus** :

| Champ | Exemple | Usage |
|-------|---------|-------|
| `availability.isAvailableOnline` | `true` | en vente en ligne |
| `availability.onlineAvailability` | `"OnlineOnly"` / `"InStock"` / `"OutOfStock"` | état stock |
| `availability.onlineAvailabilityCount` | `29` | **quantité dispo** (signal « stock limité ») |
| `availability.buttonState` | `"AddToCart"` | achetable maintenant |
| `seller.name` | `"GainSaver"` | nom vendeur lisible |
| `specs[]` | `{"name":"État du produit","value":"Boîte ouverte"}` | neuf / boîte ouverte / remis à neuf |
| `isProductOnSale` | `false` | en promo |

Utilisé pour : enrichir une alerte (stock, nom vendeur) et confirmer un prix.

### 2.3. Offres multi-vendeurs d'un produit — `offers/<sku>`

```
GET https://www.bestbuy.ca/api/offers/v1/products/<sku>/offers?postalCode=M6N%205G8
```

Liste **toutes les offres** (marketplace inclus) pour un SKU. Chaque offre :
`sellerId`, `sellerNameFr`, `regularPrice`, `salePrice`, `isWinner` (offre affichée par défaut),
`isMarketplace`, `warranty`, `isOnSale`, `isOnClearance`.

Utile si un produit neuf a plusieurs vendeurs et qu'on veut le moins cher, pas seulement
le « winner ». Demande un code postal (`postalCode`, avec l'espace encodé `%20`).

### 2.4. 🎁 Boîte ouverte / remis à neuf d'un modèle — `conditions/<sku>`

```
GET https://www.bestbuy.ca/api/v2/json/product/<sku>/conditions?currentRegion=ON&include=all&lang=fr-CA
```

À partir d'un SKU « famille », renvoie `alternateConditionProducts` groupé par état
(`"boîte ouverte"`, `"remis à neuf (très bon état)"`, ...). Chaque entrée :
`sku`, `title`, `regularPrice`, `salePrice`, `sellerName`, `productCondition`, `productUrl`.

> Sur Best Buy CA, **chaque article boîte ouverte est un SKU distinct** avec son propre prix.
> Pour ces articles `regularPrice == salePrice` et `hideSavings: true` → le « % de rabais »
> vs `regularPrice` ne veut rien dire. Comparer plutôt au **prix du neuf équivalent** ou
> au **plus bas jamais vu** (voir PRD §2.5).

### 2.5. 🔍 Recherche / découverte — `search`

```
GET https://www.bestbuy.ca/api/v2/json/search?query=macbook+pro&lang=fr-CA&currentRegion=ON&page=1&pageSize=24&sortBy=
```

- 24 résultats/page, `totalPages` fourni → pagination possible.
- Chaque `products[]` : mêmes champs que `catalog/query` (`sku`, `regularPrice`,
  `salePrice`, `saleEndDate` en **epoch ms** ici, `sellerId`, `isMarketplace`,
  `isClearance`, `name`, `productUrl`).
- `facets[]` disponibles : « Rabais », « Statut », « Marque », « Vendeur »... (filtrage
  possible mais noms de facettes à mapper).
- `sortBy` : valeurs à confirmer (`priceLowToHigh` probable).

**Pour la découverte** : requête `query=macbook pro` (et/ou `query=macbook pro open box`),
paginer, garder les items avec `salePrice <= discovery.max_price`, dé-doublonner via
`state.json`. Le filtrage par `categoryid=12746019` (voir §3) est probablement supporté
mais **non confirmé par la capture** → à tester au sprint 6.

### 2.6. Disponibilité en LOT — `availability/products`

```
GET https://www.bestbuy.ca/ecomm-api/availability/products?skus=<sku1>|<sku2>|...&postalCode=M6N5G8&accept-language=fr-CA&accept=application%2Fvnd.bestbuy.simpleproduct.v1%2Bjson
```

Séparateur `|` entre SKU. Par SKU : `shipping.status`
(`InStockOnlineOnly` / `OutOfStock` / ...), `shipping.purchasable`, `pickup`, `sellerId`.
Pas de prix, pas de quantité. Optionnel — `product/<sku>` donne déjà mieux.

---

## 3. Identifiants utiles

| Nom | ID | Note |
|-----|-----|------|
| Catégorie « MacBook Pro d'Apple » | `12746019` | `productCount` ~1200 (inclut boîte ouverte / marketplace) |
| Catégorie « MacBook » (parent) | `12746015` | |
| Collection « Aubaines sur les MacBook » | `70082` | eventType `collection` |
| Collection « Aubaines d'Entrepôt sur les MacBook » (Outlet) | `426671` | 👀 pépites liquidation |
| Endpoint catégorie (métadonnées) | `GET /api/v2/json/category/<id>?lang=fr-CA` | donne `name`, `productCount`, hiérarchie |

Vendeurs vus dans la capture : `bbyca` = Best Buy ; `1226538` = GainSaver (boîte ouverte) ;
`69817948` = Electronic Lifecycle Solutions (remis à neuf).

---

## 4. Ce que ça change pour le PRD

| Sujet PRD | Confirmé / ajusté |
|-----------|-------------------|
| Extraction via API JSON | ✅ Confirmé, sans auth ni cookie |
| Watchlist | Utiliser **`catalog/query` en lot** (≤ 20 SKU/appel) → 1 seul appel HTTP au lieu de N |
| Prix courant / référence | `salePrice` vs `regularPrice`. Attention : `hideSavings:true` → pas de vrai rabais |
| Stock | `product/<sku>` → `availability.onlineAvailabilityCount` (déclencher un badge « stock limité ») |
| Open Box (F1) | Chaque boîte ouverte = SKU distinct. Récupérables via `conditions/<sku>` à partir d'un SKU famille, OU via `search` |
| Comparaison % pour Open Box | Ne PAS comparer à `regularPrice` (identique). Comparer au neuf équivalent ou au plus bas historique |
| Découverte (F10) | `search?query=macbook pro` paginé. `categoryid=12746019` à tester |
| Code postal | Requis seulement pour `offers/<sku>` et `availability/products`. Fixer un code postal en config (ex. `M6N 5G8`) |

---

## 5. Risques / limites observés

- **Akamai Bot Manager** actif. Non déclenché à basse fréquence dans la capture, mais
  peut renvoyer un 403 / une page de challenge si on tape trop vite ou avec un UA suspect.
  Mitigation : 1 passage/heure, headers réalistes, `catalog/query` en lot pour minimiser
  le nombre de requêtes, backoff exponentiel sur 403/429, et l'alerte de panne (F2c).
- **`saleEndDate`** : format ISO string dans `catalog/query`, **epoch ms** dans `search`.
  Normaliser au parsing.
- **API non versionnée publiquement** : `/api/v1/`, `/api/v2/`, `/api/v3/` cohabitent.
  Peut changer sans préavis → tests de contrat (pydantic) + F2c.
- `catalog/query` ne donne **pas** la dispo ni le stock → un appel `product/<sku>`
  en complément pour les produits qui passent le seuil (pas pour toute la watchlist).
