"""Service responsible for synchronising GitHub repositories with projects."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.services.llm import generate_text
from app.models.project import Project
from app.services import github_service

logger = logging.getLogger(__name__)

_log_directory = Path("logs")
_log_directory.mkdir(parents=True, exist_ok=True)

auto_sync_logger = logging.getLogger("github_auto_sync")
if not auto_sync_logger.handlers:
    file_handler = logging.FileHandler(_log_directory / "github_auto_sync.log", encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    file_handler.setFormatter(formatter)
    auto_sync_logger.addHandler(file_handler)
    auto_sync_logger.setLevel(logging.INFO)
    auto_sync_logger.propagate = False


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
    except Exception as exc:  # pragma: no cover
        logger.error("Erro ao analisar contexto com IA (Gemini): %s", exc)
        return "Erro ao processar o contexto do projeto."


def sync_github_projects_to_db(db: Session, company_id: int) -> Dict[str, object]:
    """Sincroniza repositórios do GitHub com a tabela `projects`."""
    start_time = time.monotonic()
    logger.info("Iniciando sincronização de projetos do GitHub para company_id: %s", company_id)
    auto_sync_logger.info("Iniciando sincronização do GitHub para company_id=%s", company_id)

    repos = github_service.fetch_github_projects()
    if not repos:
        duration = time.monotonic() - start_time
        auto_sync_logger.error("Nenhum repositório retornado pela API. Tempo: %.2fs", duration)
        return {"status": "error", "message": "Nenhum projeto encontrado ou erro na API."}

    new_projects_count = 0
    skipped_count = 0

    for repo in repos:
        repo_name = repo.get("name") or ""
        repo_url = repo.get("html_url")  # ✅ usa a URL pública
        owner = repo.get("owner", {}).get("login", "")

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

        try:
            readme = github_service.fetch_repo_readme(owner=owner, repo_name=repo_name)
            ai_description: Optional[str] = repo.get("description") or "Projeto sem descrição."
            if readme:
                ai_description = _analyze_context_with_ai(readme)
        except Exception as e:
            logger.warning("Falha ao processar README de %s: %s", repo_name, e)
            ai_description = "Projeto de software."

        new_project = Project(
            company_id=company_id,
            name=repo_name,
            client="GitHub Import",
            description=ai_description,
            status="ativo",
            github_url=repo_url,
        )

        try:
            db.add(new_project)
            db.commit()  # ✅ commit individual garante persistência
            db.refresh(new_project)
            new_projects_count += 1
            logger.info("Projeto '%s' salvo no banco de dados.", repo_name)
        except Exception as e:
            db.rollback()
            logger.error("Erro ao salvar projeto %s: %s", repo_name, e)

    duration = time.monotonic() - start_time
    summary = (
        "Sincronização concluída. "
        f"{new_projects_count} novos projetos adicionados. {skipped_count} já existiam."
    )
    logger.info(summary)
    auto_sync_logger.info(
        "Finalizado para company_id=%s em %.2fs (novos=%s, ignorados=%s)",
        company_id,
        duration,
        new_projects_count,
        skipped_count,
    )
    return {"status": "success", "summary": summary, "new_projects": new_projects_count}
