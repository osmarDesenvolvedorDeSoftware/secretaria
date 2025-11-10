from __future__ import annotations

from typing import Iterable

from sqlalchemy import desc

from app.models.profile import Profile
from app.models.project import Project

PROFILE_QUESTION_KEYWORDS = (
    "quem é você",
    "quem é voce",
    "você é uma empresa",
    "voce e uma empresa",
    "o que você faz",
    "o que voce faz",
    "quais projetos",
    "quais projetos você",
    "quais projetos voce",
    "quais tecnologias",
    "qual sua formação",
    "qual sua formacao",
    "o que você está estudando",
    "o que voce esta estudando",
)


def _matches_profile_question(message: str) -> bool:
    normalized = message.lower()
    return any(keyword in normalized for keyword in PROFILE_QUESTION_KEYWORDS)


def build_profile_response(
    message: str,
    session_factory,
    company_id: int,
    project_limit: int = 3,
) -> str | None:
    if not message.strip():
        return None
    if not _matches_profile_question(message):
        return None

    session = session_factory()
    try:
        profile = (
            session.query(Profile)
            .order_by(desc(Profile.updated_at), desc(Profile.id))
            .first()
        )
        projects: Iterable[Project] = []
        if hasattr(session, "query"):
            projects = (
                session.query(Project)
                .filter(Project.company_id == company_id)
                .order_by(desc(Project.id))
                .limit(project_limit)
                .all()
            )
    finally:
        session.close()

    if profile is None:
        return (
            "Sou um desenvolvedor freelancer especializado em Android, Python e automações "
            "inteligentes. Posso te ajudar a tirar o projeto do papel e integrar sistemas."
        )

    project_lines = []
    for project in projects:
        snippet = (project.description or "").strip()
        if snippet and len(snippet) > 160:
            snippet = f"{snippet[:160].rstrip()}..."
        elif not snippet:
            snippet = "Projeto em andamento, descrição em breve."
        project_lines.append(f"- {project.name}: {snippet}")

    if not project_lines:
        project_lines.append("- Em breve adicionarei novos projetos públicos ao portfólio.")

    parts = [
        f"Eu sou {profile.full_name}, {profile.role or 'desenvolvedor freelancer'} especializado em {profile.specialization or 'soluções digitais sob medida.'}",
    ]
    if profile.bio:
        parts.append(profile.bio.strip())

    if profile.education:
        parts.append(f"\nFormação:\n{profile.education.strip()}")

    if profile.current_studies:
        parts.append(f"\nAtualmente estudando:\n{profile.current_studies.strip()}")

    parts.append("\nAlguns dos meus projetos recentes:")
    parts.append("\n".join(project_lines))

    extras = []
    if profile.languages:
        extras.append(f"Idiomas: {profile.languages.strip()}.")
    if profile.certifications:
        extras.append(f"Certificações: {profile.certifications.strip()}.")
    if profile.availability:
        extras.append(profile.availability.strip())
    if extras:
        parts.append("\n" + " ".join(extras))

    if profile.website:
        parts.append(f"\nSaiba mais em {profile.website.strip()}.")

    return "\n".join(part for part in parts if part)
