# Déploiement via Dokploy (Sprint 5)

Le bot est un **worker** (aucun port HTTP) : dans Dokploy on crée une application
de type **Compose**, pas "Application".

Fichiers concernés : `Dockerfile`, `docker-compose.yml`, `.dockerignore`.

---

## 1. Pousser le code

Le repo doit être accessible par Dokploy (GitHub/GitLab, ou dépôt Git self-hosted).

```bash
git push origin main
```

## 2. Créer l'application dans Dokploy

1. **Create Application → Compose**
2. **Provider** : ton repo Git, branche `main`
3. **Compose Path** : `docker-compose.yml`

## 3. Renseigner les variables d'environnement

Onglet **Environment** de l'app Dokploy — coller (au minimum) :

```dotenv
TELEGRAM_BOT_TOKEN=123456:ABC-...
TELEGRAM_CHAT_ID=123456789
POSTAL_CODE=M6N 5G8
INTERVAL_MINUTES=60
TAX_RATE=0.13
```

Toutes les autres clés ont des valeurs par défaut raisonnables (voir `.env.example`).
Dokploy écrit ces variables dans un `.env` que `docker-compose.yml` charge dans le conteneur.

🧠 **Concept — variables d'environnement vs fichier**
En conteneur, on ne commite jamais les secrets. Dokploy les injecte au runtime ;
`pydantic-settings` (`bot/config.py`) les lit directement depuis l'environnement,
exactement comme il lirait un `.env` en local. Même code, deux sources.

## 4. Déployer

Bouton **Deploy**. Dokploy build l'image (`Dockerfile`) et lance le conteneur.

Vérifs :
- onglet **Logs** : tu dois voir `Cycle terminé : N produit(s), ...`
- le conteneur passe **healthy** après le 1er cycle (le `HEALTHCHECK` du Dockerfile
  lit `last_run.txt` via `python -m bot healthcheck`)

Les redéploiements suivants sont automatiques à chaque `git push` (si l'auto-deploy
est activé) ou manuels via **Deploy**.

## 5. Persistance

Le volume nommé `macbook_data` (déclaré dans `docker-compose.yml`) est monté sur
`/data` et contient `state.json`, `bot.log`, `last_run.txt`. Il **survit aux
redéploiements** — l'historique des prix et l'anti-spam ne repartent pas de zéro.

⚠️ Ne pas supprimer ce volume dans Dokploy, sinon `lowest_ever` et l'état d'alerte
sont perdus (le bot re-signalera tout au cycle suivant).

## 6. Modifier la watchlist

`targets.json` est embarqué dans l'image. Pour le changer :

```bash
# éditer targets.json en local
git commit -am "watchlist: ajoute MacBook Pro 14 M4"
git push
```
→ Dokploy redéploie avec la nouvelle liste. L'état des SKU déjà suivis est conservé
(clé = web_code, stockée dans le volume).

## 7. Surveillance externe (recommandé)

Le `HEALTHCHECK` Docker rend le conteneur "unhealthy" si le bot se fige, mais il
faut encore être prévenu. Deux options :

- **Dokploy Notifications** (Discord/Telegram/email) sur l'événement conteneur unhealthy.
- **healthchecks.io** (dead man's switch) : créer un check, puis dans l'app Dokploy
  ajouter une commande périodique, ou plus simple, laisser l'alerte de panne **F2c**
  du bot faire le travail pour les erreurs d'API (elle, elle passe par Telegram).

## Dépannage

| Symptôme | Piste |
|----------|-------|
| Build échoue | Logs de build Dokploy ; tester `docker build .` en local |
| Conteneur "unhealthy" | Logs : le 1er cycle a-t-il tourné ? `start-period` = 3 min |
| Aucune alerte jamais | Variables Telegram bien dans l'onglet Environment ? |
| `HTTP 403` dans les logs | Akamai : augmenter `INTERVAL_MINUTES` |
| État perdu après redéploiement | Le volume `macbook_data` a-t-il été supprimé ? |

---

## Test local de l'image (avant de pousser)

```bash
docker compose build
echo "TELEGRAM_BOT_TOKEN=..." > .env
echo "TELEGRAM_CHAT_ID=..." >> .env
docker compose run --rm bot python -m bot run     # un cycle
docker compose up -d                              # en continu
docker compose logs -f
```
