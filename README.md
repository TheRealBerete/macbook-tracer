# MacBook Price Watcher Bot

Bot de veille de prix : surveille des MacBook Pro sur **Best Buy Canada**, détecte les
baisses significatives et envoie une alerte **Telegram**. Budget cible : 2 000 $ CAD (HT).

- Spécifications complètes : [`PRD.md`](PRD.md)
- API Best Buy (endpoints, champs) : [`docs/bestbuy-api.md`](docs/bestbuy-api.md)

## État d'avancement

| Sprint | Contenu | Statut |
|--------|---------|--------|
| 0 | Reconnaissance de l'API Best Buy | ✅ fait |
| 1 | Client API + modèles de contrat + tests | ✅ fait |
| 2 | Config `.env` / `targets.json` + détection + anti-spam + `state.json` | ✅ fait |
| 3 | Alertes Telegram | ✅ fait |
| 4 | Ordonnanceur + alerte de panne + logs | ✅ fait |
| 6 | Module de découverte Open Box | ✅ fait |
| 5 | Déploiement (Docker / Dokploy) | ✅ en prod |
| 7 | Commandes Telegram (F9) | ✅ fait |
| 8 | Black Friday : critères M4–M6 / 16 Go / 512 Go, reconditionné, variantes, mode rapide, erreur de prix, stock, `/deals` `/history` | ✅ fait |

## Installation

```bash
python -m venv .venv
# Windows PowerShell :
.venv\Scripts\Activate.ps1
# Git Bash :
source .venv/Scripts/activate

pip install -r requirements.txt
cp .env.example .env   # puis remplir les valeurs
```

### Configurer Telegram

1. Sur Telegram, parler à **@BotFather** → `/newbot` → récupérer le **token**.
2. Envoyer un message à ton nouveau bot, puis récupérer ton **chat_id** :
   parler à **@userinfobot** (il renvoie ton id), ou ouvrir
   `https://api.telegram.org/bot<TOKEN>/getUpdates` et lire `chat.id`.
3. Renseigner `TELEGRAM_BOT_TOKEN` et `TELEGRAM_CHAT_ID` dans `.env`.
4. Vérifier : `python -m bot test-telegram`

## Utilisation

Interroger l'API pour un ou plusieurs SKU / Web Codes (debug) :

```bash
python -m bot check 20009307 19914759
python -m bot check 20009307 --detail      # + stock (1 appel API de plus par SKU)
```

Lancer un cycle de surveillance complet sur `targets.json` :

```bash
python -m bot run             # cycle + envoi Telegram des alertes
python -m bot run --no-send   # cycle sans envoi (affichage console seulement)
python -m bot -v run          # avec logs détaillés
```

Le cycle met à jour `state.json` (prix vus, plus bas historique, anti-spam) et envoie
chaque alerte sur Telegram si `.env` est configuré.

### Surveillance en continu

```bash
python -m bot watch      # 1 cycle immédiat, puis toutes les INTERVAL_MINUTES (défaut 60)
```

- écrit dans `bot.log` (rotation : 5 × 1 Mo)
- écrit `last_run.txt` après chaque cycle réussi (heartbeat pour un moniteur externe)
- un cycle qui plante est loggé mais n'arrête pas la boucle
- s'arrête proprement sur Ctrl+C et sur `SIGTERM` (utilisé par Docker / Dokploy)

Chaque cycle inclut aussi la **découverte** (F10) : toutes les pages de la recherche
« macbook pro » (jusqu'à `DISCOVERY_MAX_PAGES`), filtrées sur les critères d'achat :

- puce **M4 → M6** (`DISCOVERY_MIN_CHIP` / `DISCOVERY_MAX_CHIP`) ;
- **≥ 16 Go de RAM** et **≥ 512 Go** de stockage, lus dans le nom du produit
  (`MIN_RAM_GB` / `MIN_STORAGE_GB`) — si illisibles, l'alerte dit « ⚠️ specs à vérifier » ;
- neuf, boîte ouverte **et remis à neuf** (avec le niveau : Excellent / Très bon / Bon / Passable) ;
- **sous le `BUDGET` : tout est signalé**, même sans rabais ; au-dessus (jusqu'à
  `DISCOVERY_MAX_PRICE`) : seulement boîte ouverte ou rabais ≥ `DISCOVERY_MIN_DISCOUNT_PCT`.

Pour chaque produit de la watchlist, le bot regarde aussi :

- ses **variantes boîte ouverte / remis à neuf** (signalées si ≤ son seuil) — même
  quand le neuf est en rupture ;
- son **stock** : alerte 🔵 quand il redevient achetable (`STOCK_CHECK_ENABLED`).

Et partout : baisse ≥ `PRICE_ERROR_PCT` (40 %) = 🚨 **erreur de prix probable**, envoyée
en premier. Si une vague de pépites arrive d'un coup, les 5 meilleures partent en détail
et le reste dans un seul récapitulatif.

### Mode Black Friday

Du `RUSH_START` (20 nov.) au `RUSH_END` (2 déc.), heure de Toronto, le bot passe
automatiquement à **un cycle toutes les `RUSH_INTERVAL_MINUTES` (10 min)**, puis revient
à `INTERVAL_MINUTES`. Un message Telegram annonce le début et la fin. Entre deux
requêtes, pause aléatoire de `REQUEST_DELAY_MIN`–`REQUEST_DELAY_MAX` s (anti-blocage).

### Commandes Telegram

Quand `watch` tourne et que Telegram est configuré, le bot écoute aussi les
commandes (long-polling, aucun port à ouvrir). Seul le `TELEGRAM_CHAT_ID`
autorisé est pris en compte.

| Commande | Effet |
|----------|-------|
| `/list` | Affiche la watchlist |
| `/add <web_code> <prix> [nom]` | Ajoute un produit (vérifie qu'il existe sur Best Buy) |
| `/remove <web_code>` | Retire un produit |
| `/setprice <web_code> <prix>` | Change le seuil d'alerte |
| `/check <web_code>` | Prix actuel d'un produit, sans l'ajouter |
| `/deals [n]` | Les n offres les moins chères vues en 24 h qui passent les critères |
| `/history <web_code>` | Historique des changements de prix d'un produit |
| `/criteria` | Critères de recherche actifs |
| `/run` | Force un cycle tout de suite |
| `/status` | Watchlist, intervalle, âge du dernier cycle |
| `/help` | Liste les commandes |

Les ajouts/retraits sont écrits dans `DATA_DIR/targets.json` (le volume en prod)
→ ils survivent aux redéploiements.

## Tests

```bash
pytest
```

Les tests utilisent des réponses figées (`tests/fixtures/`, capturées depuis le vrai
site) — **aucun appel réseau**.

## Déploiement

Via **Dokploy** (app type *Compose*) — procédure complète dans
[`docs/deploy.md`](docs/deploy.md). En résumé :

- `Dockerfile` : image slim, user non-root, `HEALTHCHECK` = `python -m bot healthcheck`
- `docker-compose.yml` : volume `macbook_data:/data` pour persister `state.json`
- secrets renseignés dans l'onglet *Environment* de Dokploy (jamais commités)

Test local de l'image :

```bash
docker compose build
docker compose run --rm bot python -m bot run
docker compose up -d && docker compose logs -f
```

## Structure

```
bot/
  bestbuy.py       Client de l'API Best Buy (retry, backoff, search)
  models.py        Modèles pydantic = contrat sur les réponses API
  config.py        Settings (.env) + Target + load_targets
  state.py         state.json (écriture atomique)
  detector.py      Déclenchement + anti-spam + alerte de panne
  discovery.py     Module de découverte (F10)
  criteria.py      Critères d'achat : puce, RAM, stockage
  telegram.py      Envoi Telegram (POST sendMessage)
  format.py        Mise en forme HTML des alertes
  runner.py        full_cycle() = watchlist + découverte
  scheduler.py     watch() = APScheduler (cycles) + écoute des commandes
  commands.py      Commandes Telegram (/add, /list, /run...)
  __main__.py      CLI (python -m bot ...)
docs/
  bestbuy-api.md   Référence des endpoints Best Buy
  deploy.md        Déploiement Dokploy
tests/
  fixtures/        Réponses JSON réelles figées
Dockerfile · docker-compose.yml
```
