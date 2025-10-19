from __future__ import annotations

from http import HTTPStatus
from flask import Blueprint, current_app, jsonify, request

from app.config import settings
from app.models import FeedbackEvent
from app.routes.panel_auth import require_panel_auth, require_panel_company_id
from app.services.audit import AuditService
from app.services.tenancy import namespaced_key
from app.utils.pii import mask_phone, mask_text

feedback_bp = Blueprint("feedback", __name__, url_prefix="/api/feedback")


@feedback_bp.post("/ingest")
@require_panel_auth
def ingest_feedback():
    payload = request.get_json(silent=True) or {}
    try:
        company_id = require_panel_company_id(payload.get("company_id"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), HTTPStatus.BAD_REQUEST

    number = payload.get("number")
    feedback_type = (payload.get("feedback_type") or "").lower()
    score = payload.get("score")
    comment = payload.get("comment")
    metadata = payload.get("metadata") or {}

    if not number:
        return jsonify({"error": "number_required"}), HTTPStatus.BAD_REQUEST
    if feedback_type not in {"thumbs_up", "thumbs_down", "nps"}:
        return jsonify({"error": "invalid_feedback_type"}), HTTPStatus.BAD_REQUEST
    if feedback_type == "nps":
        try:
            score = int(score)
        except (TypeError, ValueError):
            return jsonify({"error": "score_required"}), HTTPStatus.BAD_REQUEST
        if score < 0 or score > 10:
            return jsonify({"error": "score_out_of_range"}), HTTPStatus.BAD_REQUEST

    session_factory = getattr(current_app, "db_session", None)
    if session_factory is None:
        return jsonify({"error": "database_unavailable"}), HTTPStatus.SERVICE_UNAVAILABLE

    session = session_factory()
    try:
        event = FeedbackEvent(
            company_id=company_id,
            number=str(number),
            channel=str(payload.get("channel") or "whatsapp"),
            feedback_type=feedback_type,
            score=score,
            comment=mask_text(comment or "") if comment else None,
            details=metadata if isinstance(metadata, dict) else {},
        )
        retention_days = max(settings.retention_days_feedback, 1)
        event.expires_at = FeedbackEvent.calculate_expiration(retention_days)
        session.add(event)
        session.commit()
        _update_feedback_aggregates(company_id, event)

        audit = AuditService(session_factory)
        actor = getattr(request, "panel_identity", {}).get("sub", "panel")
        audit.record(
            company_id=company_id,
            actor=str(actor),
            actor_type="panel",
            action="feedback_ingest",
            resource="feedback_events",
            payload={
                "feedback_type": feedback_type,
                "score": score,
                "number": mask_phone(number),
            },
            ip_address=request.remote_addr,
        )

        return (
            jsonify(
                {
                    "id": event.id,
                    "company_id": company_id,
                    "number": mask_phone(number),
                    "feedback_type": feedback_type,
                    "score": score,
                    "comment": event.comment,
                }
            ),
            HTTPStatus.CREATED,
        )
    except Exception as exc:
        session.rollback()
        current_app.logger.exception("feedback_ingest_failed", exc_info=exc)
        return jsonify({"error": "feedback_store_failed"}), HTTPStatus.INTERNAL_SERVER_ERROR
    finally:
        session.close()


def _update_feedback_aggregates(company_id: int, event: FeedbackEvent) -> None:
    redis_client = getattr(current_app, "redis", None)
    if redis_client is None:
        return
    aggregate_key = namespaced_key(company_id, "feedback", "aggregate")
    try:
        if event.feedback_type == "thumbs_up":
            redis_client.hincrby(aggregate_key, "positive", 1)
        elif event.feedback_type == "thumbs_down":
            redis_client.hincrby(aggregate_key, "negative", 1)
        elif event.feedback_type == "nps" and event.score is not None:
            redis_client.hincrby(aggregate_key, "nps_total", int(event.score))
            redis_client.hincrby(aggregate_key, "nps_count", 1)
        redis_client.expire(aggregate_key, settings.context_ttl)

        number_key = namespaced_key(company_id, "feedback", "number", event.number)
        redis_client.hincrby(number_key, event.feedback_type, 1)
        redis_client.expire(number_key, settings.context_ttl)
    except Exception:
        current_app.logger.debug("feedback_aggregate_update_failed", company_id=company_id)


__all__ = ["feedback_bp"]
