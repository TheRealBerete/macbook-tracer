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
| 2 | Config `.env` / `targets.json` + détection + anti-spam | à venir |
| 3 | Alertes Telegram | à venir |
| 4 | Ordonnanceur + alerte de panne + logs | à venir |
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

## Utilisation (Sprint 1)

Interroger l'API pour un ou plusieurs SKU / Web Codes :

```bash
python -m bot check 20009307 19914759
python -m bot check 20009307 --detail      # + stock (1 appel API de plus par SKU)
python -m bot -v check 20009307            # logs détaillés (retries...)
```

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
