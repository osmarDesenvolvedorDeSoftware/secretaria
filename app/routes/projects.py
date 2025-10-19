from __future__ import annotations

from functools import wraps

from flask import Blueprint, current_app, jsonify, render_template, request
from sqlalchemy import func, select

from app import get_db_session
from app.config import settings
from app.models.personalization_config import PersonalizationConfig
from app.models.project import Project
from app.services.auth import encode_jwt, verify_jwt

bp = Blueprint("projects", __name__)


def _project_to_dict(project: Project) -> dict[str, object]:
    return {
        "id": project.id,
        "name": project.name,
        "client": project.client,
        "description": project.description,
        "status": project.status,
        "created_at": project.created_at.isoformat() if project.created_at else None,
    }


def _get_panel_config(session) -> PersonalizationConfig:
    config = (
        session.query(PersonalizationConfig)
        .order_by(PersonalizationConfig.updated_at.desc().nullslast(), PersonalizationConfig.id.asc())
        .first()
    )
    if config is None:
        config = PersonalizationConfig()
        session.add(config)
        session.flush()
    return config


def _panel_config_payload(config: PersonalizationConfig) -> dict[str, object]:
    data = config.to_dict()
    phrases = data.get("opening_phrases") or []
    if not isinstance(phrases, list):
        phrases = []
    return {
        "tone_of_voice": data.get("tone_of_voice", "amigavel"),
        "message_limit": data.get("message_limit", 5),
        "opening_phrases": phrases,
        "ai_enabled": bool(data.get("ai_enabled", True)),
    }


def _extract_token() -> str | None:
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
        token = _extract_token()
        payload = verify_jwt(token, settings.panel_jwt_secret)
        if payload is None:
            return jsonify({"error": "unauthorized"}), 401
        request.panel_identity = payload  # type: ignore[attr-defined]
        return func(*args, **kwargs)

    return wrapper


@bp.get("/painel/config")
@require_panel_auth
def get_panel_config():
    with get_db_session(current_app) as session:
        config = _get_panel_config(session)
        payload = _panel_config_payload(config)
    return jsonify(payload)


@bp.put("/painel/config")
@require_panel_auth
def update_panel_config():
    payload = request.get_json(silent=True) or {}
    tone = str(payload.get("tone_of_voice") or "amigavel").strip() or "amigavel"
    message_limit_raw = payload.get("message_limit")
    try:
        message_limit = int(message_limit_raw)
    except (TypeError, ValueError):
        message_limit = 5
    if message_limit < 1:
        message_limit = 1

    opening_raw = payload.get("opening_phrases", [])
    if isinstance(opening_raw, str):
        phrases = [line.strip() for line in opening_raw.splitlines() if line.strip()]
    elif isinstance(opening_raw, list):
        phrases = [str(item).strip() for item in opening_raw if str(item).strip()]
    else:
        phrases = []

    ai_enabled = bool(payload.get("ai_enabled", True))

    with get_db_session(current_app) as session:
        config = _get_panel_config(session)
        config.tone_of_voice = tone
        config.message_limit = message_limit
        config.opening_phrases = phrases
        config.ai_enabled = ai_enabled
        session.add(config)
        session.flush()
        response_payload = _panel_config_payload(config)

    redis_client = getattr(current_app, "redis", None)
    if redis_client is not None:
        try:
            redis_client.delete("ctx:personalization_config")  # type: ignore[arg-type]
        except Exception:
            pass
    return jsonify(response_payload)


@bp.post("/auth/token")
def issue_panel_token():
    payload = request.get_json(silent=True) or {}
    password = str(payload.get("password") or "")
    if not settings.panel_password:
        return jsonify({"error": "panel_password_not_configured"}), 503
    if password != settings.panel_password:
        return jsonify({"error": "invalid_credentials"}), 401

    token = encode_jwt({"sub": "panel"}, settings.panel_jwt_secret, settings.panel_token_ttl_seconds)
    response = jsonify(
        {
            "access_token": token,
            "token_type": "bearer",
            "expires_in": settings.panel_token_ttl_seconds,
        }
    )
    response.set_cookie(
        "panel_token",
        token,
        max_age=settings.panel_token_ttl_seconds,
        httponly=True,
        secure=False,
        samesite="Lax",
    )
    return response


@bp.get("/projects/")
@require_panel_auth
def list_projects():
    with get_db_session(current_app) as session:
        result = session.execute(
            select(Project).order_by(Project.created_at.desc(), Project.id.desc())
        )
        projects = result.scalars().all()
    return jsonify([_project_to_dict(project) for project in projects])


@bp.post("/projects/")
@require_panel_auth
def create_project():
    payload = request.get_json(silent=True) or {}
    project = Project(
        name=payload.get("name"),
        client=payload.get("client"),
        description=payload.get("description"),
        status=payload.get("status") or "ativo",
    )

    if not project.name:
        return jsonify({"error": "name is required"}), 400

    with get_db_session(current_app) as session:
        session.add(project)
        session.flush()
        project_id = project.id

    return jsonify({"ok": True, "id": project_id}), 201


@bp.put("/projects/<int:project_id>")
@require_panel_auth
def update_project(project_id: int):
    payload = request.get_json(silent=True) or {}
    with get_db_session(current_app) as session:
        project = session.get(Project, project_id)
        if project is None:
            return jsonify({"error": "not found"}), 404

        for field in ("name", "client", "description", "status"):
            if field in payload:
                setattr(project, field, payload[field])
        session.add(project)
        session.flush()
        project_id = project.id

    return jsonify({"ok": True, "id": project_id})


@bp.delete("/projects/<int:project_id>")
@require_panel_auth
def delete_project(project_id: int):
    with get_db_session(current_app) as session:
        project = session.get(Project, project_id)
        if project is None:
            return jsonify({"error": "not found"}), 404
        session.delete(project)

    return jsonify({"ok": True})


@bp.get("/projects/stats")
@require_panel_auth
def project_stats():
    with get_db_session(current_app) as session:
        rows = session.execute(
            select(Project.status, func.count()).group_by(Project.status)
        ).all()

    counts = {status: total for status, total in rows}
    return jsonify(
        {
            "ativos": counts.get("ativo", 0),
            "pausados": counts.get("pausado", 0),
            "concluidos": counts.get("concluído", counts.get("concluido", 0)),
        }
    )


@bp.get("/painel")
def painel_view():
    return render_template("painel.html")
