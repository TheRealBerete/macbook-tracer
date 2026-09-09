# Déploiement sur VPS (Sprint 5)

Cible : un petit VPS Linux (Debian 12 / Ubuntu 24.04). Le bot tourne en continu
comme service `systemd`, redémarre tout seul en cas de crash ou de reboot.

Ressources nécessaires : minuscules (~60 Mo RAM, ~0 CPU). Un **Oracle Cloud
Always Free** (VM.Standard.A1 ou E2.1.Micro) ou le plus petit **Hetzner CX22**
(~4 €/mois) suffisent largement.

---

## 1. Préparer le VPS

```bash
# en root
apt update && apt install -y python3-venv git

# utilisateur dédié, sans shell de login, sans privilèges
adduser --system --group --home /opt/macbook-tracer macbot
```

🧠 **Concept — utilisateur de service**
On ne fait jamais tourner un daemon en `root`. Un compte dédié sans privilèges
limite les dégâts si le process est compromis (même logique qu'un conteneur non-root).

## 2. Récupérer le code

```bash
cd /opt
git clone <URL_DU_DEPOT> macbook-tracer   # ou : rsync depuis ta machine
chown -R macbot:macbot /opt/macbook-tracer
cd macbook-tracer

# venv + dépendances (en tant que macbot)
sudo -u macbot python3 -m venv .venv
sudo -u macbot .venv/bin/pip install -r requirements.txt
```

## 3. Configurer

```bash
sudo -u macbot cp .env.example .env
sudo -u macbot nano .env        # remplir TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, POSTAL_CODE...
sudo -u macbot nano targets.json  # la watchlist

# vérifier que Telegram répond
sudo -u macbot .venv/bin/python -m bot test-telegram

# un cycle manuel pour valider
sudo -u macbot .venv/bin/python -m bot run
```

Permissions : `chmod 600 .env` (lisible seulement par `macbot`).

## 4. Installer le service systemd

```bash
cp deploy/macbook-tracer.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now macbook-tracer

# vérifier
systemctl status macbook-tracer
journalctl -u macbook-tracer -f       # logs en direct
```

💡 `systemctl enable --now` : `enable` = démarrage auto au boot ;
`--now` = démarre aussi tout de suite.

## 5. Surveillance du bot lui-même (optionnel mais recommandé)

Le bot écrit `last_run.txt` après chaque cycle réussi. `deploy/healthcheck.sh`
vérifie que ce fichier est récent.

Créer un check "heartbeat" gratuit sur [healthchecks.io](https://healthchecks.io)
ou UptimeRobot, puis :

```bash
chmod +x deploy/healthcheck.sh
crontab -u macbot -e
```
```cron
*/30 * * * * /opt/macbook-tracer/deploy/healthcheck.sh >/dev/null 2>&1 && curl -fsS https://hc-ping.com/<TON-UUID> >/dev/null
```

Si le bot se fige, le ping s'arrête → le service de monitoring t'alerte par email.
(L'alerte de panne F2c couvre les erreurs d'API ; ce healthcheck couvre le cas où
tout le process est mort.)

## 6. Mises à jour

```bash
cd /opt/macbook-tracer
sudo -u macbot git pull
sudo -u macbot .venv/bin/pip install -r requirements.txt
systemctl restart macbook-tracer
```

## Dépannage

| Symptôme | Piste |
|----------|-------|
| `systemctl status` = failed | `journalctl -u macbook-tracer -n 50` |
| Aucune alerte jamais | `.env` bien rempli ? `python -m bot run` en manuel |
| HTTP 403 dans `bot.log` | Akamai a tiqué : augmenter `INTERVAL_MINUTES`, vérifier `USER_AGENT` |
| Le bot ne redémarre pas au reboot | `systemctl is-enabled macbook-tracer` doit dire `enabled` |
