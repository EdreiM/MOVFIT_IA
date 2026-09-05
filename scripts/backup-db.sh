#!/bin/sh
# Backup do banco de produção da Mônica.
# Roda no servidor, na mesma pasta do docker-compose.prod.yml (ou ajuste o
# COMPOSE_FILE abaixo). Agende via crontab do host, ex:
#   0 3 * * * /caminho/para/scripts/backup-db.sh >> /var/log/movfit-backup.log 2>&1
set -eu

COMPOSE_FILE="$(dirname "$0")/../docker-compose.prod.yml"
BACKUP_DIR="$(dirname "$0")/../backups"
KEEP_DAYS=14

mkdir -p "$BACKUP_DIR"

timestamp=$(date +%Y-%m-%d_%H%M%S)
out_file="$BACKUP_DIR/movfit_${timestamp}.sql.gz"

docker compose -f "$COMPOSE_FILE" exec -T db pg_dump -U monica -d monica --no-owner --clean --if-exists \
  | gzip > "$out_file"

echo "Backup salvo em $out_file"

# Apaga backups com mais de $KEEP_DAYS dias.
find "$BACKUP_DIR" -name "movfit_*.sql.gz" -mtime "+$KEEP_DAYS" -delete
