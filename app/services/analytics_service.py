from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO, StringIO
from typing import Any, Iterable

import requests
import structlog
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AnalyticsReport, Company, Plan
from app.models.analytics_report import AnalyticsGranularity
from app.services.tenancy import namespaced_key


class AnalyticsService:
    """Serviço responsável por agregar métricas de uso e faturamento."""

    def __init__(self, session_factory, redis_client=None) -> None:
        self.session_factory = session_factory
        self.redis = redis_client
        self.logger = structlog.get_logger().bind(service="analytics")

    def _session(self) -> Session:
        return self.session_factory()  # type: ignore[call-arg]

    @staticmethod
    def _now() -> datetime:
        return datetime.utcnow()

    @staticmethod
    def _get_period_bounds(granularity: AnalyticsGranularity, moment: datetime) -> tuple[datetime, datetime]:
        if granularity == AnalyticsGranularity.DAILY:
            start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
            return start, end
        start_of_week = moment - timedelta(days=moment.weekday())
        start = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=7)
        return start, end

    def _calculate_cost(
        self,
        inbound_messages: int = 0,
        outbound_messages: int = 0,
        inbound_tokens: int = 0,
        outbound_tokens: int = 0,
    ) -> Decimal:
        total_messages = inbound_messages + outbound_messages
        total_tokens = inbound_tokens + outbound_tokens
        cost_messages = Decimal(total_messages) * Decimal(str(settings.billing_cost_per_message))
        cost_tokens = (
            Decimal(total_tokens) / Decimal(1000)
        ) * Decimal(str(settings.billing_cost_per_thousand_tokens))
        total_cost = cost_messages + cost_tokens
        return total_cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)

    def _get_or_create_report(
        self,
        session: Session,
        company_id: int,
        granularity: AnalyticsGranularity,
        moment: datetime,
    ) -> AnalyticsReport:
        period_start, period_end = self._get_period_bounds(granularity, moment)
        report = (
            session.query(AnalyticsReport)
            .filter(
                AnalyticsReport.company_id == company_id,
                AnalyticsReport.granularity == granularity,
                AnalyticsReport.period_start == period_start,
            )
            .one_or_none()
        )
        if report is None:
            report = AnalyticsReport(
                company_id=company_id,
                granularity=granularity,
                period_start=period_start,
                period_end=period_end,
                messages_inbound=0,
                messages_outbound=0,
                tokens_inbound=0,
                tokens_outbound=0,
                responses_count=0,
                response_time_total=Decimal(0),
                estimated_cost=Decimal(0),
            )
            session.add(report)
            session.flush()
        return report

    def _update_realtime_usage(
        self,
        company_id: int,
        *,
        inbound_messages: int = 0,
        outbound_messages: int = 0,
        inbound_tokens: int = 0,
        outbound_tokens: int = 0,
        response_time: float | None = None,
        cost: Decimal | None = None,
    ) -> None:
        if self.redis is None:
            return
        usage_key = namespaced_key(company_id, "usage")
        try:
            if inbound_messages:
                self.redis.hincrby(usage_key, "messages_inbound", int(inbound_messages))
            if outbound_messages:
                self.redis.hincrby(usage_key, "messages_outbound", int(outbound_messages))
            if inbound_tokens:
                self.redis.hincrby(usage_key, "tokens_inbound", int(inbound_tokens))
            if outbound_tokens:
                self.redis.hincrby(usage_key, "tokens_outbound", int(outbound_tokens))
            if response_time is not None:
                self.redis.hincrbyfloat(usage_key, "response_time_total", float(response_time))
                self.redis.hincrby(usage_key, "response_count", 1)
            if cost is not None and cost > 0:
                self.redis.hincrbyfloat(usage_key, "cost_estimated", float(cost))
            self.redis.hset(usage_key, mapping={"updated_at": self._now().isoformat()})
        except Exception:
            self.logger.warning("analytics_usage_update_failed", company_id=company_id)

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
        moment = self._now()
        cost = self._calculate_cost(
            inbound_messages=inbound_messages,
            outbound_messages=outbound_messages,
            inbound_tokens=inbound_tokens,
            outbound_tokens=outbound_tokens,
        )
        session = self._session()
        try:
            for granularity in (AnalyticsGranularity.DAILY, AnalyticsGranularity.WEEKLY):
                report = self._get_or_create_report(session, company_id, granularity, moment)
                report.messages_inbound += int(inbound_messages)
                report.messages_outbound += int(outbound_messages)
                report.tokens_inbound += int(inbound_tokens)
                report.tokens_outbound += int(outbound_tokens)
                if response_time is not None:
                    report.responses_count += 1
                    current_total = Decimal(report.response_time_total or 0)
                    report.response_time_total = current_total + Decimal(str(response_time))
                report.estimated_cost = Decimal(report.estimated_cost or 0) + cost
                report.updated_at = moment
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        self._update_realtime_usage(
            company_id,
            inbound_messages=inbound_messages,
            outbound_messages=outbound_messages,
            inbound_tokens=inbound_tokens,
            outbound_tokens=outbound_tokens,
            response_time=response_time,
            cost=cost,
        )
        self._check_thresholds(company_id)
        return float(cost)

    def _decode_usage(self, raw: dict[str, Any]) -> dict[str, float]:
        decoded: dict[str, float] = {}
        for key, value in raw.items():
            try:
                decoded[key] = float(value)
            except (TypeError, ValueError):
                continue
        return decoded

    def _load_usage_from_reports(self, company_id: int) -> dict[str, float]:
        session = self._session()
        try:
            report = (
                session.query(AnalyticsReport)
                .filter(
                    AnalyticsReport.company_id == company_id,
                    AnalyticsReport.granularity == AnalyticsGranularity.DAILY,
                )
                .order_by(AnalyticsReport.period_start.desc())
                .first()
            )
            if report is None:
                return {}
            return {
                "messages_inbound": float(report.messages_inbound or 0),
                "messages_outbound": float(report.messages_outbound or 0),
                "tokens_inbound": float(report.tokens_inbound or 0),
                "tokens_outbound": float(report.tokens_outbound or 0),
                "response_time_total": float(report.response_time_total or 0),
                "response_count": float(report.responses_count or 0),
                "cost_estimated": float(report.estimated_cost or 0),
            }
        finally:
            session.close()

    def get_real_time_usage(self, company_id: int) -> dict[str, float]:
        usage: dict[str, float]
        if self.redis is not None:
            try:
                raw = self.redis.hgetall(namespaced_key(company_id, "usage"))
            except Exception:
                raw = {}
            usage = self._decode_usage(raw)
        else:
            usage = {}
        if not usage:
            usage = self._load_usage_from_reports(company_id)

        messages_in = usage.get("messages_inbound", 0.0)
        messages_out = usage.get("messages_outbound", 0.0)
        tokens_in = usage.get("tokens_inbound", 0.0)
        tokens_out = usage.get("tokens_outbound", 0.0)
        response_total = usage.get("response_time_total", 0.0)
        response_count = usage.get("response_count", 0.0)
        average_response = response_total / response_count if response_count else 0.0

        usage.update(
            {
                "messages_total": messages_in + messages_out,
                "tokens_total": tokens_in + tokens_out,
                "average_response_time": round(average_response, 4),
                "cost_estimated": usage.get("cost_estimated", 0.0),
            }
        )
        return usage

    def _fetch_plan(self, session: Session, company_id: int) -> Plan | None:
        company = session.get(Company, company_id)
        return company.plan if company else None

    def _alert_key(self, company_id: int) -> str:
        return namespaced_key(company_id, "analytics", "alert_level")

    def _alerts_list_key(self, company_id: int) -> str:
        return namespaced_key(company_id, "analytics", "alerts")

    def _store_alert(self, company_id: int, payload: dict[str, Any]) -> None:
        if self.redis is None:
            return
        try:
            self.redis.lpush(self._alerts_list_key(company_id), json.dumps(payload))
            self.redis.ltrim(self._alerts_list_key(company_id), 0, 9)
        except Exception:
            self.logger.warning("analytics_store_alert_failed", company_id=company_id)

    def _send_webhook_alert(self, payload: dict[str, Any]) -> None:
        if not settings.billing_alert_webhook_url:
            return
        try:
            requests.post(
                settings.billing_alert_webhook_url,
                json=payload,
                timeout=5,
            )
        except Exception as exc:  # pragma: no cover - network failures should not break flow
            self.logger.warning("analytics_alert_webhook_failed", error=str(exc))

    def _check_thresholds(self, company_id: int) -> None:
        session = self._session()
        try:
            plan = self._fetch_plan(session, company_id)
        finally:
            session.close()
        if plan is None:
            return

        usage = self.get_real_time_usage(company_id)
        message_limit = float(plan.limite_mensagens or 0)
        token_limit = float(plan.limite_tokens or 0)
        messages_ratio = usage["messages_total"] / message_limit if message_limit else 0.0
        tokens_ratio = usage["tokens_total"] / token_limit if token_limit else 0.0
        reached = max(messages_ratio, tokens_ratio)

        level: str | None = None
        if reached >= 1:
            level = "critical"
        elif reached >= 0.8:
            level = "warning"

        if self.redis is None:
            return

        try:
            previous = self.redis.get(self._alert_key(company_id))
        except Exception:
            previous = None

        if level:
            alert_payload = {
                "company_id": company_id,
                "level": level,
                "messages_ratio": round(messages_ratio, 4),
                "tokens_ratio": round(tokens_ratio, 4),
                "timestamp": self._now().isoformat(),
            }
            if previous != level:
                try:
                    self.redis.set(self._alert_key(company_id), level)
                except Exception:
                    pass
                self._store_alert(company_id, alert_payload)
                self._send_webhook_alert(alert_payload)
        else:
            if previous:
                try:
                    self.redis.set(self._alert_key(company_id), "normal")
                except Exception:
                    pass

    def get_alerts(self, company_id: int, limit: int = 5) -> list[dict[str, Any]]:
        if self.redis is None:
            return []
        try:
            raw_alerts: Iterable[str] = self.redis.lrange(self._alerts_list_key(company_id), 0, limit - 1)
        except Exception:
            return []
        alerts: list[dict[str, Any]] = []
        for item in raw_alerts:
            if not item:
                continue
            try:
                parsed = json.loads(item)
            except (TypeError, ValueError):
                continue
            alerts.append(parsed)
        return alerts

    def _get_report_for_period(
        self,
        session: Session,
        company_id: int,
        granularity: AnalyticsGranularity,
        moment: datetime,
    ) -> AnalyticsReport | None:
        return (
            session.query(AnalyticsReport)
            .filter(
                AnalyticsReport.company_id == company_id,
                AnalyticsReport.granularity == granularity,
                AnalyticsReport.period_start <= moment,
                AnalyticsReport.period_end > moment,
            )
            .order_by(AnalyticsReport.period_start.desc())
            .first()
        )

    def get_summary(self, company_id: int) -> dict[str, Any]:
        moment = self._now()
        session = self._session()
        try:
            plan = self._fetch_plan(session, company_id)
            daily = self._get_report_for_period(session, company_id, AnalyticsGranularity.DAILY, moment)
            weekly = self._get_report_for_period(session, company_id, AnalyticsGranularity.WEEKLY, moment)
        finally:
            session.close()

        usage = self.get_real_time_usage(company_id)
        alerts = self.get_alerts(company_id)
        return {
            "company_id": company_id,
            "plan": plan.to_dict() if isinstance(plan, Plan) else None,
            "current_usage": usage,
            "daily": daily.to_dict() if daily else None,
            "weekly": weekly.to_dict() if weekly else None,
            "alerts": alerts,
        }

    def get_history(self, company_id: int, period: str) -> dict[str, Any]:
        period = period.lower()
        moment = self._now()
        session = self._session()
        try:
            if period == "week":
                start = (moment - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
                reports = (
                    session.query(AnalyticsReport)
                    .filter(
                        AnalyticsReport.company_id == company_id,
                        AnalyticsReport.granularity == AnalyticsGranularity.DAILY,
                        AnalyticsReport.period_start >= start,
                    )
                    .order_by(AnalyticsReport.period_start.asc())
                    .all()
                )
            elif period == "month":
                start = (moment - timedelta(weeks=8)).replace(hour=0, minute=0, second=0, microsecond=0)
                reports = (
                    session.query(AnalyticsReport)
                    .filter(
                        AnalyticsReport.company_id == company_id,
                        AnalyticsReport.granularity == AnalyticsGranularity.WEEKLY,
                        AnalyticsReport.period_start >= start,
                    )
                    .order_by(AnalyticsReport.period_start.asc())
                    .all()
                )
            else:
                raise ValueError("invalid_period")
        finally:
            session.close()

        return {
            "company_id": company_id,
            "period": period,
            "data": [report.to_dict() for report in reports],
        }

    def export_report(self, company_id: int, format_: str) -> tuple[str, str, bytes]:
        summary = self.get_summary(company_id)
        history = self.get_history(company_id, "month")

        rows: list[list[str]] = [
            [
                "Granularity",
                "Period Start",
                "Period End",
                "Messages Inbound",
                "Messages Outbound",
                "Tokens Inbound",
                "Tokens Outbound",
                "Average Response Time (s)",
                "Estimated Cost",
            ]
        ]
        for item in history.get("data", []):
            rows.append(
                [
                    str(item.get("granularity")),
                    str(item.get("period_start")),
                    str(item.get("period_end")),
                    str(item.get("messages_inbound")),
                    str(item.get("messages_outbound")),
                    str(item.get("tokens_inbound")),
                    str(item.get("tokens_outbound")),
                    str(item.get("average_response_time")),
                    str(item.get("estimated_cost")),
                ]
            )

        filename_prefix = f"analytics_company_{company_id}_{self._now().strftime('%Y%m%d')}"

        format_lower = format_.lower()
        if format_lower == "csv":
            buffer = StringIO()
            writer = csv.writer(buffer)
            writer.writerows(rows)
            content = buffer.getvalue().encode("utf-8")
            filename = f"{filename_prefix}.csv"
            return filename, "text/csv", content
        if format_lower == "pdf":
            try:
                from fpdf import FPDF
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise RuntimeError("pdf_export_not_available") from exc

            pdf = FPDF()
            pdf.set_auto_page_break(auto=True, margin=15)
            pdf.add_page()
            pdf.set_font("Arial", "B", 16)
            pdf.cell(0, 10, "Relatório de Analytics", ln=True)
            pdf.set_font("Arial", size=12)
            plan = summary.get("plan") or {}
            plan_name = plan.get("name", "-") if isinstance(plan, dict) else "-"
            pdf.cell(0, 8, f"Empresa: {company_id} | Plano: {plan_name}", ln=True)
            pdf.ln(4)
            pdf.set_font("Arial", size=10)
            for row in rows:
                pdf.multi_cell(0, 6, " | ".join(row))
            pdf_output = BytesIO(pdf.output(dest="S").encode("latin-1"))
            filename = f"{filename_prefix}.pdf"
            return filename, "application/pdf", pdf_output.getvalue()

        raise ValueError("unsupported_format")


__all__ = ["AnalyticsService"]
