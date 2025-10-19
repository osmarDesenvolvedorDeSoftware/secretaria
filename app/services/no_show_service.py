from __future__ import annotations

from datetime import datetime

import structlog
from flask import current_app
from sqlalchemy.orm import Session

from app.metrics import appointment_no_show_total
from app.models import Appointment, FeedbackEvent
from app.services.audit import AuditService

LOGGER = structlog.get_logger().bind(service="no_show_service")


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


def agendar_verificacao_no_show(appointment_id: int, horario_verificacao: datetime) -> None:
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
            "check_type": "no_show",
        }
        enqueue_at = getattr(queue, "enqueue_at", None)
        if callable(enqueue_at) and horario_verificacao > datetime.utcnow():
            enqueue_at(
                horario_verificacao,
                verificar_no_show,
                appointment.id,
                job_timeout=120,
                meta=meta,
            )
        else:
            queue.enqueue(
                verificar_no_show,
                appointment.id,
                job_timeout=120,
                meta=meta,
            )
    finally:
        session.close()


def verificar_no_show(appointment_id: int) -> bool:
    session = _session()
    try:
        appointment = session.get(Appointment, appointment_id)
        if appointment is None:
            LOGGER.warning("appointment_not_found", appointment_id=appointment_id)
            return False
        if appointment.no_show_checked:
            LOGGER.info("no_show_already_checked", appointment_id=appointment_id)
            return False
        now = datetime.utcnow()
        appointment.no_show_checked = now
        if appointment.status in {"confirmed", "rescheduled", "cancelled"}:
            session.add(appointment)
            session.commit()
            return False

        appointment.status = "no_show"
        feedback = FeedbackEvent(
            company_id=appointment.company_id,
            number=appointment.client_phone,
            feedback_type="no_show",
            comment="Cliente não compareceu",
            details={"appointment_id": appointment.id},
        )
        session.add(appointment)
        session.add(feedback)
        session.commit()

        appointment_no_show_total.labels(company=str(appointment.company_id)).inc()
        session_factory = getattr(current_app, "db_session", None)
        if session_factory is None:
            raise RuntimeError("session_factory_not_configured")
        audit_service = AuditService(session_factory)
        audit_service.record(
            company_id=appointment.company_id,
            actor="agenda",
            action="appointment.no_show_detected",
            resource="appointment",
            payload={"appointment_id": appointment.id},
        )
        return True
    finally:
        session.close()
