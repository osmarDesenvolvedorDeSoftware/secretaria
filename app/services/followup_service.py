from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Union

import structlog
from flask import current_app
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.config import settings
from app.metrics import (
    appointment_followups_negative_total,
    appointment_followups_positive_total,
    appointment_followups_sent_total,
)
from app.models import Appointment, FeedbackEvent
from app.services.audit import AuditService
from app.services.security import sanitize_text
from app.services.tenancy import TenantContext
from app.services.whaticket import WhaticketClient, WhaticketError

LOGGER = structlog.get_logger().bind(service="followup_service")


AppointmentLike = Union[Appointment, int]


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


def _resolve_instance(session: Session, value: AppointmentLike) -> Appointment | None:
    if isinstance(value, Appointment):
        state = inspect(value)
        if state.session is session and state.persistent:
            return value
        if value.id is None:
            session.add(value)
            session.flush()
            return value if value.id is not None else None
        existing = session.get(Appointment, value.id)
        if existing is not None:
            return existing
        return session.merge(value, load=True)
    return session.get(Appointment, int(value))


def _extract_id(value: AppointmentLike) -> int:
    if isinstance(value, Appointment):
        if value.id is None:
            raise ValueError("appointment_missing_id")
        return int(value.id)
    return int(value)


def agendar_followup(appointment: AppointmentLike) -> None:
    session = _session()
    try:
        instance = _resolve_instance(session, appointment)
        if instance is None:
            try:
                missing_id = _extract_id(appointment)
            except ValueError:
                missing_id = None
            LOGGER.warning(
                "appointment_not_found",
                appointment_id=missing_id,
            )
            return
        if not bool(getattr(instance, "allow_followup", True)):
            LOGGER.info(
                "followup_consent_denied",
                appointment_id=instance.id,
                company_id=instance.company_id,
            )
            return
        if not instance.end_time:
            LOGGER.warning("followup_missing_end_time", appointment_id=instance.id)
            return
        try:
            enqueue_time = _as_utc(instance.end_time) + timedelta(hours=1)
        except Exception:  # pragma: no cover - defensive conversion
            enqueue_time = datetime.utcnow().replace(tzinfo=timezone.utc) + timedelta(hours=1)
        now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
        queue = _queue_for_company(instance.company_id)
        meta = {
            "company_id": instance.company_id,
            "appointment_id": instance.id,
            "kind": "followup",
        }
        enqueue_callable = getattr(queue, "enqueue_at", None)
        enqueue_target = enqueue_time.astimezone(timezone.utc).replace(tzinfo=None)
        if enqueue_time <= now_utc or not callable(enqueue_callable):
            queue.enqueue(
                enviar_followup,
                instance.id,
                job_timeout=120,
                meta=meta,
            )
        else:
            enqueue_callable(
                enqueue_target,
                enviar_followup,
                instance.id,
                job_timeout=120,
                meta=meta,
            )
        instance.followup_next_scheduled = enqueue_time
        session.add(instance)
        session.commit()
    finally:
        session.close()


def enviar_followup(appointment: AppointmentLike) -> bool:
    session = _session()
    try:
        instance = _resolve_instance(session, appointment)
        appointment_id = _extract_id(appointment)
        if instance is None:
            LOGGER.warning("appointment_not_found", appointment_id=appointment_id)
            return False
        if not bool(getattr(instance, "allow_followup", True)):
            LOGGER.info(
                "followup_consent_denied", appointment_id=appointment_id, company_id=instance.company_id
            )
            return False
        if instance.status in {"cancelled", "rescheduled"}:
            LOGGER.info(
                "followup_skipped_for_status",
                appointment_id=appointment_id,
                status=instance.status,
            )
            return False
        tenant = TenantContext(company_id=instance.company_id, label=str(instance.company_id))
        client = WhaticketClient(current_app.redis, tenant)  # type: ignore[attr-defined]
        nome = instance.client_name or "Cliente"
        mensagem = (
            f"Espero que tenha corrido tudo bem na reunião de hoje, {nome}. 😊\n"
            "Gostaria de marcar o próximo encontro?\n\n"
            "✅ Sim, quero marcar\n"
            "❌ Não, obrigado"
        )
        try:
            if not instance.client_phone:
                LOGGER.warning("followup_missing_phone", appointment_id=appointment_id)
                return False
            client.send_text(instance.client_phone, mensagem)
        except WhaticketError:
            raise
        except Exception as exc:  # pragma: no cover - unexpected
            LOGGER.exception("followup_send_failed", appointment_id=appointment_id, error=str(exc))
            raise
        now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
        instance.followup_sent_at = now_utc
        instance.followup_next_scheduled = None
        session.add(instance)
        session.commit()

        appointment_followups_sent_total.labels(company=str(instance.company_id)).inc()

        session_factory = getattr(current_app, "db_session", None)
        if session_factory is None:
            raise RuntimeError("session_factory_not_configured")
        AuditService(session_factory).record(
            company_id=instance.company_id,
            actor="followup",
            action="followup_sent",
            resource="appointment",
            payload={
                "appointment_id": instance.id,
                "client_phone": instance.client_phone,
            },
        )
        return True
    finally:
        session.close()


def registrar_resposta(
    appointment: AppointmentLike,
    response: str,
    *,
    feedback_text: str | None = None,
) -> None:
    session = _session()
    try:
        instance = _resolve_instance(session, appointment)
        appointment_id = _extract_id(appointment)
        if instance is None:
            LOGGER.warning("appointment_not_found", appointment_id=appointment_id)
            return
        normalized = response.lower()
        instance.followup_response = normalized
        instance.followup_next_scheduled = None

        if normalized == "feedback" and feedback_text:
            sanitized_feedback = sanitize_text(feedback_text)
            if sanitized_feedback:
                feedback_event = FeedbackEvent(
                    company_id=instance.company_id,
                    number=instance.client_phone,
                    channel="whatsapp",
                    feedback_type="followup_text",
                    score=None,
                    comment=sanitized_feedback,
                    details={"appointment_id": instance.id},
                    expires_at=FeedbackEvent.calculate_expiration(settings.retention_days_feedback),
                )
                session.add(feedback_event)
                feedback_text = sanitized_feedback

        session.add(instance)
        session.commit()

        if normalized == "positive":
            appointment_followups_positive_total.labels(company=str(instance.company_id)).inc()
        elif normalized == "negative":
            appointment_followups_negative_total.labels(company=str(instance.company_id)).inc()

        session_factory = getattr(current_app, "db_session", None)
        if session_factory is None:
            raise RuntimeError("session_factory_not_configured")
        AuditService(session_factory).record(
            company_id=instance.company_id,
            actor="followup",
            action="followup_response",
            resource="appointment",
            payload={
                "appointment_id": instance.id,
                "response": normalized,
                "feedback": feedback_text or "",
            },
        )
    finally:
        session.close()


def processar_resposta(appointment: AppointmentLike, mensagem: str) -> str:
    sanitized = sanitize_text(mensagem or "")
    normalized = sanitized.lower()
    appointment_id = _extract_id(appointment)
    negative_tokens: Iterable[str] = {"nao", "não", "obrigado", "obrigada", "depois", "cancel", "outro"}
    positive_prefixes: Iterable[str] = {"sim", "quero", "vamos", "agendar", "marcar", "confirmo", "topo", "✅"}

    if any(token in normalized for token in negative_tokens):
        registrar_resposta(appointment_id, "negative")
        LOGGER.info(
            "followup_response_negative",
            appointment_id=appointment_id,
            message=normalized,
        )
        return "negative"

    if any(normalized.startswith(token) for token in positive_prefixes):
        registrar_resposta(appointment_id, "positive")
        LOGGER.info(
            "followup_response_positive",
            appointment_id=appointment_id,
            message=normalized,
        )
        return "positive"

    registrar_resposta(appointment_id, "feedback", feedback_text=normalized or sanitized)
    LOGGER.info(
        "followup_response_feedback",
        appointment_id=appointment_id,
        message=normalized,
    )
    return "feedback"


__all__ = [
    "agendar_followup",
    "enviar_followup",
    "registrar_resposta",
    "processar_resposta",
]
