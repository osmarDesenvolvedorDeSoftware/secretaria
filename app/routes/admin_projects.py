from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    current_app,
    redirect,
    render_template,
    request,
    url_for,
)
from sqlalchemy import asc

from app.config import settings
from app.models.project import Project

STATUS_CHOICES = ("ativo", "inativo")

bp = Blueprint("admin_projects", __name__, url_prefix="/admin/projects")


def _require_panel_key() -> str:
    key = request.args.get("key")
    if key != settings.panel_password:
        abort(403)
    return key or ""


@bp.route("/", methods=["GET", "POST"])
def list_projects():
    key = _require_panel_key()
    session = current_app.db_session()
    try:
        if request.method == "POST":
            projects = session.query(Project).order_by(asc(Project.id)).all()
            updated = 0
            for project in projects:
                desired_locked = request.form.get(f"locked_{project.id}") == "on"
                desired_status = request.form.get(f"status_{project.id}", project.status)
                if desired_status not in STATUS_CHOICES:
                    desired_status = project.status
                if project.locked != desired_locked:
                    project.locked = desired_locked
                    session.add(project)
                    updated += 1
                if project.status != desired_status:
                    project.status = desired_status
                    session.add(project)
                    updated += 1
            if updated:
                session.commit()
            return redirect(url_for("admin_projects.list_projects", key=key))

        projects = session.query(Project).order_by(asc(Project.name)).all()
        total_projects = len(projects)
        locked_projects = sum(1 for project in projects if project.locked)
        status_totals: dict[str, int] = {}
        for project in projects:
            status = project.status or "indefinido"
            status_totals[status] = status_totals.get(status, 0) + 1
        return render_template(
            "admin/projects_list.html",
            projects=projects,
            key=key,
            status_choices=STATUS_CHOICES,
            total_projects=total_projects,
            locked_projects=locked_projects,
            status_totals=status_totals,
        )
    finally:
        session.close()


@bp.route("/edit/<int:project_id>", methods=["GET", "POST"])
def edit_project(project_id: int):
    key = _require_panel_key()
    session = current_app.db_session()
    try:
        project = session.get(Project, project_id)
        if project is None:
            abort(404)

        if request.method == "POST":
            project.description = request.form.get("description", project.description)
            status_value = request.form.get("status", project.status)
            if status_value not in STATUS_CHOICES:
                status_value = project.status
            project.status = status_value
            project.locked = bool(request.form.get("locked"))
            session.add(project)
            session.commit()
            return redirect(url_for("admin_projects.list_projects", key=key))

        return render_template(
            "admin/project_edit.html",
            project=project,
            key=key,
            status_choices=STATUS_CHOICES,
        )
    finally:
        session.close()


@bp.route("/toggle/<int:project_id>")
def toggle_locked(project_id: int):
    key = _require_panel_key()
    session = current_app.db_session()
    try:
        project = session.get(Project, project_id)
        if project is None:
            abort(404)

        project.locked = not bool(project.locked)
        session.add(project)
        session.commit()
    finally:
        session.close()

    return redirect(url_for("admin_projects.list_projects", key=key))
