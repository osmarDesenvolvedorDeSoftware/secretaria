from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ABEvent, ABTest
from app.services.tenancy import namespaced_key

LOGGER = structlog.get_logger().bind(service="abtest")


@dataclass(frozen=True)
class VariantSelection:
    ab_test_id: int
    variant: str
    template_name: str
    metadata: dict[str, Any]


class ABTestService:
    def __init__(self, session_factory, redis_client) -> None:
        self.session_factory = session_factory
        self.redis = redis_client
        self.logger = LOGGER

    def _session(self) -> Session:
        return self.session_factory()  # type: ignore[call-arg]

    # ------------------------------------------------------------------
    def list_tests(self, company_id: int) -> list[dict[str, Any]]:
        session = self._session()
        try:
            tests = (
                session.query(ABTest)
                .filter(ABTest.company_id == company_id)
                .order_by(ABTest.created_at.desc())
                .all()
            )
            return [self._serialize_test(session, test) for test in tests]
        finally:
            session.close()

    def get_test(self, company_id: int, test_id: int) -> dict[str, Any]:
        session = self._session()
        try:
            test = (
                session.query(ABTest)
                .filter(ABTest.company_id == company_id, ABTest.id == test_id)
                .one_or_none()
            )
            if test is None:
                raise ValueError("abtest_not_found")
            return self._serialize_test(session, test)
        finally:
            session.close()

    def create_test(self, company_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        template_base = payload.get("template_base")
        if not template_base:
            raise ValueError("template_base_required")
        epsilon = float(payload.get("epsilon", 0.1) or 0.1)
        period_start = self._parse_datetime(payload.get("period_start"))
        period_end = self._parse_datetime(payload.get("period_end"))
        if period_start and period_end and period_end <= period_start:
            raise ValueError("invalid_period")
        variant_a = self._normalize_variant(payload.get("variant_a"))
        variant_b = self._normalize_variant(payload.get("variant_b"))
        target_metrics = payload.get("target_metrics")
        if not isinstance(target_metrics, (list, tuple)):
            target_metrics = []

        session = self._session()
        try:
            test = ABTest(
                company_id=company_id,
                template_base=template_base,
                name=payload.get("name"),
                variant_a=variant_a,
                variant_b=variant_b,
                target_metrics=list(target_metrics),
                epsilon=epsilon,
                period_start=period_start,
                period_end=period_end,
            )
            session.add(test)
            session.commit()
            return self._serialize_test(session, test)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def update_test(self, company_id: int, test_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        session = self._session()
        try:
            test = (
                session.query(ABTest)
                .filter(ABTest.company_id == company_id, ABTest.id == test_id)
                .one_or_none()
            )
            if test is None:
                raise ValueError("abtest_not_found")
            if test.status == "running":
                raise ValueError("cannot_update_running_test")
            if "name" in payload:
                test.name = payload.get("name")
            if "variant_a" in payload:
                test.variant_a = self._normalize_variant(payload.get("variant_a"))
            if "variant_b" in payload:
                test.variant_b = self._normalize_variant(payload.get("variant_b"))
            if "target_metrics" in payload:
                metrics = payload.get("target_metrics")
                test.target_metrics = list(metrics) if isinstance(metrics, (list, tuple)) else []
            if "epsilon" in payload and payload.get("epsilon") is not None:
                test.epsilon = float(payload.get("epsilon"))
            if "period_start" in payload:
                test.period_start = self._parse_datetime(payload.get("period_start"))
            if "period_end" in payload:
                test.period_end = self._parse_datetime(payload.get("period_end"))
            session.commit()
            return self._serialize_test(session, test)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def start_test(self, company_id: int, test_id: int) -> dict[str, Any]:
        session = self._session()
        try:
            test = (
                session.query(ABTest)
                .filter(ABTest.company_id == company_id, ABTest.id == test_id)
                .one_or_none()
            )
            if test is None:
                raise ValueError("abtest_not_found")
            test.status = "running"
            if test.period_start is None:
                test.period_start = datetime.utcnow()
            session.commit()
            return self._serialize_test(session, test)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def stop_test(self, company_id: int, test_id: int) -> dict[str, Any]:
        session = self._session()
        try:
            test = (
                session.query(ABTest)
                .filter(ABTest.company_id == company_id, ABTest.id == test_id)
                .one_or_none()
            )
            if test is None:
                raise ValueError("abtest_not_found")
            test.status = "stopped"
            test.period_end = datetime.utcnow()
            session.commit()
            return self._serialize_test(session, test)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def delete_test(self, company_id: int, test_id: int) -> None:
        session = self._session()
        try:
            test = (
                session.query(ABTest)
                .filter(ABTest.company_id == company_id, ABTest.id == test_id)
                .one_or_none()
            )
            if test is None:
                raise ValueError("abtest_not_found")
            session.delete(test)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ------------------------------------------------------------------
    def select_variant(self, company_id: int, template_name: str) -> VariantSelection | None:
        session = self._session()
        try:
            test = (
                session.query(ABTest)
                .filter(
                    ABTest.company_id == company_id,
                    ABTest.template_base == template_name,
                    ABTest.status == "running",
                )
                .order_by(ABTest.created_at.desc())
                .first()
            )
            if test is None:
                return None

            if test.period_end and datetime.utcnow() > test.period_end:
                self._finalize_test(session, test)
                session.commit()
                return None

            variant = self._choose_variant(session, test)
            if variant is None:
                return None

            selection = VariantSelection(
                ab_test_id=test.id,
                variant=variant,
                template_name=self._resolve_template_for_variant(test, variant),
                metadata=self._variant_payload(test, variant),
            )
            self._record_event(session, test, variant, "impression", None)
            session.commit()
            return selection
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def record_event(
        self,
        company_id: int,
        test_id: int,
        variant: str,
        event_type: str,
        response_time: float | None = None,
    ) -> None:
        session = self._session()
        try:
            test = (
                session.query(ABTest)
                .filter(ABTest.company_id == company_id, ABTest.id == test_id)
                .one_or_none()
            )
            if test is None:
                raise ValueError("abtest_not_found")
            self._record_event(session, test, variant, event_type, response_time)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ------------------------------------------------------------------
    def _record_event(
        self,
        session: Session,
        test: ABTest,
        variant: str,
        event_type: str,
        response_time: float | None,
    ) -> None:
        bucket = date.today()
        event = (
            session.query(ABEvent)
            .filter(
                ABEvent.ab_test_id == test.id,
                ABEvent.variant == variant,
                ABEvent.bucket_date == bucket,
            )
            .one_or_none()
        )
        if event is None:
            event = ABEvent(
                company_id=test.company_id,
                ab_test_id=test.id,
                variant=variant,
                bucket_date=bucket,
            )
            session.add(event)

        event.impressions = int(event.impressions or 0)
        event.responses = int(event.responses or 0)
        event.conversions = int(event.conversions or 0)
        event.clicks = int(event.clicks or 0)
        event.response_time_total = float(event.response_time_total or 0.0)
        event.response_time_count = int(event.response_time_count or 0)

        if event_type == "impression":
            event.impressions += 1
        elif event_type == "response":
            event.responses += 1
            if response_time is not None:
                event.response_time_total = (event.response_time_total or 0) + response_time
                event.response_time_count += 1
        elif event_type == "conversion":
            event.conversions += 1
        elif event_type == "click":
            event.clicks += 1

        retention_days = max(settings.retention_days_ab_events, 1)
        event.expires_at = datetime.utcnow() + timedelta(days=retention_days)
        session.flush()
        metrics = self._aggregate_metrics(session, test.id)
        self._store_metrics_cache(test.company_id, test.id, metrics)

    def _store_metrics_cache(
        self, company_id: int, test_id: int, metrics: dict[str, dict[str, Any]]
    ) -> None:
        key = namespaced_key(company_id, "abtest", str(test_id), "metrics")
        try:
            self.redis.hset(
                key,
                {
                    "variant_a": json_dumps(metrics.get("A", {})),
                    "variant_b": json_dumps(metrics.get("B", {})),
                },
            )
        except Exception:
            self.logger.debug("abtest_metrics_cache_failed", company_id=company_id, test_id=test_id)

    def _aggregate_metrics(self, session: Session, test_id: int) -> dict[str, dict[str, Any]]:
        rows = (
            session.query(
                ABEvent.variant,
                func.sum(ABEvent.impressions),
                func.sum(ABEvent.responses),
                func.sum(ABEvent.conversions),
                func.sum(ABEvent.clicks),
                func.sum(ABEvent.response_time_total),
                func.sum(ABEvent.response_time_count),
            )
            .filter(ABEvent.ab_test_id == test_id)
            .group_by(ABEvent.variant)
            .all()
        )
        metrics: dict[str, dict[str, Any]] = {}
        for variant, impressions, responses, conversions, clicks, total_time, time_count in rows:
            impressions = int(impressions or 0)
            responses = int(responses or 0)
            conversions = int(conversions or 0)
            clicks = int(clicks or 0)
            total_time = float(total_time or 0.0)
            time_count = int(time_count or 0)
            avg_time = total_time / time_count if time_count else 0.0
            rate = conversions / impressions if impressions else 0.0
            metrics[variant] = {
                "impressions": impressions,
                "responses": responses,
                "conversions": conversions,
                "clicks": clicks,
                "average_response_time": round(avg_time, 4),
                "conversion_rate": round(rate, 4),
            }
        return metrics

    def _serialize_test(self, session: Session, test: ABTest) -> dict[str, Any]:
        payload = test.to_dict(include_events=False)
        payload["metrics"] = self._aggregate_metrics(session, test.id)
        return payload

    def _choose_variant(self, session: Session, test: ABTest) -> str | None:
        metrics = self._aggregate_metrics(session, test.id)
        epsilon = max(0.0, min(1.0, float(test.epsilon or 0.1)))
        explore = random.random() < epsilon
        variants = ["A", "B"]
        if explore:
            return random.choice(variants)
        # exploit best conversion rate
        best_variant = None
        best_rate = -1.0
        for variant in variants:
            stats = metrics.get(variant, {})
            rate = float(stats.get("conversion_rate") or 0.0)
            if rate > best_rate:
                best_rate = rate
                best_variant = variant
        return best_variant or random.choice(variants)

    def _resolve_template_for_variant(self, test: ABTest, variant: str) -> str:
        payload = self._variant_payload(test, variant)
        template = payload.get("template") or payload.get("template_name")
        if isinstance(template, str) and template:
            return template
        return test.template_base

    def _variant_payload(self, test: ABTest, variant: str) -> dict[str, Any]:
        data = test.variant_a if variant == "A" else test.variant_b
        return data if isinstance(data, dict) else {}

    def _finalize_test(self, session: Session, test: ABTest) -> None:
        metrics = self._aggregate_metrics(session, test.id)
        variant_scores = {
            variant: stats.get("conversion_rate") or 0.0 for variant, stats in metrics.items()
        }
        winning_variant = max(variant_scores, key=variant_scores.get) if variant_scores else None
        test.winning_variant = winning_variant
        test.status = "completed"
        session.flush()

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(value)
        except (TypeError, ValueError):
            raise ValueError("invalid_datetime")

    @staticmethod
    def _normalize_variant(data: Any) -> dict[str, Any]:
        if isinstance(data, dict):
            return data
        return {}


def json_dumps(data: dict[str, Any]) -> str:
    import json

    return json.dumps(data)


__all__ = ["ABTestService", "VariantSelection"]
