"""Utilities for interacting with the GitHub REST API."""

from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.github.com"
API_VERSION = "2022-11-28"


def _build_headers(include_token: bool = True) -> dict[str, str]:
    headers: dict[str, str] = {
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": API_VERSION,
    }
    if include_token:
        token = settings.github_pat.strip()
        if token:
            headers["Authorization"] = f"token {token}"
    return headers


def fetch_github_projects() -> Optional[List[Dict[str, Any]]]:
    """Return repositories for the configured GitHub account."""

    token = settings.github_pat.strip()
    github_username = settings.github_username.strip()

    repos: Optional[List[Dict[str, Any]]] = None
    should_try_public = False

    if token:
        url = f"{BASE_URL}/user/repos?sort=updated&type=owner"
        headers = _build_headers(include_token=True)
        try:
            with httpx.Client(headers=headers, timeout=10.0) as client:
                response = client.get(url)
                response.raise_for_status()
                repos = response.json()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code in {401, 403}:
                logger.error(
                    "Token do GitHub inválido ou sem permissões para /user/repos (status %s).",
                    status_code,
                )
                should_try_public = True
            else:
                logger.error("Erro HTTP ao buscar projetos do GitHub com token: %s", exc)
                return None
        except httpx.RequestError as exc:
            logger.error("Erro de rede ao buscar projetos do GitHub com token: %s", exc)
            should_try_public = True
    else:
        should_try_public = True

    if (repos is None or should_try_public) and should_try_public:
        if not github_username:
            logger.error(
                "Não é possível buscar repositórios públicos: GITHUB_USERNAME não configurado."
            )
            return None

        url = f"{BASE_URL}/users/{github_username}/repos?sort=updated&type=owner"
        headers = _build_headers(include_token=False)
        try:
            with httpx.Client(headers=headers, timeout=10.0) as client:
                response = client.get(url)
                response.raise_for_status()
                repos = response.json()
            if token:
                logger.warning(
                    "Utilizando repositórios públicos do usuário %s devido a problemas com o token.",
                    github_username,
                )
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Erro HTTP ao buscar repositórios públicos do usuário %s: %s",
                github_username,
                exc,
            )
            return None
        except httpx.RequestError as exc:
            logger.error(
                "Erro de rede ao buscar repositórios públicos do usuário %s: %s",
                github_username,
                exc,
            )
            return None

    if repos is None:
        return None

    project_list: List[Dict[str, Any]] = []
    for repo in repos:
        project_data = {
            "name": repo.get("name"),
            "description": repo.get("description"),
            "url": repo.get("html_url"),
            "language": repo.get("language"),
            "owner_login": repo.get("owner", {}).get("login"),
        }
        project_list.append(project_data)
    return project_list


def fetch_repo_readme(owner: str, repo_name: str) -> Optional[str]:
    """Return decoded README.md contents for the given repository."""

    token = settings.github_pat.strip()
    url = f"{BASE_URL}/repos/{owner}/{repo_name}/readme"
    headers = _build_headers(include_token=bool(token))

    try:
        with httpx.Client(headers=headers, timeout=5.0) as client:
            response = client.get(url)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        if status_code == 404:
            logger.warning("README não encontrado para o repositório: %s", repo_name)
        else:
            logger.error(
                "Erro HTTP ao buscar README do repositório %s: %s", repo_name, exc
            )
        return None
    except httpx.RequestError as exc:
        logger.error("Erro de rede ao buscar README do repositório %s: %s", repo_name, exc)
        return None

    if data.get("encoding") != "base64":
        logger.warning("README do repo %s com encoding inesperado: %s", repo_name, data.get("encoding"))
        return None

    content_base64 = data.get("content")
    if not content_base64:
        return None

    try:
        content_bytes = base64.b64decode(content_base64)
        return content_bytes.decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        logger.error("Falha ao decodificar README do repo %s: %s", repo_name, exc)
        return None
