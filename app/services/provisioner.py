from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

import structlog
from redis import Redis
from rq import Queue
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Company, Plan, Subscription
from app.services.auth import encode_jwt
from app.services.tenancy import namespaced_key, queue_name_for_company


LOGGER = structlog.get_logger().bind(service="provisioner")


def _as_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:  # pragma: no cover - validação defensiva
        raise ValueError("invalid_price") from exc


def _ensure_iterable(value: Any) -> Iterable[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    return []


def _normalize_slug(value: str | None) -> str:
    base = (value or "").strip().lower()
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in base)
    cleaned = cleaned.strip("-_") or "tenant"
    return cleaned[:48]


def _tenant_schema(company_id: int) -> str:
    return f"tenant_{company_id}"


def _tenant_redis_url(base_url: str, company_id: int) -> str:
    # Redis URLs têm formato redis://host:port/db ou redis://host:port/""
    # Vamos substituir o caminho por um identificador exclusivo do tenant.
    from urllib.parse import urlparse, urlunparse

    parsed = urlparse(base_url)
    path = f"/tenant_{company_id}"
    if parsed.query:
        # Preserve query string intacta
        new_path = path
    else:
        new_path = path
    updated = parsed._replace(path=new_path)
    return urlunparse(updated)


@dataclass
class PlanPayload:
    name: str
    description: str | None = None
    price: Decimal = Decimal("0")
    message_limit: int = 1000
    token_limit: int = 500000
    features: list[str] = field(default_factory=list)


@dataclass
class ProvisioningPayload:
    company_name: str
    domain: str
    billing_cycle: str = "mensal"
    plan: PlanPayload | None = None
    base_domain: str | None = None
    tenant_slug: str | None = None

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> "ProvisioningPayload":
        name = str(payload.get("name") or "").strip()
        domain = str(payload.get("domain") or "").strip().lower()
        if not name or not domain:
            raise ValueError("name_and_domain_required")

        billing_cycle = str(payload.get("billing_cycle") or "mensal").strip().lower()
        plan_data = payload.get("plan") or {}
        if not isinstance(plan_data, dict):
            plan_data = {}

        plan_name = str(plan_data.get("name") or name).strip()
        if not plan_name:
            plan_name = f"Plano {name}"

        message_limit_raw = plan_data.get("limite_mensagens") or plan_data.get("message_limit")
        try:
            message_limit = int(message_limit_raw) if message_limit_raw is not None else 1000
        except (TypeError, ValueError):
            message_limit = 1000
        if message_limit <= 0:
            message_limit = 1000

        token_limit_raw = plan_data.get("limite_tokens") or plan_data.get("token_limit")
        try:
            token_limit = int(token_limit_raw) if token_limit_raw is not None else 500000
        except (TypeError, ValueError):
            token_limit = 500000
        if token_limit <= 0:
            token_limit = 500000

        price = _as_decimal(plan_data.get("price") or plan_data.get("preco") or 0)
        description = plan_data.get("description") or plan_data.get("descricao")
        plan_payload = PlanPayload(
            name=plan_name,
            description=str(description).strip() if description else None,
            price=price,
            message_limit=message_limit,
            token_limit=token_limit,
            features=list(_ensure_iterable(plan_data.get("features"))),
        )

        base_domain = payload.get("base_domain")
        tenant_slug = payload.get("tenant_slug")

        return ProvisioningPayload(
            company_name=name,
            domain=domain,
            billing_cycle=billing_cycle or "mensal",
            plan=plan_payload,
            base_domain=str(base_domain).strip().lower() if base_domain else None,
            tenant_slug=_normalize_slug(str(tenant_slug)) if tenant_slug else None,
        )


class ProvisionerService:
    """Serviço responsável por provisionar automaticamente novos tenants."""

    def __init__(
        self,
        session: Session,
        engine: Engine,
        redis_client: Redis,
        *,
        logger: logging.Logger | structlog.stdlib.BoundLogger = LOGGER,
    ) -> None:
        self.session = session
        self.engine = engine
        self.redis = redis_client
        self.logger = logger

    # ---------------------- utilidades privadas ----------------------
    def _update_status(self, company_id: int, step: str, status: str, **extra: Any) -> None:
        key = namespaced_key(company_id, "provisioning")
        payload: dict[str, Any] = {f"{step}_status": status, f"{step}_updated_at": datetime.utcnow().isoformat()}
        for name, value in extra.items():
            payload[f"{step}_{name}"] = value
        try:
            self.redis.hset(key, mapping={k: str(v) for k, v in payload.items()})
        except Exception:  # pragma: no cover - Redis opcional
            self.logger.warning("provisioning_status_update_failed", company_id=company_id, step=step)

    def _store_infrastructure_metadata(
        self,
        company_id: int,
        *,
        schema: str,
        redis_url: str,
        queue_name: str,
    ) -> None:
        key = namespaced_key(company_id, "infrastructure")
        data = {
            "schema": schema,
            "redis_url": redis_url,
            "queue": queue_name,
        }
        try:
            self.redis.hset(key, mapping=data)
        except Exception:  # pragma: no cover
            self.logger.warning("provisioning_infra_metadata_failed", company_id=company_id)

    def _store_domain_metadata(
        self,
        company_id: int,
        *,
        base_domain: str | None,
        tenant_slug: str,
    ) -> dict[str, str]:
        if base_domain:
            chat_domain = f"chat.{tenant_slug}.{base_domain}"
            api_domain = f"api.{tenant_slug}.{base_domain}"
        else:
            chat_domain = f"chat.{tenant_slug}.{company_id}.local"
            api_domain = f"api.{tenant_slug}.{company_id}.local"
        key = namespaced_key(company_id, "domains")
        payload = {
            "tenant_slug": tenant_slug,
            "base_domain": base_domain or "",
            "chat_domain": chat_domain,
            "api_domain": api_domain,
            "ssl_status": "pending",
            "domain_status": "pending",
        }
        try:
            self.redis.hset(key, mapping=payload)
        except Exception:  # pragma: no cover
            self.logger.warning("provisioning_domain_metadata_failed", company_id=company_id)
        return payload

    # ---------------------- métodos públicos ----------------------
    def provision(self, payload: ProvisioningPayload) -> dict[str, Any]:
        # Verificar se domínio já está em uso
        existing = self.session.execute(
            select(Company).where(Company.domain == payload.domain)
        ).scalar_one_or_none()
        if existing is not None:
            raise ValueError("domain_in_use")

        company = Company(name=payload.company_name, domain=payload.domain, status="ativo")
        self.session.add(company)
        self.session.flush()

        company_id = company.id
        tenant_slug = payload.tenant_slug or _normalize_slug(payload.company_name)
        self._update_status(company_id, "database", "creating")

        schema_name = _tenant_schema(company_id)
        with self.engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))
        self._update_status(company_id, "database", "ready", schema=schema_name)

        plan_payload = payload.plan or PlanPayload(name=f"Plano {payload.company_name}")
        plan_name = f"{plan_payload.name} ({company_id})"
        plan = Plan(
            name=plan_name,
            description=plan_payload.description,
            preco=plan_payload.price,
            limite_mensagens=plan_payload.message_limit,
            limite_tokens=plan_payload.token_limit,
            features=plan_payload.features,
        )
        self.session.add(plan)
        self.session.flush()

        company.current_plan_id = plan.id
        self.session.add(company)

        subscription = Subscription(
            company_id=company_id,
            plan_id=plan.id,
            ciclo=payload.billing_cycle or "mensal",
            status="ativa",
        )
        self.session.add(subscription)
        self.session.flush()

        self._update_status(company_id, "subscription", "ready", subscription_id=subscription.id)

        redis_url = _tenant_redis_url(settings.redis_url, company_id)
        try:
            tenant_redis = Redis.from_url(redis_url)
            tenant_redis.ping()
        except Exception:  # pragma: no cover - conexão opcional
            tenant_redis = self.redis

        queue_name = queue_name_for_company(settings.queue_name, company_id)
        Queue(queue_name, connection=tenant_redis)
        self._update_status(company_id, "queue", "ready", queue=queue_name)

        self._store_infrastructure_metadata(
            company_id,
            schema=schema_name,
            redis_url=redis_url,
            queue_name=queue_name,
        )

        domain_payload = self._store_domain_metadata(
            company_id,
            base_domain=payload.base_domain,
            tenant_slug=tenant_slug,
        )
        self._update_status(
            company_id,
            "domain",
            "pending",
            chat_domain=domain_payload["chat_domain"],
            api_domain=domain_payload["api_domain"],
        )

        command_hint = f"python scripts/spawn_worker.py --company-id {company_id}"
        self._update_status(company_id, "worker", "awaiting", command=command_hint)

        token_payload = {
            "sub": "panel", 
            "scope": "panel:admin",
            "company_id": company_id,
        }
        token = encode_jwt(token_payload, settings.panel_jwt_secret, settings.panel_token_ttl_seconds)

        self.logger.info(
            "tenant_provisioned",
            company_id=company_id,
            domain=company.domain,
            plan_id=plan.id,
            subscription_id=subscription.id,
            redis_url=redis_url,
            queue=queue_name,
        )

        logging.getLogger("provisioning.email").info(
            "provisioning_credentials_sent",
            extra={
                "company": company.name,
                "domain": company.domain,
                "token": token,
            },
        )

        return {
            "company": {
                "id": company_id,
                "name": company.name,
                "domain": company.domain,
                "status": company.status,
            },
            "plan": plan.to_dict(),
            "subscription": subscription.to_dict(),
            "infrastructure": {
                "schema": schema_name,
                "redis_url": redis_url,
                "queue": queue_name,
                "worker_hint": command_hint,
            },
            "domains": domain_payload,
            "credentials": {
                "panel_token": token,
                "expires_in": settings.panel_token_ttl_seconds,
            },
        }


__all__ = [
    "PlanPayload",
    "ProvisioningPayload",
    "ProvisionerService",
]
