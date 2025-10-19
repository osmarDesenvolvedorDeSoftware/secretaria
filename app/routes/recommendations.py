from __future__ import annotations

from http import HTTPStatus

from flask import Blueprint, current_app, jsonify, request

from app.routes.panel_auth import require_panel_auth, require_panel_company_id
from app.services.audit import AuditService
from app.services.recommendation_service import RecommendationService

recommendation_bp = Blueprint("recommendations", __name__, url_prefix="/api/recommendations")


def _get_service() -> RecommendationService:
    session_factory = getattr(current_app, "db_session", None)
    redis_client = getattr(current_app, "redis", None)
    analytics = getattr(current_app, "analytics_service", None)
    if session_factory is None or redis_client is None:
        raise RuntimeError("service_unavailable")
    return RecommendationService(session_factory, redis_client, analytics)


@recommendation_bp.post("/evaluate")
@require_panel_auth
def evaluate_recommendations():
    payload = request.get_json(silent=True) or {}
    try:
        company_id = require_panel_company_id(payload.get("company_id"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), HTTPStatus.BAD_REQUEST

    webhook_url = payload.get("webhook_url")
    try:
        service = _get_service()
        insights = service.evaluate(company_id, webhook_url=webhook_url)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), HTTPStatus.NOT_FOUND
    except RuntimeError:
        return jsonify({"error": "service_unavailable"}), HTTPStatus.SERVICE_UNAVAILABLE

    actor = getattr(request, "panel_identity", {}).get("sub", "panel")
    audit = AuditService(current_app.db_session)  # type: ignore[attr-defined]
    audit.record(
        company_id=company_id,
        actor=str(actor),
        actor_type="panel",
        action="evaluate_recommendations",
        resource="business_ai",
        payload={"webhook_url": webhook_url, "churn_score": insights.get("churn_score")},
        ip_address=request.remote_addr,
    )

    return jsonify(insights)


@recommendation_bp.get("/insights")
@require_panel_auth
def get_insights():
    try:
        company_id = require_panel_company_id(request.args.get("company_id"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), HTTPStatus.BAD_REQUEST

    try:
        service = _get_service()
        insights = service.get_insights(company_id)
    except RuntimeError:
        return jsonify({"error": "service_unavailable"}), HTTPStatus.SERVICE_UNAVAILABLE

    return jsonify(insights)


__all__ = ["recommendation_bp"]
