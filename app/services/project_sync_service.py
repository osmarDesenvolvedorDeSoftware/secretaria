"""Service responsible for synchronising GitHub repositories with projects."""

from __future__ import annotations

import logging
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.integrations.gemini import generate_text
from app.models.project import Project
from app.services import github_service

logger = logging.getLogger(__name__)


def _analyze_context_with_ai(readme_content: str) -> str:
    if not readme_content:
        return "Nenhum contexto fornecido para análise."

    truncated_readme = readme_content[:4000]
    prompt = (
        "Objetivo: Você é um analista de software sênior.\n"
        "Tarefa: Analise o conteúdo do arquivo README.md abaixo e gere uma descrição "
        "concisa em português (2-3 frases) sobre o projeto.\n\n"
        "Foco: O que o projeto faz, qual tecnologia principal ele usa e qual problema ele resolve.\n"
        "Se o README for muito curto ou inútil, apenas diga \"Projeto de software\".\n\n"
        "README:\n---\n"
        f"{truncated_readme}\n---\n\n"
        "Descrição Concisa:"
    )

    try:
        ai_description = generate_text(prompt)
        return ai_description.strip() or "Projeto de software"
    except Exception as exc:  # pragma: no cover - guard clause
        logger.error("Erro ao analisar contexto com IA (Gemini): %s", exc)
        return "Erro ao processar o contexto do projeto."


def sync_github_projects_to_db(db: Session, company_id: int) -> Dict[str, object]:
    logger.info("Iniciando sincronização de projetos do GitHub para company_id: %s", company_id)

    repos = github_service.fetch_github_projects()
    if not repos:
        logger.warning("Nenhum projeto encontrado ou erro na API do GitHub.")
        return {"status": "error", "message": "Nenhum projeto encontrado ou erro na API."}

    new_projects_count = 0
    skipped_count = 0

    for repo in repos:
        repo_name = repo.get("name") or ""
        repo_url = repo.get("url")
        owner = repo.get("owner_login") or ""

        if not repo_name or not owner:
            logger.debug("Ignorando repositório sem informações suficientes: %s", repo)
            continue

        existing_project = (
            db.query(Project)
            .filter(Project.name == repo_name, Project.company_id == company_id)
            .first()
        )
        if existing_project:
            skipped_count += 1
            continue

        logger.info("Processando novo projeto: %s", repo_name)

        readme = github_service.fetch_repo_readme(owner=owner, repo_name=repo_name)
        ai_description: Optional[str] = repo.get("description") or "Projeto sem descrição."

        if readme:
            logger.info("Analisando README de %s com IA...", repo_name)
            ai_description = _analyze_context_with_ai(readme)
        else:
            logger.info("README não encontrado para %s. Usando descrição padrão do GitHub.", repo_name)

        new_project = Project(
            company_id=company_id,
            name=repo_name,
            client="GitHub Import",
            description=ai_description,
            status="ativo",
            github_url=repo_url,
        )

        db.add(new_project)
        new_projects_count += 1
        logger.info("Projeto '%s' salvo no banco de dados.", repo_name)

    db.commit()

    summary = (
        "Sincronização concluída. "
        f"{new_projects_count} novos projetos adicionados. {skipped_count} projetos já existentes."
    )
    logger.info(summary)
    return {"status": "success", "summary": summary, "new_projects": new_projects_count}
