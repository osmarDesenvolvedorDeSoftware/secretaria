#!/usr/bin/env bash
set -euo pipefail

BUMP_TYPE="patch"
DOMAIN=""
TENANT_ID=""
TENANT_SLUG=""

usage() {
  cat <<'EOF'
Uso: scripts/deploy.sh [major|minor|patch] [--domain exemplo.com] [--tenant-id 42] [--tenant-slug empresa]

Opções:
  --domain       Domínio base utilizado para provisionar chat.<tenant>.domínio e api.<tenant>.domínio
  --tenant-id    Identificador numérico do tenant que receberá o domínio
  --tenant-slug  Slug textual opcional utilizado para compor os subdomínios
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    major|minor|patch)
      BUMP_TYPE="$1"
      shift
      ;;
    --domain)
      DOMAIN="${2:-}"
      shift 2
      ;;
    --tenant-id)
      TENANT_ID="${2:-}"
      shift 2
      ;;
    --tenant|--tenant-slug)
      TENANT_SLUG="${2:-}"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Parâmetro desconhecido: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "$TENANT_SLUG" && -n "$TENANT_ID" ]]; then
  TENANT_SLUG="tenant${TENANT_ID}"
fi

if [[ -z "$TENANT_SLUG" && -n "$DOMAIN" ]]; then
  TENANT_SLUG="tenant"
fi

if [[ -z "$DOMAIN" && -n "$TENANT_ID" ]]; then
  echo "Aviso: --tenant-id informado sem --domain. Provisionamento de domínio será pulado." >&2
fi

if [[ -z "$BUMP_TYPE" ]]; then
  BUMP_TYPE="patch"
fi

case "$BUMP_TYPE" in
  major|minor|patch)
    ;;
  *)
    echo "Tipo de versionamento inválido: $BUMP_TYPE" >&2
    usage >&2
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

if [[ -n "$DOMAIN" && -n "$TENANT_ID" ]]; then
  LOG_DIR="deployments"
  mkdir -p "$LOG_DIR"
  CHAT_DOMAIN="chat.${TENANT_SLUG}.${DOMAIN}"
  API_DOMAIN="api.${TENANT_SLUG}.${DOMAIN}"
  TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  echo "Provisionando domínios ${CHAT_DOMAIN} e ${API_DOMAIN}" >&2
  cat <<EOF >>"${LOG_DIR}/domain_status.log"
${TIMESTAMP} tenant=${TENANT_ID} domain=${DOMAIN} chat=${CHAT_DOMAIN} api=${API_DOMAIN}
EOF
  python - <<PY
from datetime import datetime

from redis import Redis

from app.config import settings
from app.services.tenancy import namespaced_key

tenant_id = int(${TENANT_ID})
tenant_slug = "${TENANT_SLUG}"
base_domain = "${DOMAIN}"
chat_domain = "${CHAT_DOMAIN}"
api_domain = "${API_DOMAIN}"
now = datetime.utcnow().isoformat()

client = Redis.from_url(settings.redis_url, decode_responses=True)
client.hset(
    namespaced_key(tenant_id, "domains"),
    mapping={
        "tenant_slug": tenant_slug,
        "base_domain": base_domain,
        "chat_domain": chat_domain,
        "api_domain": api_domain,
        "domain_status": "active",
        "ssl_status": "active",
        "updated_at": now,
    },
)
client.hset(
    namespaced_key(tenant_id, "provisioning"),
    mapping={
        "domain_status": "ready",
        "domain_updated_at": now,
        "ssl_status": "ready",
        "ssl_updated_at": now,
    },
)
PY
else
  echo "Provisionamento de domínio ignorado (parâmetros ausentes)." >&2
fi

if [[ -n "${MONITORING_WEBHOOK_URL:-}" ]]; then
  echo "Notificando canal de monitoramento" >&2
  curl -sS -X POST "$MONITORING_WEBHOOK_URL" \
    -H 'Content-Type: application/json' \
    -d "{\"event\":\"deploy\",\"version\":\"$NEW_TAG\",\"timestamp\":\"$(date -u +"%Y-%m-%dT%H:%M:%SZ")\"}"
else
  echo "Variável MONITORING_WEBHOOK_URL não definida; notificações puladas" >&2
fi

echo "Deploy concluído com a tag $NEW_TAG" >&2
