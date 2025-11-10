from __future__ import annotations

from flask import Blueprint, current_app, jsonify, render_template, request
from redis import Redis
from sqlalchemy import func, select

from app import get_db_session
from app.config import settings
from app.models import Company, PersonalizationConfig, Plan, Project, Subscription
from app.services import project_sync_service
from app.services.auth import encode_jwt
from app.services.billing import BillingService
from app.services.provisioner import ProvisionerService, ProvisioningPayload
from app.services.tenancy import TenantContext
from app.routes.panel_auth import require_panel_auth, require_panel_company_id

bp = Blueprint("projects", __name__)


def _project_to_dict(project: Project) -> dict[str, object]:
    return {
        "id": project.id,
        "name": project.name,
        "client": project.client,
        "description": project.description,
        "status": project.status,
        "github_url": project.github_url,
        "created_at": project.created_at.isoformat() if project.created_at else None,
    }


def _require_company_id(value: object | None = None) -> int:
    return require_panel_company_id(value)


def _get_billing_service() -> BillingService:
    billing = getattr(current_app, "billing_service", None)
    if billing is None:
        analytics_service = getattr(current_app, "analytics_service", None)
        billing = BillingService(current_app.db_session, current_app.redis, analytics_service)  # type: ignore[attr-defined]
        current_app.billing_service = billing  # type: ignore[attr-defined]
    return billing


def _get_panel_config(session, company_id: int) -> PersonalizationConfig:
    config = (
        session.query(PersonalizationConfig)
        .filter(PersonalizationConfig.company_id == company_id)
        .order_by(PersonalizationConfig.updated_at.desc().nullslast(), PersonalizationConfig.id.asc())
        .first()
    )
    if config is None:
        config = PersonalizationConfig(company_id=company_id)
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
        "formality_level": int(data.get("formality_level", 50) or 50),
        "empathy_level": int(data.get("empathy_level", 70) or 70),
        "adaptive_humor": bool(data.get("adaptive_humor", True)),
    }


@bp.post("/api/tenants/provision")
@require_panel_auth
def provision_tenant():
    payload = request.get_json(silent=True) or {}
    try:
        provisioning_payload = ProvisioningPayload.from_dict(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    engine = getattr(current_app, "db_engine", None)
    redis_client = getattr(current_app, "redis", None)
    if engine is None:
        return jsonify({"error": "database_engine_not_initialized"}), 503
    if redis_client is None:
        redis_client = Redis.from_url(settings.redis_url, decode_responses=True)

    with get_db_session(current_app) as session:
        service = ProvisionerService(session, engine, redis_client)
        try:
            result = service.provision(provisioning_payload)
        except ValueError as exc:
            message = str(exc)
            status_code = 409 if message == "domain_in_use" else 400
            return jsonify({"error": message}), status_code

    return jsonify({"ok": True, **result}), 201


@bp.get("/painel/config")
@require_panel_auth
def get_panel_config():
    try:
        company_id = _require_company_id()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    with get_db_session(current_app) as session:
        config = _get_panel_config(session, company_id)
        payload = _panel_config_payload(config)
    return jsonify({"company_id": company_id, **payload})


@bp.put("/painel/config")
@require_panel_auth
def update_panel_config():
    payload = request.get_json(silent=True) or {}
    try:
        company_id = _require_company_id()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
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
    formality_level = payload.get("formality_level", 50)
    empathy_level = payload.get("empathy_level", 70)
    adaptive_humor = bool(payload.get("adaptive_humor", True))

    try:
        formality_level = int(formality_level)
    except (TypeError, ValueError):
        formality_level = 50
    formality_level = max(0, min(100, formality_level))

    try:
        empathy_level = int(empathy_level)
    except (TypeError, ValueError):
        empathy_level = 70
    empathy_level = max(0, min(100, empathy_level))

    with get_db_session(current_app) as session:
        config = _get_panel_config(session, company_id)
        config.tone_of_voice = tone
        config.message_limit = message_limit
        config.opening_phrases = phrases
        config.ai_enabled = ai_enabled
        config.formality_level = formality_level
        config.empathy_level = empathy_level
        config.adaptive_humor = adaptive_humor
        session.add(config)
        session.flush()
        response_payload = _panel_config_payload(config)

    redis_client = getattr(current_app, "redis", None)
    if redis_client is not None:
        try:
            tenant = TenantContext(company_id=company_id, label=str(company_id))
            redis_client.delete(tenant.namespaced_key("ctx", "personalization_config"))  # type: ignore[arg-type]
        except Exception:
            pass
    return jsonify({"company_id": company_id, **response_payload})


@bp.post("/auth/token")
def issue_panel_token():
    payload = request.get_json(silent=True) or {}
    password = str(payload.get("password") or "")
    company_id = payload.get("company_id")
    if not settings.panel_password:
        return jsonify({"error": "panel_password_not_configured"}), 503
    if password != settings.panel_password:
        return jsonify({"error": "invalid_credentials"}), 401

    try:
        company_id_int = int(company_id)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_company_id"}), 400

    with get_db_session(current_app) as session:
        company = session.get(Company, company_id_int)
        if company is None:
            return jsonify({"error": "company_not_found"}), 404

    token_payload = {
        "sub": "panel",
        "scope": "panel:admin",
        "company_id": company_id_int,
    }
    token = encode_jwt(token_payload, settings.panel_jwt_secret, settings.panel_token_ttl_seconds)
    response = jsonify(
        {
            "access_token": token,
            "token_type": "bearer",
            "expires_in": settings.panel_token_ttl_seconds,
            "company_id": company_id_int,
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


@bp.get("/painel/planos")
@require_panel_auth
def list_plans():
    with get_db_session(current_app) as session:
        plans = session.execute(select(Plan).order_by(Plan.preco.asc(), Plan.id.asc())).scalars().all()
    return jsonify([plan.to_dict() for plan in plans])


@bp.get("/painel/empresas")
@require_panel_auth
def list_companies():
    billing = _get_billing_service()
    with get_db_session(current_app) as session:
        companies = session.execute(select(Company).order_by(Company.created_at.desc())).scalars().all()
    summaries = [billing.summarize_company(company.id) for company in companies]
    return jsonify(summaries)


@bp.post("/painel/empresas")
@require_panel_auth
def create_company():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name") or "").strip()
    domain = str(payload.get("domain") or "").strip().lower()
    status = str(payload.get("status") or "ativo").lower()
    plan_id_raw = payload.get("plan_id")
    ciclo = str(payload.get("ciclo") or "mensal").lower()
    if not name or not domain:
        return jsonify({"error": "name_and_domain_required"}), 400
    if status not in {"ativo", "suspenso", "cancelado"}:
        return jsonify({"error": "invalid_status"}), 400

    with get_db_session(current_app) as session:
        exists = session.execute(
            select(func.count()).select_from(Company).where(func.lower(Company.domain) == domain)
        ).scalar()
        if exists:
            return jsonify({"error": "domain_in_use"}), 409
        company = Company(name=name, domain=domain, status=status)
        session.add(company)
        session.flush()
        company_id = company.id

    billing = _get_billing_service()
    if plan_id_raw is not None:
        try:
            billing.assign_plan(int(company_id), int(plan_id_raw), ciclo=ciclo, status=status)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    summary = billing.summarize_company(company_id)
    return jsonify(summary), 201


@bp.put("/painel/empresas/<int:company_id>")
@require_panel_auth
def update_company(company_id: int):
    payload = request.get_json(silent=True) or {}
    plan_id_raw = payload.get("plan_id")
    ciclo = str(payload.get("ciclo") or "mensal").lower()
    status_value = None
    with get_db_session(current_app) as session:
        company = session.get(Company, company_id)
        if company is None:
            return jsonify({"error": "not_found"}), 404
        if "name" in payload:
            company.name = str(payload["name"]).strip() or company.name
        if "domain" in payload:
            domain = str(payload["domain"] or "").strip().lower()
            if not domain:
                return jsonify({"error": "invalid_domain"}), 400
            conflict = session.execute(
                select(func.count())
                .select_from(Company)
                .where(func.lower(Company.domain) == domain, Company.id != company_id)
            ).scalar()
            if conflict:
                return jsonify({"error": "domain_in_use"}), 409
            company.domain = domain
        if "status" in payload:
            status = str(payload["status"] or "ativo").lower()
            if status not in {"ativo", "suspenso", "cancelado"}:
                return jsonify({"error": "invalid_status"}), 400
            company.status = status
        session.add(company)
        session.flush()
        status_value = company.status

    billing = _get_billing_service()
    if plan_id_raw is not None:
        try:
            billing.assign_plan(company_id, int(plan_id_raw), ciclo=ciclo, status=status_value or "ativa")
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    summary = billing.summarize_company(company_id)
    return jsonify(summary)


@bp.get("/painel/empresas/<int:company_id>")
@require_panel_auth
def get_company(company_id: int):
    billing = _get_billing_service()
    summary = billing.summarize_company(company_id)
    if summary.get("status") == "desconhecida":
        return jsonify({"error": "not_found"}), 404
    return jsonify(summary)


@bp.get("/projects/")
@require_panel_auth
def list_projects():
    try:
        company_id = _require_company_id()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    with get_db_session(current_app) as session:
        result = session.execute(
            select(Project)
            .where(Project.company_id == company_id)
            .order_by(Project.created_at.desc(), Project.id.desc())
        )
        projects = result.scalars().all()
    return jsonify([_project_to_dict(project) for project in projects])


@bp.post("/projects/")
@require_panel_auth
def create_project():
    payload = request.get_json(silent=True) or {}
    try:
        company_id = _require_company_id()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    project = Project(
        company_id=company_id,
        name=payload.get("name"),
        client=payload.get("client"),
        description=payload.get("description"),
        status=payload.get("status") or "ativo",
        github_url=payload.get("github_url"),
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
    try:
        company_id = _require_company_id()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    with get_db_session(current_app) as session:
        project = session.get(Project, project_id)
        if project is None:
            return jsonify({"error": "not found"}), 404
        if project.company_id != company_id:
            return jsonify({"error": "forbidden"}), 403

        for field in ("name", "client", "description", "status", "github_url"):
            if field in payload:
                setattr(project, field, payload[field])
        session.add(project)
        session.flush()
        project_id = project.id

    return jsonify({"ok": True, "id": project_id})


@bp.post("/projects/sync")
@require_panel_auth
def sync_github_projects():
    try:
        company_id = _require_company_id()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    with get_db_session(current_app) as session:
        result = project_sync_service.sync_github_projects_to_db(session, company_id)

    status_code = 200 if result.get("status") == "success" else 500
    return jsonify(result), status_code


@bp.delete("/projects/<int:project_id>")
@require_panel_auth
def delete_project(project_id: int):
    try:
        company_id = _require_company_id()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    with get_db_session(current_app) as session:
        project = session.get(Project, project_id)
        if project is None:
            return jsonify({"error": "not found"}), 404
        if project.company_id != company_id:
            return jsonify({"error": "forbidden"}), 403
        session.delete(project)

    return jsonify({"ok": True})


@bp.get("/projects/stats")
@require_panel_auth
def project_stats():
    try:
        company_id = _require_company_id()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    with get_db_session(current_app) as session:
        rows = session.execute(
            select(Project.status, func.count())
            .where(Project.company_id == company_id)
            .group_by(Project.status)
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
