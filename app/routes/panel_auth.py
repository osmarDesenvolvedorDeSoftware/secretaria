from __future__ import annotations

from functools import wraps
from typing import Any

from flask import jsonify, request

from app.config import settings
from app.services.auth import verify_jwt


def extract_panel_token() -> str | None:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1].strip()
    cookie_token = request.cookies.get("panel_token")
    if cookie_token:
        return cookie_token
    return None


def require_panel_auth(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if settings.internal_sync_mode and request.headers.get("X-Internal-Request") == "github_sync":
            request.panel_identity = {"scope": "internal", "company_id": settings.default_company_id}  # type: ignore[attr-defined]
            request.panel_scope = "internal"  # type: ignore[attr-defined]
            request.panel_company_id = settings.default_company_id  # type: ignore[attr-defined]
            return func(*args, **kwargs)
        token = extract_panel_token()
        payload = verify_jwt(token, settings.panel_jwt_secret)
        if payload is None:
            return jsonify({"error": "unauthorized"}), 401
        request.panel_identity = payload  # type: ignore[attr-defined]
        request.panel_scope = payload.get("scope", "panel:admin")  # type: ignore[attr-defined]
        request.panel_company_id = payload.get("company_id")  # type: ignore[attr-defined]
        return func(*args, **kwargs)

    return wrapper


def require_panel_company_id(value: Any | None = None) -> int:
    candidate = value if value is not None else getattr(request, "panel_company_id", None)
    if candidate is None:
        raise ValueError("company_id_missing")
    try:
        return int(candidate)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_company_id") from exc


__all__ = ["extract_panel_token", "require_panel_auth", "require_panel_company_id"]
