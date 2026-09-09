#!/usr/bin/env bash
# Vérifie que le bot a fait un cycle récemment (heartbeat last_run.txt).
# Sortie 0 = OK, 1 = figé. À brancher sur cron + un ping vers healthchecks.io
# ou UptimeRobot (mode "heartbeat / dead man's switch").
#
# Exemple crontab (toutes les 30 min) :
#   */30 * * * * /opt/macbook-tracer/deploy/healthcheck.sh && curl -fsS https://hc-ping.com/<uuid> >/dev/null

set -euo pipefail

HEARTBEAT="${1:-/opt/macbook-tracer/last_run.txt}"
MAX_AGE_MINUTES="${2:-180}"   # 3 h : ~3 cycles ratés à l'intervalle par défaut (60 min)

if [[ ! -f "$HEARTBEAT" ]]; then
    echo "CRITICAL: pas de heartbeat ($HEARTBEAT)"
    exit 1
fi

age_seconds=$(( $(date +%s) - $(date -r "$HEARTBEAT" +%s) ))
if (( age_seconds > MAX_AGE_MINUTES * 60 )); then
    echo "CRITICAL: dernier cycle il y a $(( age_seconds / 60 )) min (> $MAX_AGE_MINUTES)"
    exit 1
fi

echo "OK: dernier cycle il y a $(( age_seconds / 60 )) min"
exit 0
