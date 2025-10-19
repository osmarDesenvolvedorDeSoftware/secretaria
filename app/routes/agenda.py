from __future__ import annotations

import hmac
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any

import structlog
from flask import Blueprint, current_app, jsonify, request

from app.metrics import webhook_received_counter
from app.models import Appointment, Company, FeedbackEvent
from app.routes.panel_auth import require_panel_auth, require_panel_company_id
from app.services import (
    auto_reschedule_service,
    cal_service,
    followup_service,
    reminder_service,
    scheduling_ai,
)
from app.services.whaticket import WhaticketError


agenda_bp = Blueprint("agenda", __name__, url_prefix="/api/agenda")

LOGGER = structlog.get_logger().bind(endpoint="agenda")


def _resolve_company_id(value: Any | None = None) -> int:
    try:
        return require_panel_company_id(value)
    except ValueError as exc:
        raise ValueError(str(exc))


@agenda_bp.get("/availability")
@require_panel_auth
def get_availability():
    user_id = request.args.get("user_id")
    start = request.args.get("start")
    end = request.args.get("end")
    if not user_id or not start or not end:
        return jsonify({"error": "missing_parameters"}), 400

    company_param = request.args.get("company_id")
    try:
        company_id = _resolve_company_id(company_param)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        slots = cal_service.listar_disponibilidade(user_id, start, end, company_id=company_id)
    except cal_service.CalServiceConfigError:
        return jsonify({"error": "cal_configuration_missing"}), 503
    except cal_service.CalServiceError as exc:
        LOGGER.warning("availability_error", error=str(exc), company_id=company_id)
        return jsonify({"error": "cal_unavailable"}), 502

    return jsonify({"slots": slots})


@agenda_bp.get("/appointments")
@require_panel_auth
def list_appointments():
    try:
        company_id = _resolve_company_id(request.args.get("company_id"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    session = current_app.db_session()  # type: ignore[attr-defined]
    try:
        items = (
            session.query(Appointment)
            .filter(Appointment.company_id == company_id)
            .order_by(Appointment.start_time.asc())
            .all()
        )
        payload = [item.to_dict() for item in items]
        relevant = [item for item in items if item.status != "cancelled"]
        total = len(relevant)
        confirmed = sum(1 for item in relevant if item.status == "confirmed")
        attendance_rate = confirmed / total if total else 0.0
        return jsonify({"appointments": payload, "attendance_rate": attendance_rate})
    finally:
        session.close()


@agenda_bp.get("/followups")
@require_panel_auth
def list_followups():
    try:
        company_id = _resolve_company_id(request.args.get("company_id"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    status_filter = (request.args.get("status") or "all").lower()
    session = current_app.db_session()  # type: ignore[attr-defined]
    try:
        records = (
            session.query(Appointment)
            .filter(
                Appointment.company_id == company_id,
                Appointment.allow_followup.is_(True),
                Appointment.followup_sent_at.isnot(None),
            )
            .order_by(Appointment.followup_sent_at.desc())
            .all()
        )
        feedback_events = (
            session.query(FeedbackEvent)
            .filter(
                FeedbackEvent.company_id == company_id,
                FeedbackEvent.feedback_type == "followup_text",
            )
            .order_by(FeedbackEvent.created_at.desc())
            .all()
        )
        feedback_map: dict[int, str] = {}
        for event in feedback_events:
            details = event.details or {}
            appointment_ref = details.get("appointment_id")
            if appointment_ref is None:
                continue
            try:
                appointment_key = int(appointment_ref)
            except (TypeError, ValueError):
                continue
            if appointment_key not in feedback_map:
                feedback_map[appointment_key] = event.comment or ""
        total_sent = len(records)
        positive = sum(1 for item in records if (item.followup_response or "").lower() == "positive")
        negative = sum(1 for item in records if (item.followup_response or "").lower() == "negative")
        feedback = sum(1 for item in records if (item.followup_response or "").lower() == "feedback")
        responded = positive + negative
        pending = total_sent - responded - feedback
        response_rate = responded / total_sent if total_sent else 0.0

        if status_filter == "positive":
            filtered = [item for item in records if (item.followup_response or "").lower() == "positive"]
        elif status_filter == "negative":
            filtered = [item for item in records if (item.followup_response or "").lower() == "negative"]
        elif status_filter == "pending":
            filtered = [
                item
                for item in records
                if not item.followup_response or (item.followup_response or "").lower() not in {"positive", "negative", "feedback"}
            ]
        else:
            filtered = records

        cutoff = datetime.utcnow() - timedelta(days=30)
        history_buckets: dict[str, dict[str, int]] = {}
        for item in records:
            sent_at = item.followup_sent_at
            if not sent_at:
                continue
            try:
                normalized = sent_at.astimezone() if sent_at.tzinfo is not None else sent_at
            except ValueError:
                normalized = sent_at
            comparable = normalized.replace(tzinfo=None) if normalized.tzinfo is not None else normalized
            if comparable < cutoff:
                continue
            day = comparable.date().isoformat()
            bucket = history_buckets.setdefault(day, {"positive": 0, "negative": 0})
            response_value = (item.followup_response or "").lower()
            if response_value == "positive":
                bucket["positive"] += 1
            elif response_value == "negative":
                bucket["negative"] += 1

        history = [
            {"date": date, "positive": data["positive"], "negative": data["negative"]}
            for date, data in sorted(history_buckets.items())
        ]

        appointments_payload: list[dict[str, object]] = []
        for item in filtered[:100]:
            payload = item.to_dict()
            payload["followup_feedback"] = feedback_map.get(item.id, "")
            appointments_payload.append(payload)

        return jsonify(
            {
                "response_rate": response_rate,
                "counts": {
                    "positive": positive,
                    "negative": negative,
                    "pending": max(pending, 0),
                    "feedback": feedback,
                    "total": total_sent,
                },
                "appointments": appointments_payload,
                "history": history,
            }
        )
    finally:
        session.close()


@agenda_bp.get("/insights")
@require_panel_auth
def get_insights():
    try:
        company_id = _resolve_company_id(request.args.get("company_id"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    insights = scheduling_ai.obter_insights(company_id)
    if not insights.get("heatmap"):
        try:
            insights = scheduling_ai.analisar_padroes(company_id)
        except Exception as exc:  # pragma: no cover - log apenas
            LOGGER.warning("agenda_insights_failed", company_id=company_id, error=str(exc))
            return jsonify({"error": "analysis_failed"}), 500

    return jsonify(insights)


@agenda_bp.post("/book")
@require_panel_auth
def book_slot():
    payload = request.get_json(silent=True) or {}
    try:
        company_id = _resolve_company_id(payload.get("company_id"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    cliente = payload.get("client") or {}
    horario = payload.get("horario") or {}
    titulo = str(payload.get("titulo") or "Reunião")
    try:
        duracao = int(payload.get("duracao") or 30)
    except (TypeError, ValueError):
        duracao = 30

    try:
        result = cal_service.criar_agendamento(company_id, cliente, horario, titulo, duracao)
    except cal_service.CalServiceConfigError:
        return jsonify({"error": "cal_configuration_missing"}), 503
    except cal_service.CalServiceError as exc:
        LOGGER.warning("book_error", error=str(exc), company_id=company_id)
        return jsonify({"error": "cal_unavailable"}), 502

    return jsonify(result)


@agenda_bp.post("/auto-reschedule")
@require_panel_auth
def trigger_auto_reschedule():
    payload = request.get_json(silent=True) or {}
    try:
        company_id = _resolve_company_id(payload.get("company_id"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    threshold_value = payload.get("threshold")
    lookahead_value = payload.get("lookahead_hours")
    try:
        threshold = (
            float(threshold_value)
            if threshold_value is not None
            else auto_reschedule_service.DEFAULT_THRESHOLD
        )
    except (TypeError, ValueError):
        threshold = auto_reschedule_service.DEFAULT_THRESHOLD
    try:
        lookahead_hours = (
            int(lookahead_value)
            if lookahead_value is not None
            else auto_reschedule_service.DEFAULT_LOOKAHEAD_HOURS
        )
    except (TypeError, ValueError):
        lookahead_hours = auto_reschedule_service.DEFAULT_LOOKAHEAD_HOURS

    try:
        result = auto_reschedule_service.executar_reagendamento(
            company_id,
            threshold=threshold,
            lookahead_hours=lookahead_hours,
        )
    except auto_reschedule_service.AutoRescheduleError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(result)


@agenda_bp.post("/cancel")
@require_panel_auth
def cancel_booking():
    payload = request.get_json(silent=True) or {}
    try:
        company_id = _resolve_company_id(payload.get("company_id"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    booking_id = payload.get("booking_id")
    if not booking_id:
        return jsonify({"error": "missing_booking_id"}), 400

    try:
        cal_service.cancelar_agendamento(company_id, str(booking_id))
    except cal_service.CalServiceError as exc:
        LOGGER.warning("cancel_error", error=str(exc), company_id=company_id)
        return jsonify({"error": "cal_unavailable"}), 502

    return jsonify({"cancelled": True})


@agenda_bp.post("/followups/<int:appointment_id>/resend")
@require_panel_auth
def resend_followup(appointment_id: int):
    session = current_app.db_session()  # type: ignore[attr-defined]
    try:
        appointment = session.get(Appointment, appointment_id)
    finally:
        session.close()

    if appointment is None:
        return jsonify({"error": "appointment_not_found"}), 404

    try:
        success = followup_service.enviar_followup(appointment_id)
    except WhaticketError as exc:
        LOGGER.warning("followup_resend_failed", appointment_id=appointment_id, error=str(exc))
        return jsonify({"error": "followup_failed"}), 502
    except Exception as exc:  # pragma: no cover - defensive fallback
        LOGGER.warning("followup_resend_unexpected", appointment_id=appointment_id, error=str(exc))
        return jsonify({"error": "followup_failed"}), 500

    if not success:
        return jsonify({"error": "followup_not_allowed"}), 400

    return jsonify({"followup_sent": True})


@agenda_bp.post("/appointments/<int:appointment_id>/reminder")
@require_panel_auth
def trigger_manual_reminder(appointment_id: int):
    try:
        success = reminder_service.enviar_lembrete(appointment_id, "manual")
    except WhaticketError as exc:
        LOGGER.warning("manual_reminder_failed", appointment_id=appointment_id, error=str(exc))
        return jsonify({"error": "reminder_failed"}), 502
    except Exception as exc:  # pragma: no cover - defensive fallback
        LOGGER.warning("manual_reminder_unexpected", appointment_id=appointment_id, error=str(exc))
        return jsonify({"error": "reminder_failed"}), 500

    if not success:
        return jsonify({"error": "appointment_not_found"}), 404

    return jsonify({"reminder_sent": True})


@agenda_bp.post("/webhook/cal")
def cal_webhook():
    raw_body = request.get_data()
    payload = request.get_json(silent=True) or {}
    company_header = request.headers.get("X-Cal-Company")
    company_id = company_header or payload.get("company_id")
    if not company_id:
        webhook_received_counter.labels(company="unknown", status="rejected").inc()
        return jsonify({"error": "company_id_missing"}), 400

    session = current_app.db_session()  # type: ignore[attr-defined]
    try:
        company = session.get(Company, int(company_id))
    finally:
        session.close()

    if company is None or not company.cal_webhook_secret:
        webhook_received_counter.labels(company=str(company_id), status="rejected").inc()
        return jsonify({"error": "company_not_configured"}), 404

    signature = request.headers.get("X-Cal-Signature", "")
    expected = hmac.new(company.cal_webhook_secret.encode(), raw_body, sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        webhook_received_counter.labels(company=str(company.id), status="rejected").inc()
        return jsonify({"error": "invalid_signature"}), 401

    try:
        cal_service.sincronizar_webhook(payload)
    except cal_service.CalServiceError as exc:
        LOGGER.warning("webhook_sync_error", error=str(exc), company_id=company.id)
        webhook_received_counter.labels(company=str(company.id), status="rejected").inc()
        return jsonify({"error": "processing_error"}), 400

    webhook_received_counter.labels(company=str(company.id), status="accepted").inc()
    return jsonify({"received": True})


__all__ = ["agenda_bp"]
