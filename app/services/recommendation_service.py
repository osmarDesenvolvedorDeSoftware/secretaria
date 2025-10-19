from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests
import structlog
from sqlalchemy import case, desc, func
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Company, Conversation, FeedbackEvent
from app.services.analytics_service import AnalyticsService
from app.services.tenancy import namespaced_key

LOGGER = structlog.get_logger().bind(service="recommendation")


@dataclass
class FeedbackSignal:
    positive: int = 0
    negative: int = 0
    nps_total: int = 0
    nps_count: int = 0

    @property
    def ratio(self) -> float:
        total = self.positive + self.negative
        if total <= 0:
            return 0.0
        return round(self.positive / total, 4)

    @property
    def nps(self) -> float:
        if self.nps_count <= 0:
            return 0.0
        return round(self.nps_total / self.nps_count, 2)


class RecommendationService:
    def __init__(
        self,
        session_factory,
        redis_client,
        analytics_service: AnalyticsService | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.redis = redis_client
        self.analytics_service = analytics_service or AnalyticsService(session_factory, redis_client)
        self.logger = LOGGER

    def _session(self) -> Session:
        return self.session_factory()  # type: ignore[call-arg]

    # ------------------------------------------------------------------
    def evaluate(self, company_id: int, *, webhook_url: str | None = None) -> dict[str, Any]:
        session = self._session()
        try:
            company = session.get(Company, company_id)
            if company is None:
                raise ValueError("company_not_found")
            usage = self.analytics_service.get_real_time_usage(company_id)
            plan = company.plan
            now = datetime.utcnow()
            last_conversation = (
                session.query(Conversation)
                .filter(Conversation.company_id == company_id)
                .order_by(desc(Conversation.updated_at))
                .first()
            )
            last_interaction = last_conversation.updated_at if last_conversation else None
            recency_days = (now - last_interaction).days if last_interaction else 30
            recency_score = max(0.0, min(1.0, 1 - (recency_days / 30)))

            message_limit = float(plan.limite_mensagens or 1) if plan else 1.0
            token_limit = float(plan.limite_tokens or 1) if plan else 1.0
            messages_ratio = min(usage.get("messages_total", 0.0) / message_limit, 2.0)
            tokens_ratio = min(usage.get("tokens_total", 0.0) / token_limit, 2.0)
            frequency_score = max(0.0, min(1.0, (messages_ratio + tokens_ratio) / 2.0))

            estimated_cost = usage.get("cost_estimated", 0.0)
            plan_price = float(plan.preco or 1) if plan and plan.preco else 1.0
            value_score = max(0.0, min(1.0, estimated_cost / max(plan_price, 1.0)))

            churn_score = 1 - ((0.5 * recency_score) + (0.3 * frequency_score) + (0.2 * value_score))
            churn_score = round(max(0.0, min(1.0, churn_score)), 4)

            feedback_signal = self._load_feedback_signal(company_id, session)
            next_action = self._next_best_action(churn_score, messages_ratio, tokens_ratio, feedback_signal)
            upgrade_suggestions = self._build_upgrade_suggestions(plan, messages_ratio, tokens_ratio, usage)

            insights = {
                "company_id": company_id,
                "generated_at": now.isoformat(),
                "churn_score": churn_score,
                "plan_usage": {
                    "messages_ratio": round(messages_ratio, 4),
                    "tokens_ratio": round(tokens_ratio, 4),
                    "message_limit": int(message_limit),
                    "token_limit": int(token_limit),
                },
                "estimated_cost": round(float(estimated_cost), 4),
                "upgrade_suggestions": upgrade_suggestions,
                "next_best_action": next_action,
                "feedback": {
                    "positive_ratio": feedback_signal.ratio,
                    "average_nps": feedback_signal.nps,
                },
            }

            self._store_insights(company_id, insights)
            self._maybe_emit_triggers(company_id, insights, webhook_url=webhook_url)
            return insights
        finally:
            session.close()

    def get_insights(self, company_id: int) -> dict[str, Any]:
        key = namespaced_key(company_id, "business_ai", "insights")
        cached = None
        try:
            cached = self.redis.get(key)
        except Exception:
            cached = None
        if cached:
            try:
                return json.loads(cached)
            except (TypeError, ValueError):
                pass
        return {
            "company_id": company_id,
            "generated_at": None,
            "churn_score": None,
            "plan_usage": {},
            "estimated_cost": 0.0,
            "upgrade_suggestions": [],
            "next_best_action": None,
            "feedback": {},
        }

    def store_webhook_url(self, company_id: int, webhook_url: str) -> None:
        key = namespaced_key(company_id, "business_ai", "webhook_url")
        try:
            self.redis.set(key, webhook_url)
        except Exception:
            self.logger.warning("webhook_url_store_failed", company_id=company_id)

    # ------------------------------------------------------------------
    def _store_insights(self, company_id: int, payload: dict[str, Any]) -> None:
        key = namespaced_key(company_id, "business_ai", "insights")
        try:
            ttl = max(settings.business_ai_insights_ttl, 60)
            self.redis.setex(key, ttl, json.dumps(payload))
        except Exception:
            self.logger.warning("insights_cache_failed", company_id=company_id)

    def _append_trigger(self, company_id: int, event: dict[str, Any]) -> None:
        key = namespaced_key(company_id, "business_ai", "triggers")
        try:
            self.redis.lpush(key, json.dumps(event))
            self.redis.ltrim(key, 0, 49)
        except Exception:
            self.logger.warning("trigger_store_failed", company_id=company_id)

    def _resolve_webhook_url(self, company_id: int, override: str | None = None) -> str | None:
        if override:
            self.store_webhook_url(company_id, override)
            return override
        try:
            stored = self.redis.get(namespaced_key(company_id, "business_ai", "webhook_url"))
        except Exception:
            stored = None
        if stored:
            return stored
        return settings.business_ai_default_webhook

    def _emit_webhook(self, company_id: int, event: dict[str, Any], webhook_url: str | None) -> None:
        url = self._resolve_webhook_url(company_id, webhook_url)
        if not url:
            return
        try:
            requests.post(url, json=event, timeout=5)
        except Exception as exc:  # pragma: no cover - network failure logging only
            self.logger.warning("recommendation_webhook_failed", error=str(exc), company_id=company_id)

    def _maybe_emit_triggers(
        self,
        company_id: int,
        insights: dict[str, Any],
        *,
        webhook_url: str | None = None,
    ) -> None:
        plan_usage = insights.get("plan_usage") or {}
        messages_ratio = float(plan_usage.get("messages_ratio") or 0.0)
        tokens_ratio = float(plan_usage.get("tokens_ratio") or 0.0)
        churn_score = float(insights.get("churn_score") or 0.0)
        action = insights.get("next_best_action") or {}
        triggers: list[dict[str, Any]] = []

        limit_ratio = max(messages_ratio, tokens_ratio)
        if limit_ratio >= 0.8:
            triggers.append({
                "type": "billing/usage_near_limit",
                "ratio": round(limit_ratio, 4),
                "timestamp": datetime.utcnow().isoformat(),
            })
        if churn_score >= 0.7:
            triggers.append({
                "type": "churn_risk",
                "churn_score": churn_score,
                "timestamp": datetime.utcnow().isoformat(),
            })
        if isinstance(action, dict) and action.get("category") == "upsell":
            triggers.append({
                "type": "campaign_suggestion",
                "action": action,
                "timestamp": datetime.utcnow().isoformat(),
            })

        for event in triggers:
            event_payload = {**event, "company_id": company_id}
            self._append_trigger(company_id, event_payload)
            self._emit_webhook(company_id, event_payload, webhook_url)

    # ------------------------------------------------------------------
    def _build_upgrade_suggestions(
        self,
        plan: Any | None,
        messages_ratio: float,
        tokens_ratio: float,
        usage: dict[str, float],
    ) -> list[dict[str, Any]]:
        suggestions: list[dict[str, Any]] = []
        if plan is None:
            return suggestions
        if messages_ratio >= 1 or tokens_ratio >= 1:
            suggestions.append(
                {
                    "type": "upgrade_required",
                    "reason": "Limites atuais excedidos",
                    "evidence": {
                        "messages_ratio": round(messages_ratio, 3),
                        "tokens_ratio": round(tokens_ratio, 3),
                        "current_plan": plan.to_dict(),
                    },
                }
            )
        elif messages_ratio >= 0.8 or tokens_ratio >= 0.8:
            suggestions.append(
                {
                    "type": "upgrade_recommended",
                    "reason": "Consumo acima de 80%",
                    "evidence": {
                        "messages_processed": usage.get("messages_total", 0),
                        "tokens_consumed": usage.get("tokens_total", 0),
                    },
                }
            )
        return suggestions

    def _next_best_action(
        self,
        churn_score: float,
        messages_ratio: float,
        tokens_ratio: float,
        feedback: FeedbackSignal,
    ) -> dict[str, Any]:
        if churn_score >= 0.7:
            return {
                "category": "retention",
                "action": "transferir_para_humano",
                "message": "Cliente com alto risco de churn. Encaminhar para atendimento humano com urgência.",
            }
        if max(messages_ratio, tokens_ratio) >= 0.9:
            return {
                "category": "upsell",
                "action": "ofertar_upgrade",
                "message": "Oferta de upgrade recomendada: limites próximos ao máximo do plano.",
            }
        if feedback.ratio <= 0.4 and (feedback.positive + feedback.negative) >= 3:
            return {
                "category": "experience",
                "action": "pedir_mais_contexto",
                "message": "Solicitar mais detalhes ao cliente e revisar fluxos automáticos devido à queda na satisfação.",
            }
        return {
            "category": "engagement",
            "action": "oferecer_produto",
            "message": "Apresentar produto complementar baseado nas últimas interações positivas.",
        }

    def _load_feedback_signal(self, company_id: int, session: Session) -> FeedbackSignal:
        signal = FeedbackSignal()
        key = namespaced_key(company_id, "feedback", "aggregate")
        try:
            raw = self.redis.hgetall(key) or {}
        except Exception:
            raw = {}
        if raw:
            signal.positive = int(raw.get("positive", 0) or 0)
            signal.negative = int(raw.get("negative", 0) or 0)
            signal.nps_total = int(raw.get("nps_total", 0) or 0)
            signal.nps_count = int(raw.get("nps_count", 0) or 0)
            return signal

        # fallback to database aggregation
        aggregates = (
            session.query(
                func.sum(case((FeedbackEvent.feedback_type == "thumbs_up", 1), else_=0)),
                func.sum(case((FeedbackEvent.feedback_type == "thumbs_down", 1), else_=0)),
                func.sum(FeedbackEvent.score),
                func.count(FeedbackEvent.id),
            )
            .filter(FeedbackEvent.company_id == company_id)
            .one()
        )
        signal.positive = int(aggregates[0] or 0)
        signal.negative = int(aggregates[1] or 0)
        total_score = aggregates[2] or 0
        signal.nps_total = int(total_score) if total_score is not None else 0
        signal.nps_count = int(aggregates[3] or 0)
        return signal


__all__ = ["RecommendationService"]
