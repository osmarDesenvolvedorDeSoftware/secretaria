from __future__ import annotations

from datetime import date, datetime
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.models import Company, Plan, Subscription
from app.services.tenancy import namespaced_key


class BillingService:
    """Serviço utilitário para gerenciar assinaturas e integrações de cobrança."""

    def __init__(self, session_factory, redis_client=None) -> None:
        self.session_factory = session_factory
        self.logger = structlog.get_logger().bind(service="billing")
        self.redis = redis_client

    def _session(self) -> Session:
        return self.session_factory()  # type: ignore[call-arg]

    @staticmethod
    def _normalize_subscription_status(status: str | None) -> str:
        mapping = {
            "ativo": "ativa",
            "ativa": "ativa",
            "suspenso": "suspensa",
            "suspensa": "suspensa",
            "cancelado": "cancelada",
            "cancelada": "cancelada",
            "pending": "pendente",
            "pendente": "pendente",
            "paused": "suspensa",
            "cancelled": "cancelada",
            "active": "ativa",
        }
        normalized = mapping.get((status or "").lower())
        return normalized or "ativa"

    def get_or_create_subscription(
        self,
        company_id: int,
        plan_id: int,
        *,
        ciclo: str = "mensal",
    ) -> Subscription:
        session = self._session()
        try:
            subscription = (
                session.query(Subscription)
                .filter(Subscription.company_id == company_id, Subscription.plan_id == plan_id)
                .order_by(Subscription.started_at.desc())
                .first()
            )
            if subscription is None:
                subscription = Subscription(
                    company_id=company_id,
                    plan_id=plan_id,
                    ciclo=ciclo,
                    status="pendente",
                )
                session.add(subscription)
                session.flush()
            return subscription
        finally:
            session.close()

    def assign_plan(
        self,
        company_id: int,
        plan_id: int,
        *,
        ciclo: str = "mensal",
        status: str = "ativa",
        vencimento: date | None = None,
    ) -> Subscription:
        session = self._session()
        try:
            company = session.get(Company, company_id)
            if company is None:
                raise ValueError(f"Empresa {company_id} não encontrada")
            plan = session.get(Plan, plan_id)
            if plan is None:
                raise ValueError(f"Plano {plan_id} não encontrado")
            normalized_status = self._normalize_subscription_status(status)
            subscription = (
                session.query(Subscription)
                .filter(Subscription.company_id == company.id)
                .order_by(Subscription.started_at.desc())
                .first()
            )
            if subscription is None:
                subscription = Subscription(
                    company_id=company.id,
                    plan_id=plan.id,
                    ciclo=ciclo,
                    status=normalized_status,
                )
            subscription.plan_id = plan.id
            subscription.status = normalized_status
            subscription.ciclo = ciclo
            subscription.vencimento = vencimento
            subscription.started_at = subscription.started_at or datetime.utcnow()
            session.add(subscription)
            company.current_plan_id = plan.id
            session.add(company)
            session.commit()
            self.logger.info(
                "billing_subscription_updated",
                company_id=company.id,
                plan_id=plan.id,
                status=status,
            )
            return subscription
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def handle_payment_webhook(self, payload: dict[str, Any]) -> None:
        """Integração inicial com provedores de pagamento via webhook."""

        event_type = payload.get("event") or payload.get("type") or "unknown"
        metadata = payload.get("data") or {}
        company_id = metadata.get("company_id") or payload.get("company_id")
        plan_name = metadata.get("plan") or payload.get("plan")

        session = self._session()
        try:
            company = session.query(Company).get(int(company_id)) if company_id else None
            plan = (
                session.query(Plan)
                .filter(Plan.name == plan_name)
                .first()
                if plan_name
                else None
            )
            if not company or not plan:
                self.logger.warning(
                    "billing_webhook_unmatched",
                    event=event_type,
                    company_id=company_id,
                    plan_name=plan_name,
                )
                return

            status = metadata.get("status") or "ativa"
            ciclo = metadata.get("cycle") or "mensal"
            vencimento_raw = metadata.get("due_date")
            vencimento = None
            if isinstance(vencimento_raw, str):
                try:
                    vencimento = datetime.fromisoformat(vencimento_raw).date()
                except ValueError:
                    vencimento = None

            subscription = self.assign_plan(
                company.id,
                plan.id,
                ciclo=ciclo,
                status=status,
                vencimento=vencimento,
            )
            self.logger.info(
                "billing_webhook_processed",
                event_type=event_type,
                company_id=company.id,
                subscription_id=subscription.id,
                status=self._normalize_subscription_status(status),
            )
        finally:
            session.close()

    def summarize_company(self, company_id: int) -> dict[str, Any]:
        session = self._session()
        try:
            company = session.query(Company).get(company_id)
            if company is None:
                return {"company_id": company_id, "status": "desconhecida"}
            plan = company.plan
            subscription = (
                session.query(Subscription)
                .filter(Subscription.company_id == company_id)
                .order_by(Subscription.started_at.desc())
                .first()
            )
            usage: dict[str, int] = {}
            provisioning: dict[str, str] = {}
            domain_info: dict[str, str] = {}
            infrastructure: dict[str, str] = {}
            worker_count = 0
            if self.redis is not None:
                usage_key = namespaced_key(company_id, "usage")
                provisioning_key = namespaced_key(company_id, "provisioning")
                domain_key = namespaced_key(company_id, "domains")
                infra_key = namespaced_key(company_id, "infrastructure")

                def _decode_map(raw: dict) -> dict[str, str]:
                    decoded: dict[str, str] = {}
                    for key, value in raw.items():
                        key_str = key.decode() if isinstance(key, (bytes, bytearray)) else str(key)
                        if isinstance(value, (bytes, bytearray)):
                            decoded[key_str] = value.decode()
                        else:
                            decoded[key_str] = str(value)
                    return decoded

                try:
                    usage_raw = self.redis.hgetall(usage_key)
                    if isinstance(usage_raw, dict):
                        decoded_usage = _decode_map(usage_raw)
                        parsed_usage: dict[str, int] = {}
                        for key, value in decoded_usage.items():
                            try:
                                parsed_usage[key] = int(value)
                            except (TypeError, ValueError):
                                continue
                        usage = parsed_usage
                except Exception:
                    usage = {}

                try:
                    provisioning_raw = self.redis.hgetall(provisioning_key)
                    if isinstance(provisioning_raw, dict):
                        provisioning = _decode_map(provisioning_raw)
                except Exception:
                    provisioning = {}

                try:
                    domain_raw = self.redis.hgetall(domain_key)
                    if isinstance(domain_raw, dict):
                        domain_info = _decode_map(domain_raw)
                except Exception:
                    domain_info = {}

                try:
                    infra_raw = self.redis.hgetall(infra_key)
                    if isinstance(infra_raw, dict):
                        infrastructure = _decode_map(infra_raw)
                except Exception:
                    infrastructure = {}

                try:
                    worker_count = int(self.redis.scard(namespaced_key(company_id, "workers")))
                except Exception:
                    worker_count = 0
            return {
                "company_id": company.id,
                "company_name": company.name,
                "domain": company.domain,
                "status": company.status,
                "plan": plan.to_dict() if plan else None,
                "subscription": subscription.to_dict() if subscription else None,
                "usage": usage,
                "provisioning": provisioning,
                "domains": domain_info,
                "infrastructure": infrastructure,
                "worker_count": worker_count,
            }
        finally:
            session.close()


__all__ = ["BillingService"]
