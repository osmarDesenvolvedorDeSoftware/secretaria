from __future__ import annotations

from datetime import datetime, timedelta

import structlog
from flask import current_app
from sqlalchemy.orm import Session

from app.metrics import appointment_reminders_sent_total
from app.models import Appointment
from app.services.audit import AuditService
from app.services.tenancy import TenantContext
from app.services.whaticket import WhaticketClient, WhaticketError

LOGGER = structlog.get_logger().bind(service="reminder_service")

REMINDER_TYPES = {"24h", "1h", "manual", "custom"}


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


def agendar_lembrete(appointment_id: int, horario_envio: datetime, reminder_type: str = "custom") -> None:
    """Agenda um job RQ para envio do lembrete."""

    session = _session()
    try:
        appointment = session.get(Appointment, appointment_id)
        if appointment is None:
            LOGGER.warning("appointment_not_found", appointment_id=appointment_id)
            return
        queue = _queue_for_company(appointment.company_id)
        meta = {
            "company_id": appointment.company_id,
            "appointment_id": appointment.id,
            "reminder_type": reminder_type,
        }
        enqueue_at = getattr(queue, "enqueue_at", None)
        now = datetime.utcnow()
        if horario_envio <= now or not callable(enqueue_at):
            queue.enqueue(
                enviar_lembrete,
                appointment.id,
                reminder_type,
                job_timeout=120,
                meta=meta,
            )
        else:
            enqueue_at(
                horario_envio,
                enviar_lembrete,
                appointment.id,
                reminder_type,
                job_timeout=120,
                meta=meta,
            )
    finally:
        session.close()


def agendar_lembretes_padrao(appointment: Appointment) -> None:
    if not appointment.start_time:
        return
    start = appointment.start_time
    now = datetime.utcnow()
    horarios = [
        (start - timedelta(hours=24), "24h"),
        (start - timedelta(hours=1), "1h"),
    ]
    for horario, tipo in horarios:
        if horario > now:
            agendar_lembrete(appointment.id, horario, tipo)


def enviar_lembrete(appointment_id: int, reminder_type: str = "custom") -> bool:
    session = _session()
    try:
        appointment = session.get(Appointment, appointment_id)
        if appointment is None:
            LOGGER.warning("appointment_not_found", appointment_id=appointment_id)
            return False
        if appointment.status in {"cancelled", "rescheduled", "no_show"}:
            LOGGER.info(
                "skip_reminder_for_status",
                appointment_id=appointment_id,
                status=appointment.status,
            )
            return False
        tenant = TenantContext(company_id=appointment.company_id, label=str(appointment.company_id))
        client = WhaticketClient(current_app.redis, tenant)  # type: ignore[attr-defined]
        try:
            formatted_time = appointment.start_time.astimezone().strftime("%d/%m às %Hh%M")
        except ValueError:
            formatted_time = appointment.start_time.strftime("%d/%m às %Hh%M")
        nome = appointment.client_name or "Cliente"
        mensagem = (
            f"📅 Olá {nome}, lembrando da sua reunião às {formatted_time}. "
            "Deseja confirmar ou reagendar?\n\n"
            "✅ Confirmar presença\n"
            "🔄 Reagendar"
        )
        client.send_text(appointment.client_phone, mensagem)
    except WhaticketError:
        raise
    except Exception as exc:  # pragma: no cover - defensive logging
        LOGGER.exception("reminder_send_failed", appointment_id=appointment_id, error=str(exc))
        raise
    else:
        now = datetime.utcnow()
        normalized_type = reminder_type if reminder_type in REMINDER_TYPES else "custom"
        if normalized_type == "24h":
            appointment.reminder_24h_sent = now
        elif normalized_type == "1h":
            appointment.reminder_1h_sent = now
        appointment_reminders_sent_total.labels(
            company=str(appointment.company_id),
            type=normalized_type,
        ).inc()
        session.add(appointment)
        session.commit()

        session_factory = getattr(current_app, "db_session", None)
        if session_factory is None:
            raise RuntimeError("session_factory_not_configured")
        audit_service = AuditService(session_factory)
        audit_service.record(
            company_id=appointment.company_id,
            actor="agenda",
            action="appointment.reminder_sent",
            resource="appointment",
            payload={
                "appointment_id": appointment.id,
                "reminder_type": normalized_type,
                "start_time": appointment.start_time.isoformat(),
            },
        )
        return True
    finally:
        session.close()
