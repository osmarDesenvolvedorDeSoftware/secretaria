from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable

import structlog
from flask import current_app

from app.metrics import (
    appointments_auto_rescheduled_total,
    appointments_risk_high_total,
)
from app.models import Appointment, Company
from app.services import cal_service, scheduling_ai
from app.services.context_engine import ContextEngine
from app.services.tenancy import TenantContext
from app.services.whaticket import WhaticketClient, WhaticketError

LOGGER = structlog.get_logger().bind(service="auto_reschedule")

_DEFAULT_LOOKAHEAD_HOURS = 72
_DEFAULT_THRESHOLD = 0.8
_AVAILABILITY_DAYS = 14

DEFAULT_THRESHOLD = _DEFAULT_THRESHOLD
DEFAULT_LOOKAHEAD_HOURS = _DEFAULT_LOOKAHEAD_HOURS


class AutoRescheduleError(Exception):
    """Erro ao executar reagendamento inteligente."""


def _session():
    session_factory = getattr(current_app, "db_session", None)
    if session_factory is None:
        raise RuntimeError("session_factory_not_configured")
    return session_factory()


def _redis():
    redis_client = getattr(current_app, "redis", None)
    if redis_client is None:
        raise RuntimeError("redis_not_configured")
    return redis_client


def _parse_iso_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _format_human_time(moment: datetime) -> str:
    try:
        localized = moment.astimezone()
    except ValueError:
        localized = moment
    return localized.strftime("%Hh")


def _weekday_label(moment: datetime) -> str:
    labels = [
        "segunda-feira",
        "terça-feira",
        "quarta-feira",
        "quinta-feira",
        "sexta-feira",
        "sábado",
        "domingo",
    ]
    return labels[moment.weekday()] if 0 <= moment.weekday() < len(labels) else "dia útil"


def _match_availability(
    company: Company,
    appointment: Appointment,
    suggestions: Iterable[dict[str, Any]],
) -> dict[str, Any] | None:
    if not company.cal_default_user_id:
        return None

    start_range = datetime.utcnow()
    end_range = start_range + timedelta(days=_AVAILABILITY_DAYS)
    try:
        availability = cal_service.listar_disponibilidade(
            company.cal_default_user_id,
            start_range.isoformat(),
            end_range.isoformat(),
            company_id=company.id,
        )
    except cal_service.CalServiceError as exc:
        LOGGER.warning("auto_reschedule_availability_failed", company_id=company.id, error=str(exc))
        return None

    if not availability:
        return None

    matched = None
    for suggestion in suggestions:
        target_weekday = suggestion.get("weekday")
        target_hour = suggestion.get("hour")
        for slot in availability:
            slot_start = _parse_iso_datetime(slot.get("start") or slot.get("startTime"))
            if slot_start is None or slot_start <= appointment.start_time:
                continue
            if slot_start.weekday() == target_weekday and slot_start.hour == target_hour:
                matched = slot
                break
        if matched:
            break

    if matched is None:
        for slot in availability:
            slot_start = _parse_iso_datetime(slot.get("start") or slot.get("startTime"))
            if slot_start and slot_start > appointment.start_time:
                matched = slot
                break

    if matched is None:
        return None

    slot_start = _parse_iso_datetime(matched.get("start") or matched.get("startTime"))
    slot_end = _parse_iso_datetime(matched.get("end") or matched.get("endTime"))
    duration = int(matched.get("duration") or 30)
    if slot_start is None:
        return None
    if slot_end is None:
        slot_end = slot_start + timedelta(minutes=duration)
    return {
        "start": slot_start,
        "end": slot_end,
        "duration": duration,
        "raw": matched,
    }


def _build_message(appointment: Appointment, new_slot: dict[str, Any]) -> str:
    original_time = _format_human_time(appointment.start_time)
    new_start: datetime = new_slot["start"]
    new_time = _format_human_time(new_start)
    weekday_label = _weekday_label(new_start)
    human_label = new_start.strftime("%d/%m às %Hh%M")
    return (
        "Percebi que o horário das "
        f"{original_time} costuma ter mais imprevistos. "
        f"Que tal reagendarmos para {weekday_label} às {new_time}, onde há menos cancelamentos?\n"
        f"Tenho {human_label} reservado pra você. "
        "Responda 1 para confirmar ou indique outra faixa que eu verifico."
    )


def _set_reschedule_state(
    company_id: int,
    appointment: Appointment,
    new_slot: dict[str, Any],
    probability: float,
) -> None:
    redis_client = _redis()
    session_factory = getattr(current_app, "db_session", None)
    if session_factory is None:
        raise RuntimeError("session_factory_not_configured")
    tenant = TenantContext(company_id=company_id, label=str(company_id))
    context = ContextEngine(redis_client, session_factory, tenant)
    slot_start: datetime = new_slot["start"]
    slot_end: datetime = new_slot["end"]
    option = {
        "start": slot_start.isoformat(),
        "end": slot_end.isoformat(),
        "duration": int(new_slot["duration"]),
        "label": slot_start.strftime("%d/%m às %Hh%M").lower(),
    }
    payload = {
        "phase": "awaiting_reschedule",
        "options": [option],
        "title": appointment.title or f"Reunião com {appointment.client_name or 'cliente'}",
        "client_name": appointment.client_name or appointment.client_phone,
        "original_appointment_id": appointment.id,
        "risk_flagged": True,
        "risk_probability": probability,
    }
    context.set_agenda_state(appointment.client_phone, payload)


def executar_reagendamento(
    company_id: int,
    *,
    threshold: float = _DEFAULT_THRESHOLD,
    lookahead_hours: int = _DEFAULT_LOOKAHEAD_HOURS,
) -> dict[str, Any]:
    session = _session()
    try:
        company = session.get(Company, company_id)
        if company is None:
            raise AutoRescheduleError("company_not_found")

        now = datetime.utcnow()
        window_end = now + timedelta(hours=max(lookahead_hours, 1))
        upcoming: list[Appointment] = (
            session.query(Appointment)
            .filter(
                Appointment.company_id == company_id,
                Appointment.start_time >= now,
                Appointment.start_time <= window_end,
                Appointment.status.in_(["pending", "confirmed", "rescheduled"]),
            )
            .order_by(Appointment.start_time.asc())
            .all()
        )

        if not upcoming:
            return {
                "company_id": company_id,
                "processed": 0,
                "results": [],
            }

        tenant = TenantContext(company_id=company_id, label=str(company_id))
        whaticket = WhaticketClient(_redis(), tenant)
        results: list[dict[str, Any]] = []

        for appointment in upcoming:
            probability = scheduling_ai.prever_no_show(appointment)
            reminder_pending = (
                appointment.reminder_24h_sent is not None
                and appointment.confirmed_at is None
                and appointment.start_time - now <= timedelta(hours=24)
            )
            is_high_risk = probability >= threshold or reminder_pending
            if not is_high_risk:
                continue

            appointments_risk_high_total.labels(company=str(company_id)).inc()
            suggestions = scheduling_ai.sugerir_horarios_otimizados(company_id)
            matched_slot = _match_availability(company, appointment, suggestions)
            if matched_slot is None:
                results.append(
                    {
                        "appointment_id": appointment.id,
                        "status": "no_slot_available",
                        "probability": probability,
                    }
                )
                continue

            try:
                message = _build_message(appointment, matched_slot)
                whaticket.send_text(appointment.client_phone, message)
            except WhaticketError as exc:
                LOGGER.warning(
                    "auto_reschedule_message_failed",
                    appointment_id=appointment.id,
                    error=str(exc),
                )
                results.append(
                    {
                        "appointment_id": appointment.id,
                        "status": "delivery_failed",
                        "probability": probability,
                        "suggested_start": matched_slot["start"].isoformat(),
                    }
                )
                continue

            appointments_auto_rescheduled_total.labels(company=str(company_id)).inc()
            _set_reschedule_state(company_id, appointment, matched_slot, probability)

            results.append(
                {
                    "appointment_id": appointment.id,
                    "status": "message_sent",
                    "probability": probability,
                    "suggested_start": matched_slot["start"].isoformat(),
                    "suggested_end": matched_slot["end"].isoformat(),
                }
            )

        return {
            "company_id": company_id,
            "processed": len(results),
            "results": results,
        }
    finally:
        session.close()
