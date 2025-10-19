from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, TYPE_CHECKING

import structlog
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Company, Plan, Subscription
from app.services.tenancy import namespaced_key


if TYPE_CHECKING:  # pragma: no cover - hint only
    from app.services.analytics_service import AnalyticsService


class BillingService:
    """Serviço utilitário para gerenciar assinaturas e integrações de cobrança."""

    def __init__(self, session_factory, redis_client=None, analytics_service: "AnalyticsService | None" = None) -> None:
        self.session_factory = session_factory
        self.logger = structlog.get_logger().bind(service="billing")
        self.redis = redis_client
        self.analytics_service: "AnalyticsService | None" = analytics_service

    def _session(self) -> Session:
        return self.session_factory()  # type: ignore[call-arg]

    def attach_analytics_service(self, analytics_service: "AnalyticsService") -> None:
        self.analytics_service = analytics_service

    @staticmethod
    def _usage_key(company_id: int) -> str:
        return namespaced_key(company_id, "usage")

    @staticmethod
    def _calculate_cost(
        inbound_messages: int = 0,
        outbound_messages: int = 0,
        inbound_tokens: int = 0,
        outbound_tokens: int = 0,
    ) -> float:
        total_messages = inbound_messages + outbound_messages
        total_tokens = inbound_tokens + outbound_tokens
        message_cost = Decimal(total_messages) * Decimal(str(settings.billing_cost_per_message))
        token_cost = (Decimal(total_tokens) / Decimal(1000)) * Decimal(
            str(settings.billing_cost_per_thousand_tokens)
        )
        return float((message_cost + token_cost).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))

    def _update_usage_hash(
        self,
        company_id: int,
        *,
        inbound_messages: int = 0,
        outbound_messages: int = 0,
        inbound_tokens: int = 0,
        outbound_tokens: int = 0,
        response_time: float | None = None,
        cost: float | None = None,
    ) -> None:
        if self.redis is None:
            return
        key = self._usage_key(company_id)
        try:
            if inbound_messages:
                self.redis.hincrby(key, "messages_inbound", int(inbound_messages))
            if outbound_messages:
                self.redis.hincrby(key, "messages_outbound", int(outbound_messages))
            if inbound_tokens:
                self.redis.hincrby(key, "tokens_inbound", int(inbound_tokens))
            if outbound_tokens:
                self.redis.hincrby(key, "tokens_outbound", int(outbound_tokens))
            if response_time is not None:
                self.redis.hincrbyfloat(key, "response_time_total", float(response_time))
                self.redis.hincrby(key, "response_count", 1)
            if cost:
                self.redis.hincrbyfloat(key, "cost_estimated", float(cost))
            self.redis.hset(key, mapping={"updated_at": datetime.utcnow().isoformat()})
        except Exception:
            self.logger.warning("billing_usage_update_failed", company_id=company_id)

    def record_usage(
        self,
        company_id: int,
        *,
        inbound_messages: int = 0,
        outbound_messages: int = 0,
        inbound_tokens: int = 0,
        outbound_tokens: int = 0,
        response_time: float | None = None,
    ) -> float:
        if self.analytics_service is not None:
            cost = self.analytics_service.record_usage(
                company_id,
                inbound_messages=inbound_messages,
                outbound_messages=outbound_messages,
                inbound_tokens=inbound_tokens,
                outbound_tokens=outbound_tokens,
                response_time=response_time,
            )
            return cost

        cost = self._calculate_cost(
            inbound_messages=inbound_messages,
            outbound_messages=outbound_messages,
            inbound_tokens=inbound_tokens,
            outbound_tokens=outbound_tokens,
        )
        self._update_usage_hash(
            company_id,
            inbound_messages=inbound_messages,
            outbound_messages=outbound_messages,
            inbound_tokens=inbound_tokens,
            outbound_tokens=outbound_tokens,
            response_time=response_time,
            cost=cost,
        )
        return cost

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
            usage: dict[str, float] = {}
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
                        parsed_usage: dict[str, float] = {}
                        for key, value in decoded_usage.items():
                            try:
                                parsed_usage[key] = int(value)
                            except (TypeError, ValueError):
                                try:
                                    parsed_usage[key] = float(value)
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
            inbound_messages = int(usage.get("messages_inbound", 0)) if usage else 0
            outbound_messages = int(usage.get("messages_outbound", 0)) if usage else 0
            inbound_tokens = int(usage.get("tokens_inbound", 0)) if usage else 0
            outbound_tokens = int(usage.get("tokens_outbound", 0)) if usage else 0
            total_messages = inbound_messages + outbound_messages
            total_tokens = inbound_tokens + outbound_tokens
            if usage:
                usage.setdefault("messages_total", total_messages)
                usage.setdefault("tokens_total", total_tokens)
                usage.setdefault("messages", usage.get("messages_total", total_messages))
                usage.setdefault("tokens", usage.get("tokens_total", total_tokens))
                if "cost_estimated" in usage:
                    usage["cost_estimated"] = float(usage.get("cost_estimated", 0))
                response_total = float(usage.get("response_time_total", 0))
                response_count = float(usage.get("response_count", 0))
                usage["average_response_time"] = (
                    round(response_total / response_count, 4) if response_count else 0.0
                )
            else:
                usage = {
                    "messages_total": total_messages,
                    "tokens_total": total_tokens,
                    "messages": total_messages,
                    "tokens": total_tokens,
                    "average_response_time": 0.0,
                    "cost_estimated": 0.0,
                }

            analytics_summary = None
            if self.analytics_service is not None:
                try:
                    analytics_summary = self.analytics_service.get_summary(company.id)
                except Exception as exc:
                    self.logger.warning(
                        "billing_analytics_summary_failed",
                        company_id=company.id,
                        error=str(exc),
                    )

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
                "analytics": analytics_summary,
            }
        finally:
            session.close()


__all__ = ["BillingService"]
