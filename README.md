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
| 5 | Déploiement VPS (`systemd`) | à venir |
| 6 | Module de découverte Open Box | à venir |

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
- s'arrête proprement sur Ctrl+C et sur `SIGTERM` (utilisé par `systemd` — Sprint 5)

## Tests

```bash
pytest
```

Les tests utilisent des réponses figées (`tests/fixtures/`, capturées depuis le vrai
site) — **aucun appel réseau**.

## Structure

```
bot/
  bestbuy.py     Client de l'API Best Buy (retry, backoff)
  models.py      Modèles pydantic = contrat sur les réponses API
  __main__.py    CLI (python -m bot ...)
docs/
  bestbuy-api.md Référence des endpoints
tests/
  fixtures/      Réponses JSON réelles figées
```
