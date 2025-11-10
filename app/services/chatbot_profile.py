from __future__ import annotations

from typing import Sequence

from sqlalchemy.orm import Session

from app.models.profile import Profile
from app.models.project import Project

PROFILE_QUESTION_KEYWORDS = (
    "quem é o desenvolvedor",
    "quem e o desenvolvedor",
    "me fala do desenvolvedor",
    "me fale do desenvolvedor",
    "fale do desenvolvedor",
    "fale sobre o desenvolvedor",
    "quem fez",
    "quem criou",
    "quem programou",
    "quem desenvolveu",
    "quem é você",
    "quem é voce",
    "você é uma empresa",
    "voce e uma empresa",
    "o que você faz",
    "o que voce faz",
    "quais tecnologias",
    "qual sua formação",
    "qual sua formacao",
    "o que você está estudando",
    "o que voce esta estudando",
)

PROJECT_LIST_KEYWORDS = (
    "quais projetos",
    "quais projetos voce",
    "quais projetos você",
    "quais projetos voce tem",
    "quais projetos você tem",
    "quais projetos voce possui",
    "quais projetos você possui",
    "projetos",
    "meus projetos",
    "me fala dos projetos",
    "me fale dos projetos",
    "me fala do projeto",
    "me fale do projeto",
    "portfólio",
    "portfolio",
    "trabalhos",
)

PROFILE_KEY_TERMS = {
    "desenvolvedor",
    "desenvolvedora",
    "programador",
    "programadora",
    "dev",
}

PROFILE_INTENT_HINTS = {
    "quem",
    "qual",
    "fala",
    "fale",
    "sobre",
    "me",
    "conta",
    "conte",
    "informações",
    "informacoes",
    "info",
}

PROJECT_KEY_TERMS = {
    "projeto",
    "projetos",
    "portfolio",
    "portfólio",
    "trabalho",
    "trabalhos",
}

PROJECT_INTENT_HINTS = {
    "quais",
    "lista",
    "list",
    "mostra",
    "mostrar",
    "fale",
    "fala",
    "sobre",
    "me",
    "conte",
    "conta",
}

DEFAULT_FALLBACK_MESSAGE = (
    "Posso te ajudar com informações sobre os projetos desenvolvidos ou o perfil do "
    "programador. Você pode perguntar, por exemplo: 'quem é o desenvolvedor' ou 'me "
    "fale do projeto IPTV'."
)


def _normalize_message(message: str) -> str:
    return " ".join(message.lower().strip().split())


def _matches_profile_question(message: str) -> bool:
    normalized = _normalize_message(message)
    if any(keyword in normalized for keyword in PROFILE_QUESTION_KEYWORDS):
        return True

    tokens = set(normalized.split())
    if PROFILE_KEY_TERMS.intersection(tokens) and PROFILE_INTENT_HINTS.intersection(tokens):
        return True

    return False


def _matches_project_query(message: str) -> bool:
    normalized = _normalize_message(message)
    if any(keyword in normalized for keyword in PROJECT_LIST_KEYWORDS):
        return True

    tokens = set(normalized.split())
    if PROJECT_KEY_TERMS.intersection(tokens) and PROJECT_INTENT_HINTS.intersection(tokens):
        return True

    return False


def _select_projects(
    session: Session,
    company_id: int | None,
    project_limit: int | None = None,
) -> list[Project]:
    query = session.query(Project)
    if company_id is not None:
        query = query.filter(Project.company_id == company_id)
    if hasattr(Project, "created_at"):
        query = query.order_by(Project.created_at.desc(), Project.id.desc())
    else:
        query = query.order_by(Project.id.desc())
    if project_limit is not None:
        query = query.limit(project_limit)
    return list(query.all())


def build_profile_response(
    message: str,
    session_factory,
    company_id: int,
    project_limit: int = 3,
) -> str | None:
    if not message.strip():
        return None
    normalized = message.lower()

    session = session_factory()
    try:
        profile = (
            session.query(Profile)
            .order_by(Profile.updated_at.desc(), Profile.id.desc())
            .first()
        )
        all_projects: list[Project] = _select_projects(
            session, company_id, project_limit=None
        )
        should_answer = _matches_profile_question(message) or _matches_project_query(message)
        if not should_answer:
            for project in all_projects:
                name = (project.name or "").lower()
                if name and name in normalized:
                    should_answer = True
                    break
        if not should_answer:
            return None

        return generate_dynamic_response(
            session,
            message,
            company_id=company_id,
            project_limit=project_limit,
            profile=profile,
            projects=all_projects,
        )
    finally:
        session.close()


def generate_dynamic_response(
    db_session: Session,
    text: str,
    *,
    company_id: int | None = None,
    project_limit: int | None = 5,
    profile: Profile | None = None,
    projects: Sequence[Project] | None = None,
) -> str:
    normalized = _normalize_message(text)

    if profile is None:
        profile = (
            db_session.query(Profile)
            .order_by(Profile.updated_at.desc(), Profile.id.desc())
            .first()
        )

    project_list: list[Project]
    if projects is None:
        project_list = _select_projects(
            db_session,
            company_id,
            project_limit=None,
        )
    else:
        project_list = list(projects)

    if any(keyword in normalized for keyword in PROFILE_QUESTION_KEYWORDS):
        if profile:
            return (
                f"O desenvolvedor é **{profile.full_name}**, {profile.role or 'desenvolvedor freelancer'} "
                f"especializado em {profile.specialization or 'soluções digitais sob medida'}.\n\n"
                f"Formação: {profile.education or 'formação em desenvolvimento de software e computação em nuvem'}\n"
                f"Atualmente estudando: {profile.current_studies or 'Android, Python, IoT e Inteligência Artificial'}\n"
                f"Disponibilidade: {profile.availability or 'Disponível para novos projetos'}\n"
                f"Portfólio: {profile.website or 'https://osmardev.online'}"
            ).strip()
        return (
            "Sou um desenvolvedor freelancer especializado em Android, Python e automações "
            "inteligentes. Posso te ajudar a tirar o projeto do papel e integrar sistemas."
        )

    for project in project_list:
        project_name = (project.name or "").lower()
        if project_name and project_name in normalized:
            created_at = None
            if hasattr(project, "created_at") and project.created_at is not None:
                created_at = project.created_at.strftime("%d/%m/%Y")
            description = (project.description or "Sem descrição disponível.").strip()
            status = (project.status or "Concluído").strip()
            repo = project.github_url or "privado ou ainda não publicado."
            author = profile.full_name if profile else "nosso desenvolvedor principal"
            parts = [
                f"O projeto **{project.name}** foi desenvolvido por {author}.",
                f"\nDescrição: {description}",
                f"\nStatus: {status}",
            ]
            if created_at:
                parts.append(f"\nData de criação: {created_at}")
            parts.append(f"\nRepositório: {repo}")
            return "".join(parts).strip()

    if any(keyword in normalized for keyword in PROJECT_LIST_KEYWORDS):
        if not project_list:
            return "Ainda não há projetos cadastrados no sistema."

        limit = None
        if normalized not in {"projetos", "meus projetos"}:
            limit = project_limit

        displayed_projects = project_list if limit is None else project_list[: max(limit or 0, 0)]
        lines = []
        for project in displayed_projects:
            snippet = (project.description or "Sem descrição disponível.").strip()
            if len(snippet) > 120:
                snippet = f"{snippet[:120].rstrip()}..."
            lines.append(f"- **{project.name}** — {snippet}")

        response = [
            f"Atualmente, {profile.full_name if profile else 'o desenvolvedor'} trabalhou nos seguintes projetos:",
            "\n".join(lines),
        ]

        if limit is not None and project_limit is not None and len(project_list) > project_limit:
            response.append(
                "\n\nDiga o nome de um deles para saber mais detalhes."
            )
        return "\n".join(part for part in response if part).strip()

    return DEFAULT_FALLBACK_MESSAGE
