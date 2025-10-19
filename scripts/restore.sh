#!/usr/bin/env bash
set -euo pipefail

ARCHIVE=${1:-}
if [[ -z "$ARCHIVE" ]]; then
  echo "Uso: $0 <arquivo.tar.gz>" >&2
  exit 1
fi

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "DATABASE_URL não definido" >&2
  exit 1
fi

TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"' EXIT

tar -xzf "$ARCHIVE" -C "$TEMP_DIR"
SQL_FILE=$(find "$TEMP_DIR" -name 'secretaria_*.sql' -print -quit)

if [[ -z "$SQL_FILE" ]]; then
  echo "Arquivo SQL não encontrado no backup" >&2
  exit 1
fi

psql "$DATABASE_URL" < "$SQL_FILE"

echo "Restauração concluída."
