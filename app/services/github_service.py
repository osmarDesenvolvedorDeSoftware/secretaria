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
            headers["Authorization"] = f"Bearer {token}"
    return headers


def _fetch_paginated_repos(
    client: httpx.Client,
    url_builder,
    log_prefix: str,
) -> List[Dict[str, Any]]:
    """Fetch paginated repositories until exhaustion."""

    collected: List[Dict[str, Any]] = []
    page = 1

    while True:
        logger.info("%s (página %s)...", log_prefix, page)
        response = client.get(url_builder(page))

        if response.status_code == 403:
            logger.warning(
                "Limite de requisições da API do GitHub atingido (status 403). Aguarde antes de tentar novamente."
            )
            raise httpx.HTTPStatusError("rate limited", request=response.request, response=response)

        response.raise_for_status()

        data = response.json()
        if not isinstance(data, list):
            logger.error("Resposta inesperada da API do GitHub ao listar repositórios: %s", data)
            break

        collected.extend(data)
        if len(data) < 100:
            break
        page += 1

    return collected


def fetch_github_projects() -> Optional[List[Dict[str, Any]]]:
    """Return repositories for the configured GitHub account."""

    token = settings.github_pat.strip()
    github_username = settings.github_username.strip()
    include_private = settings.github_include_private

    repos: Optional[List[Dict[str, Any]]] = None
    should_try_public = not include_private

    if include_private and not token:
        logger.warning(
            "GITHUB_INCLUDE_PRIVATE está habilitado, mas nenhum token foi configurado. Fallback para repositórios públicos."
        )
        should_try_public = True

    if include_private and token:
        headers = _build_headers(include_token=True)
        try:
            with httpx.Client(headers=headers, timeout=10.0) as client:
                repos = _fetch_paginated_repos(
                    client,
                    lambda page: (
                        f"{BASE_URL}/user/repos?sort=updated&type=owner&per_page=100&page={page}"
                    ),
                    "Buscando repositórios",
                )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response else None
            if status_code == 401:
                logger.error("Token inválido ou sem permissão para repositórios privados.")
                should_try_public = True
            elif status_code == 403:
                return None
            elif status_code == 404:
                logger.warning(
                    "Endpoint de repositórios privados não disponível. Tentando listar repositórios públicos."
                )
                should_try_public = True
            else:
                logger.error(
                    "Erro HTTP ao buscar repositórios privados do GitHub: status %s", status_code
                )
                should_try_public = True
        except httpx.RequestError as exc:
            logger.error("Erro de rede ao buscar repositórios privados do GitHub: %s", exc)
            should_try_public = True

    if (repos is None or should_try_public) and github_username:
        headers = _build_headers(include_token=False)
        try:
            with httpx.Client(headers=headers, timeout=10.0) as client:
                repos = _fetch_paginated_repos(
                    client,
                    lambda page: (
                        f"{BASE_URL}/users/{github_username}/repos?sort=updated&type=owner&per_page=100&page={page}"
                    ),
                    "Buscando repositórios públicos",
                )
            if include_private and token:
                logger.warning(
                    "Token configurado, mas utilizando apenas repositórios públicos do usuário %s.",
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
    elif repos is None and not github_username:
        logger.error(
            "Não é possível buscar repositórios públicos: GITHUB_USERNAME não configurado."
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
            "private": repo.get("private"),
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
