'''
Serviço para gerar respostas dinâmicas sobre o perfil do desenvolvedor e projetos,
integrando um fluxo de RAG (Retrieval-Augmented Generation) simplificado.
'''
from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from app.models.profile import Profile
from app.models.project import Project

# --- Constantes de Intenção ---

PROFILE_KEYWORDS = {
    "desenvolvedor", "programador", "dev", "criador", "autor", "quem é você", "quem e voce",
    "fale sobre você", "fale sobre voce", "seu perfil", "sua formação", "sua formacao",
    "quem fez", "quem criou", "quem programou", "quem desenvolveu",
}

PROJECT_KEYWORDS = {
    "projeto", "projetos", "portfolio", "portfólio", "trabalho", "trabalhos",
    "o que você faz", "o que voce faz", "exemplos", "cases",
}

# --- Funções de Detecção de Intenção ---

def _normalize_text(text: str) -> str:
    '''Normaliza o texto para análise: minúsculas, sem acentos e espaços extras.'''
    text = text.lower().strip()
    # Remove acentos de forma simples
    text = re.sub(r'[áàâãä]', 'a', text)
    text = re.sub(r'[éèêë]', 'e', text)
    text = re.sub(r'[íìîï]', 'i', text)
    text = re.sub(r'[óòôõö]', 'o', text)
    text = re.sub(r'[úùûü]', 'u', text)
    text = re.sub(r'ç', 'c', text)
    return re.sub(r'\s+', ' ', text)

def detect_intent(message: str) -> str | None:
    '''Detecta a intenção do usuário (perfil ou projetos) com base em palavras-chave.
    
    Retorna:
        "profile" se detectar intenção de perguntar sobre o desenvolvedor
        "projects" se detectar intenção de perguntar sobre projetos
        None se não detectar intenção específica
    '''
    normalized_msg = _normalize_text(message)
    tokens = set(normalized_msg.split())

    # Verifica se contém variações de "desenvolvedor" (tolera erros de digitação)
    if any(variant in normalized_msg for variant in ["desenvolvedor", "desevovledor", "desenvolvedor"]):
        return "profile"

    # Verifica palavras-chave de perfil
    if any(keyword in normalized_msg or keyword in tokens for keyword in PROFILE_KEYWORDS):
        return "profile"

    # Verifica palavras-chave de projetos
    if any(keyword in normalized_msg or keyword in tokens for keyword in PROJECT_KEYWORDS):
        return "projects"

    return None

# --- Funções de Recuperação de Dados (Retrieval) ---

def get_profile_context(session: Session) -> str:
    '''Busca o perfil do desenvolvedor no banco e formata como um texto de contexto.'''
    profile = session.query(Profile).order_by(Profile.updated_at.desc()).first()
    if not profile:
        return "Nenhuma informação de perfil de desenvolvedor encontrada no banco de dados."

    context_parts = []
    if profile.full_name:
        context_parts.append(f"Nome: {profile.full_name}")
    if profile.role:
        context_parts.append(f"Função: {profile.role}")
    if profile.specialization:
        context_parts.append(f"Especialização: {profile.specialization}")
    if profile.bio:
        context_parts.append(f"Biografia: {profile.bio}")
    if profile.education:
        context_parts.append(f"Formação: {profile.education}")
    if profile.current_studies:
        context_parts.append(f"Estudos Atuais: {profile.current_studies}")
    if profile.experience_years:
        context_parts.append(f"Anos de Experiência: {profile.experience_years}")
    if profile.availability:
        context_parts.append(f"Disponibilidade: {profile.availability}")
    if profile.languages:
        context_parts.append(f"Idiomas: {profile.languages}")
    if profile.email:
        context_parts.append(f"Email: {profile.email}")
    if profile.website:
        context_parts.append(f"Website/Portfólio: {profile.website}")
    if profile.github_url:
        context_parts.append(f"GitHub: {profile.github_url}")
    if profile.linkedin_url:
        context_parts.append(f"LinkedIn: {profile.linkedin_url}")

    return "\n".join(context_parts)

def get_projects_context(session: Session, company_id: int) -> str:
    '''Busca os projetos no banco e formata como um texto de contexto.'''
    projects = session.query(Project).filter_by(company_id=company_id).order_by(Project.created_at.desc()).limit(10).all()
    if not projects:
        return "Nenhum projeto encontrado no banco de dados para esta empresa."

    project_lines = []
    for p in projects:
        project_info = f"Projeto: {p.name}"
        if p.description:
            project_info += f"\nDescrição: {p.description}"
        if p.status:
            project_info += f"\nStatus: {p.status}"
        if p.client:
            project_info += f"\nCliente: {p.client}"
        if p.github_url:
            project_info += f"\nRepositório: {p.github_url}"
        project_lines.append(project_info)

    return "\n\n".join(project_lines)

# --- Função Principal de Geração de Contexto para o LLM ---

def build_rag_context(
    message: str,
    session_factory,
    company_id: int,
) -> dict[str, Any] | None:
    '''
    Constrói o contexto de RAG para ser injetado no prompt do LLM.
    
    Args:
        message: Mensagem do usuário
        session_factory: Factory para criar sessão do banco
        company_id: ID da empresa (para filtrar projetos)
    
    Returns:
        Dicionário com system_prompt enriquecido e status, ou None se não detectar intenção
    '''
    intent = detect_intent(message)
    if not intent:
        return None

    session = session_factory()
    try:
        if intent == "profile":
            context_data = get_profile_context(session)
            system_prompt = f'''Você é uma secretária virtual profissional e amigável.

**CONTEXTO DO BANCO DE DADOS (Perfil do Desenvolvedor):**
{context_data}

**SUA TAREFA:**
Responda à pergunta do usuário sobre o desenvolvedor usando EXCLUSIVAMENTE as informações do contexto acima.
Seja amigável, profissional e natural na resposta.
Se alguma informação não estiver disponível no contexto, diga que não tem essa informação no momento.
'''.strip()
            return {"system_prompt": system_prompt, "status": "profile_rag"}

        if intent == "projects":
            context_data = get_projects_context(session, company_id)
            system_prompt = f'''Você é uma secretária virtual profissional e amigável.

**CONTEXTO DO BANCO DE DADOS (Projetos Desenvolvidos):**
{context_data}

**SUA TAREFA:**
Responda à pergunta do usuário sobre os projetos usando EXCLUSIVAMENTE as informações do contexto acima.
Liste os projetos de forma clara e organizada.
Seja profissional e destaque os pontos fortes de cada projeto.
'''.strip()
            return {"system_prompt": system_prompt, "status": "projects_rag"}

    finally:
        session.close()

    return None


# Mantém as funções antigas para compatibilidade com testes existentes
def build_profile_response(
    message: str,
    session_factory,
    company_id: int,
    project_limit: int = 3,
) -> str | None:
    '''
    Função legada mantida para compatibilidade com testes.
    Agora retorna None para forçar o uso do fluxo RAG.
    '''
    # Delega para o novo fluxo RAG
    rag_context = build_rag_context(message, session_factory, company_id)
    if rag_context:
        # Retorna uma flag para indicar que deve usar RAG
        return "__USE_RAG__"
    return None


def generate_dynamic_response(
    db_session: Session,
    text: str,
    *,
    company_id: int | None = None,
    project_limit: int | None = 5,
    profile: Any = None,
    projects: Any = None,
) -> str:
    '''
    Função legada mantida para compatibilidade com testes.
    '''
    normalized = _normalize_text(text)

    profile_obj = profile
    if profile_obj is None:
        profile_obj = (
            db_session.query(Profile)
            .order_by(Profile.updated_at.desc())
            .first()
        )

    if projects is None:
        query = db_session.query(Project)
        if company_id is not None:
            query = query.filter(Project.company_id == company_id)
        if hasattr(Project, "created_at"):
            query = query.order_by(Project.created_at.desc())
        project_list = list(query.all())
    else:
        project_list = list(projects)

    intent = detect_intent(text)

    if intent == "profile":
        if profile_obj:
            return (
                f"O desenvolvedor é **{profile_obj.full_name}**, {profile_obj.role or 'desenvolvedor freelancer'} "
                f"especializado em {profile_obj.specialization or 'soluções digitais sob medida'}.\n\n"
                f"Formação: {profile_obj.education or 'formação em desenvolvimento de software e computação em nuvem'}\n"
                f"Atualmente estudando: {profile_obj.current_studies or 'Android, Python, IoT e Inteligência Artificial'}\n"
                f"Disponibilidade: {profile_obj.availability or 'Disponível para novos projetos'}\n"
                f"Portfólio: {profile_obj.website or 'https://osmardev.online'}"
            ).strip()
        return (
            "Sou um desenvolvedor freelancer especializado em Android, Python e automações "
            "inteligentes. Posso te ajudar a tirar o projeto do papel e integrar sistemas."
        )

    for project in project_list:
        project_name = _normalize_text(project.name or "")
        if project_name and project_name in normalized:
            created_at = None
            if hasattr(project, "created_at") and project.created_at is not None:
                created_at = project.created_at.strftime("%d/%m/%Y")
            description = (project.description or "Sem descrição disponível.").strip()
            status = (project.status or "Concluído").strip()
            repo = project.github_url or "privado ou ainda não publicado."
            author = profile_obj.full_name if profile_obj else "nosso desenvolvedor principal"
            parts = [
                f"O projeto **{project.name}** foi desenvolvido por {author}.",
                f"\nDescrição: {description}",
                f"\nStatus: {status}",
            ]
            if created_at:
                parts.append(f"\nData de criação: {created_at}")
            parts.append(f"\nRepositório: {repo}")
            return "".join(parts).strip()

    if intent == "projects":
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
            f"Atualmente, {profile_obj.full_name if profile_obj else 'o desenvolvedor'} trabalhou nos seguintes projetos:",
            "\n".join(lines),
        ]

        if limit is not None and project_limit is not None and len(project_list) > project_limit:
            response.append(
                "\n\nDiga o nome de um deles para saber mais detalhes."
            )
        return "\n".join(part for part in response if part).strip()

    return (
        "Posso te ajudar com informações sobre os projetos desenvolvidos ou o perfil do "
        "programador. Você pode perguntar, por exemplo: 'quem é o desenvolvedor' ou 'me "
        "fale do projeto IPTV'."
    )
