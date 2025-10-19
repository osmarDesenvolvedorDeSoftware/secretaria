from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

import requests
import structlog
from flask import current_app, g
from sqlalchemy.orm import Session
from tenacity import before_sleep_log, retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings
from app.metrics import (
    appointments_cancelled_total,
    appointments_confirmed_total,
    appointments_latency_seconds,
    appointments_total,
)
from app.models import Appointment, Company
from app.services import no_show_service, reminder_service
from app.services.audit import AuditService


LOGGER = structlog.get_logger().bind(service="cal_service")


class CalServiceError(RuntimeError):
    """Erro genérico da integração com o Cal.com."""


class CalServiceConfigError(CalServiceError):
    """Indica ausência de configuração necessária para o tenant."""


def _session_factory():
    session_factory = getattr(current_app, "db_session", None)
    if session_factory is None:
        raise RuntimeError("session_factory_not_configured")
    return session_factory


def _get_audit_service() -> AuditService:
    audit_service = getattr(current_app, "cal_audit_service", None)
    if audit_service is None:
        audit_service = AuditService(_session_factory())
        current_app.cal_audit_service = audit_service  # type: ignore[attr-defined]
    return audit_service


def _get_company(session: Session, company_id: int) -> Company:
    company = session.get(Company, company_id)
    if company is None:
        raise CalServiceError(f"company_not_found:{company_id}")
    return company


def _ensure_credentials(company: Company) -> None:
    if not company.cal_api_key:
        raise CalServiceConfigError("cal_api_key_missing")


def _headers(company: Company, extra: Optional[dict[str, str]] = None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {company.cal_api_key}",
        "Accept": "application/json",
    }
    if extra:
        headers.update(extra)
    return headers


def _full_url(path: str) -> str:
    base = settings.cal_api_base_url.rstrip("/")
    return f"{base}/{path.lstrip('/')}"


@retry(
    retry=retry_if_exception_type(requests.RequestException),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    stop=stop_after_attempt(3),
    reraise=True,
    before_sleep=before_sleep_log(LOGGER, "warning"),
)
def _perform_request(method: str, url: str, *, headers: dict[str, str], **kwargs: Any) -> requests.Response:
    response = requests.request(
        method,
        url,
        headers=headers,
        timeout=settings.request_timeout_seconds,
        **kwargs,
    )
    if response.status_code >= 500:
        raise requests.RequestException(f"cal_unavailable:{response.status_code}")
    return response


def _parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    sanitized = value.replace("Z", "+00:00") if isinstance(value, str) else value
    return datetime.fromisoformat(sanitized)


def listar_disponibilidade(
    usuario_id: str,
    data_inicial: str,
    data_final: str,
    company_id: Optional[int] = None,
) -> list[dict[str, Any]]:
    session = _session_factory()()
    try:
        company_identifier = company_id
        if company_identifier is None:
            company_obj = getattr(g, "company", None)
            if company_obj is not None:
                company_identifier = getattr(company_obj, "id", None)
        if company_identifier is None:
            tenant = getattr(g, "tenant", None)
            company_identifier = getattr(tenant, "company_id", None)
        if company_identifier is None:
            raise CalServiceError("company_context_missing")
        company = _get_company(session, int(company_identifier))
        _ensure_credentials(company)
        params = {"userId": usuario_id, "start": data_inicial, "end": data_final}
        url = _full_url("availability")
        response = _perform_request("GET", url, headers=_headers(company), params=params)
        if response.status_code >= 400:
            raise CalServiceError(f"availability_error:{response.status_code}")
        payload = response.json() if response.content else {}
        slots: Iterable[dict[str, Any]] = payload.get("slots") or payload.get("availability") or []
        slots_list = [dict(slot) for slot in slots if isinstance(slot, dict)]
        _get_audit_service().record(
            company_id=company.id,
            actor="agenda",
            action="cal.list_availability",
            resource="cal_availability",
            payload={"user_id": usuario_id, "start": data_inicial, "end": data_final, "total": len(slots_list)},
        )
        return slots_list
    finally:
        session.close()


def criar_agendamento(
    company_id: int,
    cliente: dict[str, Any],
    horario: dict[str, Any],
    titulo: str,
    duracao: int,
    *,
    reschedule: bool = False,
    original_appointment_id: int | None = None,
) -> dict[str, Any]:
    session = _session_factory()()
    try:
        company = _get_company(session, company_id)
        _ensure_credentials(company)

        start = _parse_datetime(horario.get("start") or horario)
        end_value = horario.get("end") or (start + timedelta(minutes=int(duracao)))
        end = _parse_datetime(end_value)
        payload = {
            "title": titulo,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "duration": duracao,
            "customer": cliente,
        }
        if company.cal_default_user_id:
            payload["userId"] = company.cal_default_user_id

        appointments_total.labels(company=str(company.id)).inc()
        url = _full_url("bookings")
        started = time.monotonic()
        response = _perform_request(
            "POST",
            url,
            headers=_headers(company, {"Content-Type": "application/json"}),
            json=payload,
        )
        latency = time.monotonic() - started
        appointments_latency_seconds.labels(company=str(company.id)).observe(latency)
        if response.status_code >= 400:
            raise CalServiceError(f"booking_error:{response.status_code}")

        data = response.json() if response.content else {}
        booking = data.get("booking") or data
        booking_id = str(booking.get("id"))
        meeting_url = booking.get("meetingUrl") or booking.get("url") or booking.get("joinUrl")

        appointment = Appointment(
            company_id=company.id,
            client_name=str(cliente.get("name") or "Cliente"),
            client_phone=str(cliente.get("phone") or ""),
            start_time=start,
            end_time=end,
            title=titulo,
            cal_booking_id=booking_id,
            status="pending",
            meeting_url=meeting_url,
        )
        session.add(appointment)
        session.commit()
        session.refresh(appointment)

        if reschedule and original_appointment_id:
            previous = session.get(Appointment, original_appointment_id)
            if previous is not None:
                previous.status = "rescheduled"
                session.add(previous)
                session.commit()
                _get_audit_service().record(
                    company_id=company.id,
                    actor="agenda",
                    action="appointment.rescheduled",
                    resource="appointment",
                    payload={
                        "appointment_id": previous.id,
                        "new_appointment_id": appointment.id,
                        "booking_id": booking_id,
                    },
                )

        try:
            reminder_service.agendar_lembretes_padrao(appointment)
        except Exception as exc:  # pragma: no cover - scheduling failures shouldn't break flow
            LOGGER.warning("schedule_reminders_failed", error=str(exc), appointment_id=appointment.id)
        try:
            check_time = appointment.end_time + timedelta(minutes=30)
            no_show_service.agendar_verificacao_no_show(appointment.id, check_time)
        except Exception as exc:  # pragma: no cover - scheduling failures shouldn't break flow
            LOGGER.warning("schedule_no_show_failed", error=str(exc), appointment_id=appointment.id)

        appointments_confirmed_total.labels(company=str(company.id)).inc()

        _get_audit_service().record(
            company_id=company.id,
            actor="agenda",
            action="cal.booking_created",
            resource="cal_booking",
            payload={"booking_id": booking_id, "title": titulo, "client": cliente},
        )
        return {
            "booking_id": booking_id,
            "meeting_url": meeting_url,
            "start": start,
            "end": end,
            "appointment_id": appointment.id,
        }
    finally:
        session.close()


def cancelar_agendamento(company_id: int, booking_id: str) -> bool:
    session = _session_factory()()
    try:
        company = _get_company(session, company_id)
        _ensure_credentials(company)

        url = _full_url(f"bookings/{booking_id}")
        response = _perform_request("DELETE", url, headers=_headers(company))
        if response.status_code >= 400:
            raise CalServiceError(f"booking_cancel_error:{response.status_code}")

        appointment = (
            session.query(Appointment)
            .filter(Appointment.company_id == company.id, Appointment.cal_booking_id == booking_id)
            .first()
        )
        if appointment is not None:
            appointment.status = "cancelled"
            session.add(appointment)
        session.commit()

        appointments_cancelled_total.labels(company=str(company.id)).inc()

        _get_audit_service().record(
            company_id=company.id,
            actor="agenda",
            action="cal.booking_cancelled",
            resource="cal_booking",
            payload={"booking_id": booking_id},
        )
        return True
    finally:
        session.close()


def sincronizar_webhook(payload: dict[str, Any]) -> None:
    event = str(payload.get("event") or payload.get("type") or "").lower()
    data = payload.get("data") or {}
    metadata = payload.get("metadata") or {}
    company_id = (
        payload.get("company_id")
        or metadata.get("company_id")
        or data.get("company_id")
        or data.get("companyId")
    )
    if not company_id:
        raise CalServiceError("company_id_missing")

    session = _session_factory()()
    try:
        company = _get_company(session, int(company_id))
        _ensure_credentials(company)

        booking = data.get("booking") or data
        booking_id = str(booking.get("id")) if booking else None
        if not booking_id:
            raise CalServiceError("booking_id_missing")

        appointment = (
            session.query(Appointment)
            .filter(Appointment.company_id == company.id, Appointment.cal_booking_id == booking_id)
            .first()
        )

        status = "confirmed"
        if event.endswith("cancelled"):
            status = "cancelled"
        elif event.endswith("rescheduled"):
            status = "rescheduled"

        if appointment is None:
            start = booking.get("start") or booking.get("start_time")
            end = booking.get("end") or booking.get("end_time")
            client = booking.get("customer") or {}
            appointment = Appointment(
                company_id=company.id,
                client_name=str(client.get("name") or "Cliente"),
                client_phone=str(client.get("phone") or ""),
                start_time=_parse_datetime(start) if start else datetime.utcnow(),
                end_time=_parse_datetime(end) if end else datetime.utcnow(),
                title=str(booking.get("title") or "Agendamento"),
                cal_booking_id=booking_id,
                status=status,
                meeting_url=booking.get("meetingUrl") or booking.get("url") or booking.get("joinUrl"),
            )
        else:
            if event.endswith("rescheduled"):
                new_start = booking.get("start") or booking.get("start_time")
                new_end = booking.get("end") or booking.get("end_time")
                if new_start:
                    appointment.start_time = _parse_datetime(new_start)
                if new_end:
                    appointment.end_time = _parse_datetime(new_end)
            appointment.status = status
            meeting = booking.get("meetingUrl") or booking.get("url") or booking.get("joinUrl")
            if meeting:
                appointment.meeting_url = meeting

        session.add(appointment)
        session.commit()

        if status == "cancelled":
            appointments_cancelled_total.labels(company=str(company.id)).inc()

        _get_audit_service().record(
            company_id=company.id,
            actor="agenda",
            action=f"cal.webhook.{event or 'unknown'}",
            resource="cal_booking",
            payload={"booking_id": booking_id, "status": appointment.status},
        )
    finally:
        session.close()


__all__ = [
    "CalServiceError",
    "CalServiceConfigError",
    "listar_disponibilidade",
    "criar_agendamento",
    "cancelar_agendamento",
    "sincronizar_webhook",
]
