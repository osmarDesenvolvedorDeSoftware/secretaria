from __future__ import annotations

from http import HTTPStatus

from flask import Blueprint, current_app, jsonify, request

from app.routes.panel_auth import require_panel_auth, require_panel_company_id
from app.services.abtest_service import ABTestService
from app.services.audit import AuditService

abtest_bp = Blueprint("abtests", __name__, url_prefix="/api/abtests")


def _service() -> ABTestService:
    session_factory = getattr(current_app, "db_session", None)
    redis_client = getattr(current_app, "redis", None)
    if session_factory is None or redis_client is None:
        raise RuntimeError("service_unavailable")
    return ABTestService(session_factory, redis_client)


def _audit() -> AuditService:
    session_factory = getattr(current_app, "db_session", None)
    if session_factory is None:
        raise RuntimeError("service_unavailable")
    return AuditService(session_factory)


@abtest_bp.get("")
@require_panel_auth
def list_tests():
    try:
        company_id = require_panel_company_id(request.args.get("company_id"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), HTTPStatus.BAD_REQUEST
    try:
        service = _service()
        tests = service.list_tests(company_id)
    except RuntimeError:
        return jsonify({"error": "service_unavailable"}), HTTPStatus.SERVICE_UNAVAILABLE
    return jsonify({"items": tests})


@abtest_bp.post("")
@require_panel_auth
def create_test():
    payload = request.get_json(silent=True) or {}
    try:
        company_id = require_panel_company_id(payload.get("company_id"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), HTTPStatus.BAD_REQUEST
    try:
        service = _service()
        test = service.create_test(company_id, payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), HTTPStatus.BAD_REQUEST
    except RuntimeError:
        return jsonify({"error": "service_unavailable"}), HTTPStatus.SERVICE_UNAVAILABLE

    actor = getattr(request, "panel_identity", {}).get("sub", "panel")
    _audit().record(
        company_id=company_id,
        actor=str(actor),
        actor_type="panel",
        action="abtest_create",
        resource="ab_tests",
        payload={"test_id": test.get("id")},
        ip_address=request.remote_addr,
    )
    return jsonify(test), HTTPStatus.CREATED


@abtest_bp.get("/<int:test_id>")
@require_panel_auth
def get_test(test_id: int):
    try:
        company_id = require_panel_company_id(request.args.get("company_id"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), HTTPStatus.BAD_REQUEST
    try:
        service = _service()
        test = service.get_test(company_id, test_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), HTTPStatus.NOT_FOUND
    except RuntimeError:
        return jsonify({"error": "service_unavailable"}), HTTPStatus.SERVICE_UNAVAILABLE
    return jsonify(test)


@abtest_bp.put("/<int:test_id>")
@require_panel_auth
def update_test(test_id: int):
    payload = request.get_json(silent=True) or {}
    try:
        company_id = require_panel_company_id(payload.get("company_id"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), HTTPStatus.BAD_REQUEST
    try:
        service = _service()
        test = service.update_test(company_id, test_id, payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), HTTPStatus.BAD_REQUEST
    except RuntimeError:
        return jsonify({"error": "service_unavailable"}), HTTPStatus.SERVICE_UNAVAILABLE

    actor = getattr(request, "panel_identity", {}).get("sub", "panel")
    _audit().record(
        company_id=company_id,
        actor=str(actor),
        actor_type="panel",
        action="abtest_update",
        resource="ab_tests",
        payload={"test_id": test_id},
        ip_address=request.remote_addr,
    )
    return jsonify(test)


@abtest_bp.delete("/<int:test_id>")
@require_panel_auth
def delete_test(test_id: int):
    payload = request.get_json(silent=True) or {}
    try:
        company_id = require_panel_company_id(payload.get("company_id"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), HTTPStatus.BAD_REQUEST
    try:
        service = _service()
        service.delete_test(company_id, test_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), HTTPStatus.NOT_FOUND
    except RuntimeError:
        return jsonify({"error": "service_unavailable"}), HTTPStatus.SERVICE_UNAVAILABLE

    actor = getattr(request, "panel_identity", {}).get("sub", "panel")
    _audit().record(
        company_id=company_id,
        actor=str(actor),
        actor_type="panel",
        action="abtest_delete",
        resource="ab_tests",
        payload={"test_id": test_id},
        ip_address=request.remote_addr,
    )
    return jsonify({"deleted": True})


@abtest_bp.post("/<int:test_id>/start")
@require_panel_auth
def start_test(test_id: int):
    payload = request.get_json(silent=True) or {}
    try:
        company_id = require_panel_company_id(payload.get("company_id"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), HTTPStatus.BAD_REQUEST
    try:
        service = _service()
        test = service.start_test(company_id, test_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), HTTPStatus.BAD_REQUEST
    except RuntimeError:
        return jsonify({"error": "service_unavailable"}), HTTPStatus.SERVICE_UNAVAILABLE

    actor = getattr(request, "panel_identity", {}).get("sub", "panel")
    _audit().record(
        company_id=company_id,
        actor=str(actor),
        actor_type="panel",
        action="abtest_start",
        resource="ab_tests",
        payload={"test_id": test_id},
        ip_address=request.remote_addr,
    )
    return jsonify(test)


@abtest_bp.post("/<int:test_id>/stop")
@require_panel_auth
def stop_test(test_id: int):
    payload = request.get_json(silent=True) or {}
    try:
        company_id = require_panel_company_id(payload.get("company_id"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), HTTPStatus.BAD_REQUEST
    try:
        service = _service()
        test = service.stop_test(company_id, test_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), HTTPStatus.BAD_REQUEST
    except RuntimeError:
        return jsonify({"error": "service_unavailable"}), HTTPStatus.SERVICE_UNAVAILABLE

    actor = getattr(request, "panel_identity", {}).get("sub", "panel")
    _audit().record(
        company_id=company_id,
        actor=str(actor),
        actor_type="panel",
        action="abtest_stop",
        resource="ab_tests",
        payload={"test_id": test_id},
        ip_address=request.remote_addr,
    )
    return jsonify(test)


__all__ = ["abtest_bp"]
