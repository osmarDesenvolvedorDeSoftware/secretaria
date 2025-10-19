from __future__ import annotations

from flask import Blueprint, Response, current_app, jsonify, request

from app.routes.panel_auth import require_panel_auth, require_panel_company_id
from app.services.analytics_service import AnalyticsService
from app.services.billing import BillingService


analytics_bp = Blueprint("analytics", __name__, url_prefix="/api/analytics")


def _get_analytics_service() -> AnalyticsService:
    service = getattr(current_app, "analytics_service", None)
    if service is None:
        session_factory = getattr(current_app, "db_session", None)
        redis_client = getattr(current_app, "redis", None)
        service = AnalyticsService(session_factory, redis_client)
        current_app.analytics_service = service  # type: ignore[attr-defined]
        billing = getattr(current_app, "billing_service", None)
        if billing is not None:
            billing.attach_analytics_service(service)
        else:
            billing = BillingService(session_factory, redis_client, service)
            current_app.billing_service = billing  # type: ignore[attr-defined]
    return service


def _resolve_company_id() -> int:
    company_param = request.args.get("company_id")
    if company_param is not None:
        return require_panel_company_id(company_param)
    return require_panel_company_id()


@analytics_bp.get("/summary")
@require_panel_auth
def analytics_summary():
    try:
        company_id = _resolve_company_id()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    service = _get_analytics_service()
    summary = service.get_summary(company_id)
    return jsonify(summary)


@analytics_bp.get("/history")
@require_panel_auth
def analytics_history():
    try:
        company_id = _resolve_company_id()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    period = request.args.get("period", "week").lower()
    service = _get_analytics_service()
    try:
        history = service.get_history(company_id, period)
    except ValueError:
        return jsonify({"error": "invalid_period"}), 400
    return jsonify(history)


@analytics_bp.get("/export")
@require_panel_auth
def analytics_export() -> Response:
    try:
        company_id = _resolve_company_id()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    format_ = request.args.get("format", "csv")
    service = _get_analytics_service()
    try:
        filename, content_type, payload = service.export_report(company_id, format_)
    except ValueError:
        return jsonify({"error": "unsupported_format"}), 400
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503

    response: Response = current_app.response_class(payload, mimetype=content_type)
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return response


__all__ = ["analytics_bp"]
