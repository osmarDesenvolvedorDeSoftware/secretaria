from __future__ import annotations

from datetime import datetime

from flask import (
    Blueprint,
    abort,
    current_app,
    redirect,
    render_template,
    request,
    url_for,
)

from app.config import settings
from app.models.profile import Profile

bp = Blueprint("admin_profile", __name__, url_prefix="/admin/profile")


def _require_panel_key() -> str:
    key = request.args.get("key")
    if key != settings.panel_password:
        abort(403)
    return key or ""


@bp.route("/", methods=["GET", "POST"])
def manage_profile():
    key = _require_panel_key()
    session = current_app.db_session()
    try:
        profile = (
            session.query(Profile)
            .order_by(Profile.updated_at.desc(), Profile.id.desc())
            .first()
        )

        if request.method == "POST":
            if profile is None:
                profile = Profile()
            profile.full_name = request.form.get("full_name", profile.full_name)
            profile.role = request.form.get("role", profile.role)
            profile.specialization = request.form.get("specialization", profile.specialization)
            profile.bio = request.form.get("bio", profile.bio)
            profile.education = request.form.get("education", profile.education)
            profile.current_studies = request.form.get("current_studies", profile.current_studies)
            profile.certifications = request.form.get("certifications", profile.certifications)
            experience_text = request.form.get("experience_years", "")
            try:
                profile.experience_years = int(experience_text)
            except (TypeError, ValueError):
                profile.experience_years = profile.experience_years or 0
            profile.availability = request.form.get("availability", profile.availability)
            profile.languages = request.form.get("languages", profile.languages)
            profile.website = request.form.get("website", profile.website)
            profile.github_url = request.form.get("github_url", profile.github_url)
            profile.linkedin_url = request.form.get("linkedin_url", profile.linkedin_url)
            profile.email = request.form.get("email", profile.email)
            profile.avatar_url = request.form.get("avatar_url", profile.avatar_url)
            profile.updated_at = datetime.utcnow()

            session.add(profile)
            session.commit()
            return redirect(url_for("admin_profile.manage_profile", key=key, saved=1))

        display_profile = profile or Profile()

        return render_template(
            "admin/profile.html",
            profile=display_profile,
            key=key,
            saved=request.args.get("saved") == "1",
        )
    finally:
        session.close()
