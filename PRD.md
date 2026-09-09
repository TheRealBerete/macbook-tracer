# PRD - MacBook Price Watcher Bot (Best Buy Canada)

**Version** : 2.0 (décisions finales)
**Date** : 2026-09-09
**Auteur** : BERETE
**Statut** : Cadré, prêt pour développement

> Changelog v1.0 → v2.0 : les choix techniques "suggérés" sont désormais **tranchés**.
> Ajouts majeurs : anti-spam d'alertes, alerte de panne du bot, module de découverte
> Open Box, extraction via API JSON (au lieu de scraping HTML). Refurbished retiré du MVP.
> Config : `.env` (réglages) + `targets.json` (watchlist), au lieu de `config.yaml`.
> Sprint 0 fait : endpoints Best Buy confirmés (sans auth) → voir `docs/bestbuy-api.md`.

---

## 1. Objectif du projet

Développer un bot de veille de prix automatisé qui surveille les MacBook Pro sur Best Buy
Canada, détecte les baisses de prix significatives, et envoie des alertes via Telegram
pour permettre un achat réactif, avec un budget cible de **2 000 $ CAD (hors taxes)**.

---

## 2. Contexte et opportunité

- Le marché canadien des MacBook est volatil : rabais rares mais profonds.
- Black Friday 2026 est la fenêtre d'action principale, mais des "pépites" apparaissent
  toute l'année (Open Box, vendeurs tiers, liquidations).
- Les offres partent en **quelques heures** : une alerte dans l'heure est suffisante,
  inutile de viser la seconde près.

---

## 3. Public cible

- **Utilisateur** : une seule personne (l'auteur), acheteur canadien francophone.
- **Cas d'usage** : recevoir une alerte Telegram, décider d'acheter en moins d'une heure.

---

## 4. Décisions finales (verrouillées)

| Sujet | Décision | Pourquoi |
|---|---|---|
| **Langage** | Python 3.10+ | Écosystème scraping / HTTP / scheduling mature |
| **Hébergement** | Conteneur Docker déployé via **Dokploy** (sur le serveur de l'utilisateur) | Doit tourner 24/7, surtout le matin de Black Friday. L'utilisateur gère déjà ses déploiements avec Dokploy |
| **Exécution** | Process Python long-running + `APScheduler` dans le conteneur ; `restart: unless-stopped` + `HEALTHCHECK` Docker | Redémarre si crash ou reboot ; heartbeat `last_run.txt` pour détecter un bot figé |
| **Intervalle** | 60 min par défaut, configurable (`interval_minutes`) | Compromis réactivité / risque de blocage. Le délai de détection reste sous le KPI |
| **Extraction** | API JSON interne de Best Buy (`bestbuy.ca/api/...`) | Best Buy est une SPA React : le prix n'est pas dans le HTML brut. L'API renvoie du JSON propre (prix, prix régulier, dispo, vendeur, Open Box). Pas de navigateur headless à maintenir |
| **Fallback extraction** | Aucun en V1 ; à la place, **alerte de panne** si l'API casse | Playwright = lourd (Chromium, RAM, lenteur). On l'ajoutera seulement si l'API se ferme |
| **Périmètre** | Watchlist de Web Codes **+ module de découverte** Open Box / catégorie | La section 2 parle de pépites inconnues : une watchlist fixe seule ne les verrait jamais |
| **Déclenchement alerte** | `prix ≤ alert_price` **OU** `baisse% ≥ min_discount_pct` (vs prix régulier Best Buy) | L'absolu garantit que rien sous le budget ne passe inaperçu ; le % attrape les grosses promos même sur un modèle plus cher |
| **Prix de référence pour le %** | Champ `regularPrice` renvoyé par l'API Best Buy | Source objective et disponible à chaque appel |
| **Anti-spam** | État par produit ; ré-alerte seulement si nouvelle baisse ≥ `realert_delta` $, ou rappel max 1×/24 h | Sinon le bot enverrait une alerte à chaque passage tant que le prix reste bas |
| **Open Box** | Vérifié automatiquement pour chaque produit surveillé (pas de config séparée) | Simplicité : une seule ligne de config par modèle, le bot regarde neuf + Open Box |
| **Refurbished** | Retiré de la V1 | Best Buy Canada n'en vend quasiment pas ; ce serait une source séparée (apple.ca) |
| **Telegram** | `POST`/`GET` HTTP direct sur `api.telegram.org` (pas de librairie), envoi ET commandes (long-polling `getUpdates`) | `python-telegram-bot` (asyncio, lourde) évitée : ~200 lignes suffisent et restent synchrones comme le reste du bot |
| **Configuration** | `.env` (secrets + réglages globaux) + `targets.json` (watchlist) | `.env` = standard pour les secrets, plat et sans dépendance lourde. La watchlist est une liste d'objets → `targets.json` (JSON natif, pas de YAML ni d'indentation piégeuse) |
| **Stockage d'état** | `state.json` (prix courants + état d'alerte, géré par le bot) | Pas de base de données nécessaire tant qu'on ne fait pas de graphes d'historique |
| **Proxies** | Aucun en V1 | Usage perso, 1 appel API toutes les 60 min, User-Agent réaliste : risque de blocage négligeable |
| **Taxes** | Estimation informative seulement (`tax_rate: 0.13` par défaut) | Best Buy affiche les prix HT ; le message montre aussi un prix TTC estimé pour info |

---

## 5. Portée fonctionnelle

### 5.1. V1 (MVP — obligatoire)

| # | Fonctionnalité | Description |
|---|---------------|-------------|
| F1 | **Lecture produit via API** | Watchlist entière en 1 appel `catalog/query` (≤ 20 SKU) : `salePrice`, `regularPrice`, `saleEndDate`, vendeur, flags. Appel `product/<sku>` en complément (stock, détail) uniquement pour les SKU qui passent le seuil. |
| F2 | **Détection de baisse** | Neuf : alerte si `salePrice ≤ alert_price` OU `baisse% ≥ min_discount_pct` vs `regularPrice`. Boîte ouverte (`hideSavings` / `regularPrice == salePrice`) : alerte si `salePrice ≤ alert_price` OU `salePrice < lowest_ever`. |
| F2b | **Anti-spam d'alertes** | Mémorise `last_alerted_price` / `last_alerted_at` par produit. Ré-alerte uniquement si le prix baisse encore d'au moins `realert_delta` $, ou une fois par 24 h en rappel si toujours sous seuil. |
| F2c | **Alerte de panne** | Après 3 échecs API consécutifs sur un produit (erreur HTTP, JSON inattendu), envoie un message Telegram ⚠️. Une seule alerte de panne par produit jusqu'à rétablissement. |
| F3 | **Alertes Telegram** | Message formaté : modèle, prix HT, prix TTC estimé, rabais $ et %, état (neuf / Open Box + niveau), vendeur, position vs budget, lien. Niveau visuel 🟢/🟡/🔴. |
| F4 | **Configuration** | `.env` : secrets + réglages globaux (token Telegram, chat_id, intervalle, taux de taxe, seuils par défaut, paramètres découverte). `targets.json` : la watchlist (liste de produits + seuils par produit). |
| F5 | **Exécution programmée** | `APScheduler` déclenche un cycle complet toutes les `interval_minutes`. |
| F10 | **Module de découverte Open Box** | Scanne périodiquement la catégorie MacBook Pro et les offres Open Box. Signale toute annonce dont le prix ≤ `discovery.max_price` ET baisse ≥ `discovery.min_discount_pct`, même si le Web Code n'est pas dans la watchlist. Dé-doublonnage via `state.json`. |
| F11 | **Log local** | Fichier `bot.log` : chaque cycle, chaque prix relevé, chaque alerte envoyée, chaque erreur. Pour diagnostiquer après coup. |

### 5.2. V2 (optionnel — plus tard)

| # | Fonctionnalité | Description |
|---|---------------|-------------|
| F6 | Suivi multi-vendeurs | Amazon Canada, Staples, Costco. |
| F7 | Historique des prix | Migration vers SQLite + génération de graphes de tendance. |
| F8 | Détection d'erreur de prix | Baisse > 40 % vs régulier → alerte priorité maximale, message spécifique. |
| ~~F9~~ | ~~Commandes Telegram~~ | ✅ **Fait (Sprint 7)** : `/help /list /add /remove /setprice /check /run /status` par long-polling `getUpdates` — pas de `python-telegram-bot`, pas de webhook. `bot/commands.py`. |
| F12 | Vérification panier | Confirmer que le prix affiché = prix réel en simulant un "ajout au panier". |
| F13 | Intervalle variable par plage de dates | Ex. 5 min pendant la semaine du Black Friday, 60 min le reste de l'année. |

---

## 6. Spécifications techniques

### 6.1. Architecture

```
                    ┌─────────────────────────────────────────┐
                    │  APScheduler (cycle toutes les 60 min)   │
                    └────────────────┬────────────────────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              ▼                                             ▼
   ┌────────────────────┐                    ┌─────────────────────────┐
   │  Watchlist module  │                    │  Discovery module (F10) │
   │  targets.json      │                    │  search + conditions    │
   └─────────┬──────────┘                    └───────────┬─────────────┘
             │        appels HTTP GET (JSON)             │
             ▼                                           ▼
   ┌─────────────────────────────────────────────────────────────────┐
   │              Best Buy Canada — API JSON interne                  │
   │   /api/v1/catalog/query · /api/v2/json/product/<sku>             │
   │   /api/v2/json/search   · /api/v2/json/product/<sku>/conditions  │
   └─────────────────────────────────────────────────────────────────┘
             │                                           │
             ▼                                           ▼
   ┌─────────────────────────────────────────────────────────────────┐
   │   Price evaluator : compare au seuil + au % + à l'état d'alerte  │
   │                  (lit / écrit  state.json)                       │
   └────────────────────────────────┬────────────────────────────────┘
                                    │ (si alerte à envoyer)
                                    ▼
                    ┌─────────────────────────────────┐
                    │  Telegram sender (POST HTTP)     │
                    └────────────────┬────────────────┘
                                     ▼
                            [ Utilisateur ]
```

### 6.2. Stack

| Composant | Choix | Notes |
|-----------|-------|-------|
| Langage | Python 3.10+ | |
| Requêtes HTTP | `httpx` (ou `requests`) | `httpx` gère HTTP/2 et les timeouts proprement |
| Parsing | `json` (stdlib) | Pas de HTML à parser grâce à l'API |
| Ordonnanceur | `APScheduler` | `BlockingScheduler`, un seul job périodique (`max_instances=1`, `coalesce`) |
| Config / secrets | `.env` + `pydantic-settings` (local) / variables d'env injectées par Dokploy (prod) | Même code lit les deux ; `DATA_DIR` pointe vers un volume en conteneur |
| Watchlist | `targets.json` + `json` (stdlib) | Liste de produits ; aucune dépendance |
| Alertes | `httpx` → `api.telegram.org/bot<token>/sendMessage` | Pas de librairie Telegram en V1 |
| Validation config | `pydantic` v2 (`BaseSettings` pour le `.env`, `BaseModel` pour `targets.json`) | Vérifie au démarrage que tout est bien typé ; message d'erreur clair sinon |
| Déploiement | Docker + Dokploy (app *Compose*) | `Dockerfile` slim non-root, `docker-compose.yml` avec volume `macbook_data:/data` |
| Supervision | `restart: unless-stopped` + `HEALTHCHECK` Docker (`python -m bot healthcheck`) | Conteneur marqué *unhealthy* si aucun cycle depuis 3 h |
| Secrets | Variables d'env Dokploy (prod) / `.env` non commité (local) | `.gitignore` + `.dockerignore` : `.env`, `state.json`, `bot.log`, `*.har` |

### 6.3. Endpoints Best Buy (Sprint 0 — confirmés via capture HAR du 2026-09-09)

> Détail complet, champs et exemples : **`docs/bestbuy-api.md`**.
> Résultat : API JSON utilisable **sans authentification, sans cookie, sans clé**.
> Non documentée / non versionnée → tests de contrat `pydantic` + alerte de panne (F2c).

| Besoin | Endpoint | Notes |
|--------|----------|-------|
| **Watchlist : prix + prix régulier + vendeur, en lot** | `GET /api/v1/catalog/query?ids=<sku,sku,...>&lang=fr-CA` | **≤ 20 SKU/appel** ; pas de code postal ; `salePrice` / `regularPrice` / `saleEndDate` / `sellerId` / `isClearance` / `hideSavings` |
| Détail + stock d'un produit | `GET /api/v2/json/product/<sku>?currentRegion=ON&include=all&lang=fr-CA` | `availability.onlineAvailabilityCount`, `availability.buttonState`, `seller.name`, `specs[]` (état neuf/boîte ouverte) |
| Offres multi-vendeurs d'un SKU | `GET /api/offers/v1/products/<sku>/offers?postalCode=M6N%205G8` | toutes les offres + `isWinner` ; **code postal requis** |
| Variantes boîte ouverte / remis à neuf d'un modèle | `GET /api/v2/json/product/<sku>/conditions?currentRegion=ON&include=all&lang=fr-CA` | `alternateConditionProducts` groupé par état ; chaque état = un SKU distinct |
| Découverte / recherche (F10) | `GET /api/v2/json/search?query=macbook+pro&lang=fr-CA&currentRegion=ON&page=1&pageSize=24` | 24/page, `totalPages` ; `saleEndDate` en **epoch ms** ici ; filtre `categoryid=12746019` à tester |
| Disponibilité en lot (optionnel) | `GET /ecomm-api/availability/products?skus=<sku\|sku>&postalCode=M6N5G8&...` | statut expédition seulement, pas de prix |

Catégorie MacBook Pro = `12746019`. Collection Outlet MacBook = `426671`.

Headers (voir `docs/bestbuy-api.md` §1) : `User-Agent` Chrome réaliste,
`Accept: application/json, text/plain, */*`, `Accept-Language: fr-CA,fr;q=0.9`,
`Referer: https://www.bestbuy.ca/fr-ca`. Code postal fixé en config (`POSTAL_CODE`).
Backoff exponentiel sur 403/429 (Akamai Bot Manager actif mais non déclenché à 1 appel/h).

**Impact sur la conception :**
- La watchlist tient en **1 appel `catalog/query`** (si ≤ 20 produits) au lieu d'un appel par produit.
- On n'appelle `product/<sku>` (stock, détail) **que pour les produits qui passent le seuil**.
- Pour un article **boîte ouverte**, `regularPrice == salePrice` : le `min_discount_pct`
  n'a pas de sens → comparer au **plus bas jamais vu** (`lowest_ever` dans `state.json`)
  ou à un `alert_price` absolu.

### 6.4. Configuration : `.env` + `targets.json`

**`.env`** — secrets et réglages globaux (une clé = une valeur, pas de structure) :

```dotenv
# --- Secrets ---
TELEGRAM_BOT_TOKEN=123456:ABC-def...
TELEGRAM_CHAT_ID=123456789

# --- Scraping ---
INTERVAL_MINUTES=60
USER_AGENT=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36
POSTAL_CODE=M6N 5G8            # requis par les endpoints offers / availability
REQUEST_DELAY_MIN=1
REQUEST_DELAY_MAX=3

# --- Alerting ---
TAX_RATE=0.13                 # estimation TTC informative (0.13 = TVH Ontario)
REALERT_DELTA=50              # $ de baisse supplémentaire avant de ré-alerter
DAILY_REMINDER=true           # rappel 1x/24h si toujours sous seuil
FAILURE_THRESHOLD=3           # échecs API consécutifs avant alerte de panne

# --- Discovery (module F10) ---
DISCOVERY_ENABLED=true
DISCOVERY_CATEGORY=macbook-pro
DISCOVERY_MAX_PRICE=2000      # HT
DISCOVERY_MIN_DISCOUNT_PCT=15

# Seuils par défaut si absents d'un target
DEFAULT_MIN_DISCOUNT_PCT=15
```

**`targets.json`** — la watchlist (liste d'objets, édition manuelle facile) :

```json
[
  {
    "name": "MacBook Pro 14 M4 24Go/1To",
    "web_code": "18619896",
    "alert_price": 1999,
    "min_discount_pct": 15,
    "check_open_box": true
  },
  {
    "name": "MacBook Pro 16 M4 Pro 24Go/512Go",
    "web_code": "19343847",
    "alert_price": 2100,
    "check_open_box": true
  }
]
```

`min_discount_pct` est optionnel par target : si absent, on prend `DEFAULT_MIN_DISCOUNT_PCT`.

Simplifications vs v1.0 : `max_price` + `alert_threshold` fusionnés en `alert_price` ;
`url` supprimé (reconstruit depuis `web_code`) ; `state` supprimé (Open Box géré
automatiquement) ; plus de YAML ni de dépendance `PyYAML`.

### 6.5. `state.json` (géré par le bot, ne pas éditer à la main)

```json
{
  "18619896": {
    "current_price": 2299.99,
    "regular_price": 2499.99,
    "lowest_ever": 2199.99,
    "last_seen_at": "2026-09-09T14:00:00-04:00",
    "last_alerted_price": null,
    "last_alerted_at": null,
    "consecutive_failures": 0,
    "failure_alerted": false
  }
}
```

---

## 7. Format des alertes Telegram

### Alerte de baisse de prix

```
🔴 ALERTE PRIX - MACBOOK PRO

📦 Modèle : MacBook Pro 14" M4 (24 Go / 1 To)
💰 Prix : 1 949,99 $ HT  (≈ 2 203,49 $ TTC est.)
📉 Rabais : 550 $  (-22,0 % vs 2 499,99 $)
📊 Plus bas jamais vu par le bot : 1 949,99 $  ← nouveau plancher
🏷️ État : Neuf
🏪 Vendeur : Best Buy
🎯 Budget cible : 2 000 $ HT  →  ✅ DANS LE BUDGET

🔗 https://www.bestbuy.ca/en-ca/product/18619896
```

### Alerte de découverte (F10)

```
💎 PÉPITE OPEN BOX DÉTECTÉE

📦 MacBook Pro 14" M3 Pro (18 Go / 512 Go)
💰 Prix : 1 799,99 $ HT  (≈ 2 033,99 $ TTC est.)
📉 -28 % vs 2 499,99 $
🏷️ État : Open Box - Excellent
🔗 https://www.bestbuy.ca/en-ca/product/XXXXXXXX
```

### Alerte de panne (F2c)

```
⚠️ BOT EN ERREUR

Le suivi de "MacBook Pro 14 M4 24Go/1To" (18619896) échoue depuis 3 cycles.
Dernière erreur : HTTP 403 sur /api/offers/v1/...
→ Vérifier si l'API Best Buy a changé.
```

### Niveaux visuels (badge en tête de message)

| Badge | Condition |
|-------|-----------|
| 🟢 | Baisse entre 5 % et 15 % |
| 🟡 | Baisse entre 15 % et 25 % |
| 🔴 | Baisse ≥ 25 % **OU** prix ≤ `alert_price` |

---

## 8. Contraintes et risques

| Risque | Impact | Mitigation retenue |
|--------|--------|--------------------|
| L'API interne change de forme / se ferme | Élevé | F2c (alerte de panne) + `bot.log`. Playdev Playwright reste une option V2 |
| Blocage IP (403 / CAPTCHA) | Moyen | Intervalle 60 min, User-Agent réaliste, `catalog/query` en lot, backoff exponentiel. Pas de proxies tant que non nécessaire |
| Prix affiché ≠ prix au panier | Moyen | Accepté en V1 (faux positif rare). F12 en V2 |
| Conteneur down | Moyen | `restart: unless-stopped` + `HEALTHCHECK` Docker + heartbeat `last_run.txt` (notif Dokploy possible) |
| Fuite du token Telegram | Moyen (spam du bot) | Token dans `.env`, jamais commité, `.gitignore` strict |
| `state.json` corrompu | Faible | Écriture atomique (fichier temp + `os.replace`), sauvegarde du précédent |

---

## 9. Critères de succès (KPI)

- ✅ **Délai baisse réelle → alerte reçue** : ≤ 60 min (= l'intervalle).
- ✅ **Taux de détection** : 100 % des baisses > 10 % sur les produits de la watchlist,
  mesuré en rejouant l'historique `bot.log`.
- ✅ **Faux positifs** : < 5 % des alertes (pas d'alerte pour une fluctuation < 5 %,
  pas de doublon grâce à F2b).
- ✅ **Robustesse** : le bot tourne 30 jours d'affilée sans intervention manuelle
  (hors changement d'API).
- ✅ **Objectif métier** : achat d'un MacBook Pro ≤ 2 000 $ HT avant fin 2026.

---

## 10. Points à trancher pendant le développement (pas bloquants)

1. ~~ID de catégorie / forme des réponses API~~ → **fait au Sprint 0**, voir `docs/bestbuy-api.md`.
2. **Filtre `categoryid` sur `/search`** : confirmer qu'il fonctionne (sinon découverte
   par `query=macbook pro` paginé) — Sprint 6.
3. **`sortBy` de `/search`** : valeurs exactes (`priceLowToHigh` ?) — Sprint 6.
4. ~~Healthcheck~~ → `HEALTHCHECK` Docker + `python -m bot healthcheck` (heartbeat `last_run.txt`). Reste : brancher une notification Dokploy sur *unhealthy*.
5. **Produits en rupture** : alerter quand un produit surveillé redevient disponible ?
   (proposé : oui, alerte 🔵 « de retour en stock », via `availability.buttonState`).
6. **Comparaison boîte ouverte** : `lowest_ever` seul, ou aussi vs prix du neuf équivalent
   (nécessite de lier chaque boîte ouverte à un SKU neuf de référence) ?

---

## 11. Découpage de développement suggéré

| Sprint | Livrable | Statut |
|--------|----------|--------|
| 0 | Reco API, endpoints + champs documentés dans `docs/bestbuy-api.md` | ✅ fait |
| 1 | Client API (`catalog/query` + `product/<sku>`) + modèles de contrat + tests | ✅ fait |
| 2 | `.env` + `targets.json` + `state.json` + watchlist + logique F2 / F2b / F2c | ✅ fait |
| 3 | Envoi Telegram (F3) + formatage des messages + niveaux visuels | ✅ fait (test live à faire par l'utilisateur) |
| 4 | `APScheduler` (F5) + `bot.log` (F11) + heartbeat | ✅ fait |
| 5 | Déploiement Docker / Dokploy + healthcheck | ✅ `Dockerfile`, `docker-compose.yml`, `docs/deploy.md` ; déploiement à faire par l'utilisateur dans Dokploy |
| 6 | Module de découverte Open Box (F10) | ✅ fait |
| 7 | Commandes Telegram (F9) — long-polling `getUpdates` | ✅ fait |

Code : paquet `bot/`, 75 tests. CLI `python -m bot {check,run,watch,test-telegram,healthcheck}`.
En prod via Dokploy.
