from __future__ import annotations

from flask import Blueprint, current_app, jsonify, render_template, request
from sqlalchemy import func, select

from app import get_db_session
from app.models.project import Project

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


@bp.get("/projects/")
def list_projects():
    with get_db_session(current_app) as session:
        result = session.execute(
            select(Project).order_by(Project.created_at.desc(), Project.id.desc())
        )
        projects = result.scalars().all()
    return jsonify([_project_to_dict(project) for project in projects])


@bp.post("/projects/")
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
def delete_project(project_id: int):
    with get_db_session(current_app) as session:
        project = session.get(Project, project_id)
        if project is None:
            return jsonify({"error": "not found"}), 404
        session.delete(project)

    return jsonify({"ok": True})


@bp.get("/projects/stats")
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
