# Image du bot de veille de prix. Worker sans port HTTP.
FROM python:3.12-slim

# Utilisateur non-root (moindre privilège).
RUN useradd --create-home --uid 1000 macbot

WORKDIR /app

# Les dépendances d'abord -> le cache Docker est réutilisé tant que
# requirements.txt ne change pas.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Puis le code + la watchlist par défaut.
COPY bot/ ./bot/
COPY targets.json ./targets.json

# Dossier des données mutables -> monté comme volume (voir docker-compose.yml).
ENV DATA_DIR=/data \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1
RUN mkdir -p /data && chown -R macbot:macbot /data /app

USER macbot
VOLUME ["/data"]

# Docker (et Dokploy) marquent le conteneur "unhealthy" si aucun cycle depuis 3 h.
HEALTHCHECK --interval=5m --timeout=15s --start-period=3m --retries=3 \
    CMD ["python", "-m", "bot", "healthcheck", "--max-age", "180"]

STOPSIGNAL SIGTERM
CMD ["python", "-m", "bot", "watch"]
