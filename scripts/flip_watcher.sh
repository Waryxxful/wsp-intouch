#!/bin/bash
# Watcher de flip de cliente activo para wsp_demo -- corre en el HOST via cron,
# NUNCA dentro del contenedor (el contenedor no tiene ni necesita el socket de
# Docker, ver admin_panel/cliente_flip.py). Instalar con:
#   (crontab -l 2>/dev/null; echo "* * * * * /home/admincrm/wsp_demo/scripts/flip_watcher.sh") | crontab -
set -euo pipefail

REPO_DIR="/home/admincrm/wsp_demo"
FLAG="$REPO_DIR/.flip_request.json"
LOG="$REPO_DIR/.flip_watcher.log"
LOCK="$REPO_DIR/.flip_watcher.lock"

exec 9>"$LOCK"
flock -n 9 || exit 0

[ -f "$FLAG" ] || exit 0

TARGET=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('target_cliente','?'))" "$FLAG" 2>/dev/null || echo "?")
echo "$(date -u +%FT%TZ) flip request detectado -> $TARGET" >> "$LOG"

# Se borra ANTES del recreate a proposito: si el recreate falla, no queremos
# un loop de reintentos infinito cada minuto -- el fallo queda en el log.
rm -f "$FLAG"

cd "$REPO_DIR"
if docker compose up -d --force-recreate web >> "$LOG" 2>&1; then
    echo "$(date -u +%FT%TZ) recreate OK -> $TARGET" >> "$LOG"
else
    echo "$(date -u +%FT%TZ) recreate FALLO -> $TARGET" >> "$LOG"
fi
