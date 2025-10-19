from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog
from flask import current_app
from sqlalchemy.orm import Session

from app.metrics import (
    appointment_followups_negative_total,
    appointment_followups_positive_total,
    appointment_followups_sent_total,
)
from app.models import Appointment
from app.services.audit import AuditService
from app.services.tenancy import TenantContext
from app.services.whaticket import WhaticketClient, WhaticketError

LOGGER = structlog.get_logger().bind(service="followup_service")


def _session() -> Session:
    session_factory = getattr(current_app, "db_session", None)
    if session_factory is None:
        raise RuntimeError("session_factory_not_configured")
    return session_factory()


def _queue_for_company(company_id: int):
    get_queue = getattr(current_app, "get_task_queue", None)
    if get_queue is None:
        raise RuntimeError("task_queue_not_configured")
    return get_queue(company_id)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def agendar_followup(appointment_id: int) -> None:
    session = _session()
    try:
        appointment = session.get(Appointment, appointment_id)
        if appointment is None:
            LOGGER.warning("appointment_not_found", appointment_id=appointment_id)
            return
        if not bool(getattr(appointment, "allow_followup", True)):
            LOGGER.info(
                "followup_consent_denied",
                appointment_id=appointment_id,
                company_id=appointment.company_id,
            )
            return
        if not appointment.end_time:
            LOGGER.warning("followup_missing_end_time", appointment_id=appointment_id)
            return
        try:
            enqueue_time = _as_utc(appointment.end_time) + timedelta(hours=1)
        except Exception:  # pragma: no cover - defensive conversion
            enqueue_time = datetime.utcnow().replace(tzinfo=timezone.utc) + timedelta(hours=1)
        now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
        queue = _queue_for_company(appointment.company_id)
        meta = {
            "company_id": appointment.company_id,
            "appointment_id": appointment.id,
            "kind": "followup",
        }
        enqueue_callable = getattr(queue, "enqueue_at", None)
        enqueue_target = enqueue_time.astimezone(timezone.utc).replace(tzinfo=None)
        if enqueue_time <= now_utc or not callable(enqueue_callable):
            queue.enqueue(
                enviar_followup,
                appointment.id,
                job_timeout=120,
                meta=meta,
            )
        else:
            enqueue_callable(
                enqueue_target,
                enviar_followup,
                appointment.id,
                job_timeout=120,
                meta=meta,
            )
        appointment.followup_next_scheduled = enqueue_time
        session.add(appointment)
        session.commit()
    finally:
        session.close()


def enviar_followup(appointment_id: int) -> bool:
    session = _session()
    try:
        appointment = session.get(Appointment, appointment_id)
        if appointment is None:
            LOGGER.warning("appointment_not_found", appointment_id=appointment_id)
            return False
        if not bool(getattr(appointment, "allow_followup", True)):
            LOGGER.info(
                "followup_consent_denied", appointment_id=appointment_id, company_id=appointment.company_id
            )
            return False
        if appointment.status in {"cancelled", "rescheduled"}:
            LOGGER.info(
                "followup_skipped_for_status",
                appointment_id=appointment_id,
                status=appointment.status,
            )
            return False
        tenant = TenantContext(company_id=appointment.company_id, label=str(appointment.company_id))
        client = WhaticketClient(current_app.redis, tenant)  # type: ignore[attr-defined]
        nome = appointment.client_name or "Cliente"
        mensagem = (
            f"Espero que tenha corrido tudo bem na reunião de hoje, {nome}. 😊\n"
            "Gostaria de marcar o próximo encontro?\n\n"
            "✅ Sim, quero marcar\n"
            "❌ Não, obrigado"
        )
        try:
            client.send_text(appointment.client_phone, mensagem)
        except WhaticketError:
            raise
        except Exception as exc:  # pragma: no cover - unexpected
            LOGGER.exception("followup_send_failed", appointment_id=appointment_id, error=str(exc))
            raise
        now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
        appointment.followup_sent_at = now_utc
        appointment.followup_next_scheduled = None
        session.add(appointment)
        session.commit()

        appointment_followups_sent_total.labels(company=str(appointment.company_id)).inc()

        session_factory = getattr(current_app, "db_session", None)
        if session_factory is None:
            raise RuntimeError("session_factory_not_configured")
        AuditService(session_factory).record(
            company_id=appointment.company_id,
            actor="followup",
            action="followup_sent",
            resource="appointment",
            payload={
                "appointment_id": appointment.id,
                "client_phone": appointment.client_phone,
            },
        )
        return True
    finally:
        session.close()


def registrar_resposta(
    appointment_id: int,
    response: str,
    *,
    feedback_text: str | None = None,
) -> None:
    session = _session()
    try:
        appointment = session.get(Appointment, appointment_id)
        if appointment is None:
            LOGGER.warning("appointment_not_found", appointment_id=appointment_id)
            return
        normalized = response.lower()
        appointment.followup_response = normalized
        appointment.followup_next_scheduled = None
        session.add(appointment)
        session.commit()

        if normalized == "positive":
            appointment_followups_positive_total.labels(company=str(appointment.company_id)).inc()
        elif normalized == "negative":
            appointment_followups_negative_total.labels(company=str(appointment.company_id)).inc()

        session_factory = getattr(current_app, "db_session", None)
        if session_factory is None:
            raise RuntimeError("session_factory_not_configured")
        AuditService(session_factory).record(
            company_id=appointment.company_id,
            actor="followup",
            action="followup_response",
            resource="appointment",
            payload={
                "appointment_id": appointment.id,
                "response": normalized,
                "feedback": feedback_text or "",
            },
        )
    finally:
        session.close()


__all__ = [
    "agendar_followup",
    "enviar_followup",
    "registrar_resposta",
]
