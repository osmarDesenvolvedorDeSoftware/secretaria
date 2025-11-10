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


def _build_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": API_VERSION,
    }
    token = settings.github_pat.strip()
    if token:
        headers["Authorization"] = f"token {token}"
    else:
        logger.warning("GITHUB_PAT is not configured. GitHub API calls will fail.")
    return headers


def fetch_github_projects() -> Optional[List[Dict[str, Any]]]:
    """Return repositories for the configured GitHub account."""

    token = settings.github_pat.strip()
    if not token:
        logger.error("Token do GitHub (GITHUB_PAT) não configurado.")
        return None

    url = f"{BASE_URL}/user/repos?sort=updated&type=owner"
    headers = _build_headers()
    try:
        with httpx.Client(headers=headers, timeout=10.0) as client:
            response = client.get(url)
            response.raise_for_status()
            repos = response.json()
    except httpx.HTTPStatusError as exc:
        logger.error("Erro HTTP ao buscar projetos do GitHub: %s", exc)
        return None
    except httpx.RequestError as exc:
        logger.error("Erro de rede ao buscar projetos do GitHub: %s", exc)
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
    if not token:
        logger.error("Token do GitHub (GITHUB_PAT) não configurado.")
        return None

    url = f"{BASE_URL}/repos/{owner}/{repo_name}/readme"
    headers = _build_headers()

    try:
        with httpx.Client(headers=headers, timeout=5.0) as client:
            response = client.get(url)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            logger.warning("README não encontrado para o repo: %s", repo_name)
        else:
            logger.error("Erro HTTP ao buscar README do repo %s: %s", repo_name, exc)
        return None
    except httpx.RequestError as exc:
        logger.error("Erro de rede ao buscar README do repo %s: %s", repo_name, exc)
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
