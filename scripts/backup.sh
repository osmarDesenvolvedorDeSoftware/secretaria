#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "DATABASE_URL não definido" >&2
  exit 1
fi

BACKUP_DIR=${1:-backups}
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
mkdir -p "$BACKUP_DIR"

SQL_FILE="$BACKUP_DIR/secretaria_${TIMESTAMP}.sql"
ARCHIVE_FILE="$BACKUP_DIR/secretaria_${TIMESTAMP}.tar.gz"

pg_dump "$DATABASE_URL" > "$SQL_FILE"

tar -czf "$ARCHIVE_FILE" \
  "$SQL_FILE" \
  docker-compose.prod.yml \
  requirements.txt \
  templates \
  app

rm -f "$SQL_FILE"

echo "Backup concluído em $ARCHIVE_FILE"
