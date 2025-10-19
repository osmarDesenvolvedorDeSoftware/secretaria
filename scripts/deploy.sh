#!/usr/bin/env bash
set -euo pipefail

BUMP_TYPE=${1:-patch}

case "$BUMP_TYPE" in
  major|minor|patch)
    ;;
  *)
    echo "Uso: $0 [major|minor|patch]" >&2
    exit 1
    ;;
esac

CURRENT_TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "v0.0.0")
IFS='.' read -r CURRENT_MAJOR CURRENT_MINOR CURRENT_PATCH <<<"${CURRENT_TAG#v}"

case "$BUMP_TYPE" in
  major)
    NEXT_MAJOR=$((CURRENT_MAJOR + 1))
    NEXT_MINOR=0
    NEXT_PATCH=0
    ;;
  minor)
    NEXT_MAJOR=$CURRENT_MAJOR
    NEXT_MINOR=$((CURRENT_MINOR + 1))
    NEXT_PATCH=0
    ;;
  patch)
    NEXT_MAJOR=$CURRENT_MAJOR
    NEXT_MINOR=$CURRENT_MINOR
    NEXT_PATCH=$((CURRENT_PATCH + 1))
    ;;
esac

NEW_TAG="v${NEXT_MAJOR}.${NEXT_MINOR}.${NEXT_PATCH}"

echo "Criando tag ${NEW_TAG}" >&2
git tag -a "$NEW_TAG" -m "Release $NEW_TAG"

if git remote get-url origin >/dev/null 2>&1; then
  echo "Enviando tag para origin" >&2
  git push origin "$NEW_TAG"
fi

echo "Construindo imagem da aplicação" >&2
docker compose -f docker-compose.prod.yml build app

echo "Atualizando dependências externas" >&2
docker compose -f docker-compose.prod.yml pull redis postgres prometheus grafana alertmanager || true

echo "Subindo serviços" >&2
docker compose -f docker-compose.prod.yml up -d --remove-orphans

echo "Aplicando migrações" >&2
docker compose -f docker-compose.prod.yml run --rm app alembic upgrade head

echo "Recarregando stack" >&2
docker compose -f docker-compose.prod.yml up -d

if [[ -n "${MONITORING_WEBHOOK_URL:-}" ]]; then
  echo "Notificando canal de monitoramento" >&2
  curl -sS -X POST "$MONITORING_WEBHOOK_URL" \
    -H 'Content-Type: application/json' \
    -d "{\"event\":\"deploy\",\"version\":\"$NEW_TAG\",\"timestamp\":\"$(date -u +"%Y-%m-%dT%H:%M:%SZ")\"}"
else
  echo "Variável MONITORING_WEBHOOK_URL não definida; notificações puladas" >&2
fi

echo "Deploy concluído com a tag $NEW_TAG" >&2
