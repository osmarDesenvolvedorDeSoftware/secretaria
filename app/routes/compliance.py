from __future__ import annotations

import csv
import io
from http import HTTPStatus
from flask import Blueprint, Response, current_app, jsonify, request
from sqlalchemy import delete

from app.config import settings
from app.models import Conversation, CustomerContext, DeliveryLog, FeedbackEvent
from app.routes.panel_auth import require_panel_auth, require_panel_company_id
from app.services.audit import AuditService
from app.services.tenancy import namespaced_key
from app.utils.pii import mask_phone

compliance_bp = Blueprint("compliance", __name__, url_prefix="/api/compliance")


@compliance_bp.post("/export_data")
@require_panel_auth
def export_data() -> Response:
    payload = request.get_json(silent=True) or {}
    try:
        company_id = require_panel_company_id(payload.get("company_id"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), HTTPStatus.BAD_REQUEST

    number = payload.get("number")
    if not number:
        return jsonify({"error": "number_required"}), HTTPStatus.BAD_REQUEST

    export_format = (payload.get("format") or "json").lower()
    session_factory = getattr(current_app, "db_session", None)
    if session_factory is None:
        return jsonify({"error": "database_unavailable"}), HTTPStatus.SERVICE_UNAVAILABLE

    session = session_factory()
    try:
        conversations = (
            session.query(Conversation)
            .filter(Conversation.company_id == company_id, Conversation.number == number)
            .all()
        )
        contexts = (
            session.query(CustomerContext)
            .filter(CustomerContext.company_id == company_id, CustomerContext.number == number)
            .all()
        )
        feedbacks = (
            session.query(FeedbackEvent)
            .filter(FeedbackEvent.company_id == company_id, FeedbackEvent.number == number)
            .all()
        )
        deliveries = (
            session.query(DeliveryLog)
            .filter(DeliveryLog.company_id == company_id, DeliveryLog.number == number)
            .all()
        )

        payload_json = {
            "company_id": company_id,
            "number": mask_phone(number),
            "conversations": [conv.to_dict() for conv in conversations],
            "contexts": [ctx.to_dict() for ctx in contexts],
            "feedbacks": [event.to_dict() for event in feedbacks],
            "delivery_logs": [log.to_dict() for log in deliveries],
        }

        audit = AuditService(session_factory)
        actor = getattr(request, "panel_identity", {}).get("sub", "panel")
        audit.record(
            company_id=company_id,
            actor=str(actor),
            actor_type="panel",
            action="export_data",
            resource="compliance",
            payload={"number": mask_phone(number), "format": export_format},
            ip_address=request.remote_addr,
        )

        if export_format == "json":
            return jsonify(payload_json)

        if export_format == "csv":
            stream = io.StringIO()
            writer = csv.writer(stream)
            writer.writerow(["section", "content"])
            for section, items in payload_json.items():
                if section in {"company_id", "number"}:
                    writer.writerow([section, items])
                    continue
                for item in items:
                    writer.writerow([section, item])
            response = current_app.response_class(stream.getvalue(), mimetype="text/csv")
            response.headers["Content-Disposition"] = "attachment; filename=compliance_export.csv"
            return response

        return jsonify({"error": "unsupported_format"}), HTTPStatus.BAD_REQUEST
    finally:
        session.close()


@compliance_bp.post("/delete_data")
@require_panel_auth
def delete_data() -> Response:
    payload = request.get_json(silent=True) or {}
    try:
        company_id = require_panel_company_id(payload.get("company_id"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), HTTPStatus.BAD_REQUEST

    number = payload.get("number")
    if not number:
        return jsonify({"error": "number_required"}), HTTPStatus.BAD_REQUEST

    session_factory = getattr(current_app, "db_session", None)
    if session_factory is None:
        return jsonify({"error": "database_unavailable"}), HTTPStatus.SERVICE_UNAVAILABLE

    session = session_factory()
    try:
        total_deleted = {
            "conversations": int(
                session.execute(
                    delete(Conversation).where(
                        Conversation.company_id == company_id,
                        Conversation.number == number,
                    )
                ).rowcount
                or 0
            ),
            "contexts": int(
                session.execute(
                    delete(CustomerContext).where(
                        CustomerContext.company_id == company_id,
                        CustomerContext.number == number,
                    )
                ).rowcount
                or 0
            ),
            "feedbacks": int(
                session.execute(
                    delete(FeedbackEvent).where(
                        FeedbackEvent.company_id == company_id,
                        FeedbackEvent.number == number,
                    )
                ).rowcount
                or 0
            ),
            "delivery_logs": int(
                session.execute(
                    delete(DeliveryLog).where(
                        DeliveryLog.company_id == company_id,
                        DeliveryLog.number == number,
                    )
                ).rowcount
                or 0
            ),
        }
        session.commit()

        redis_client = getattr(current_app, "redis", None)
        if redis_client is not None:
            redis_client.delete(namespaced_key(company_id, "feedback", "number", number))

        audit = AuditService(session_factory)
        actor = getattr(request, "panel_identity", {}).get("sub", "panel")
        audit.record(
            company_id=company_id,
            actor=str(actor),
            actor_type="panel",
            action="delete_data",
            resource="compliance",
            payload={"number": mask_phone(number), "deleted": total_deleted},
            ip_address=request.remote_addr,
        )

        return jsonify({"deleted": total_deleted, "number": mask_phone(number)})
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@compliance_bp.get("/policies")
@require_panel_auth
def compliance_policies() -> Response:
    try:
        company_id = require_panel_company_id()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), HTTPStatus.BAD_REQUEST

    policies = {
        "company_id": company_id,
        "retention_days": {
            "contexts": settings.retention_days_contexts,
            "feedback": settings.retention_days_feedback,
            "ab_events": settings.retention_days_ab_events,
        },
        "cache_ttl_seconds": {
            "context_history": settings.context_ttl,
            "business_ai_insights": settings.business_ai_insights_ttl,
        },
    }
    return jsonify(policies)


__all__ = ["compliance_bp"]
