from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from flask import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Company


class CompanyNotFoundError(Exception):
    """Erro lançado quando não é possível resolver uma empresa para o contexto."""


def _normalize_domain(domain: str | None) -> str | None:
    if not domain:
        return None
    sanitized = domain.strip().lower()
    if sanitized.startswith("http://"):
        sanitized = sanitized[7:]
    elif sanitized.startswith("https://"):
        sanitized = sanitized[8:]
    if "/" in sanitized:
        sanitized = sanitized.split("/", 1)[0]
    return sanitized or None


def extract_domain_from_request(request: Request) -> str | None:
    """Extrai o domínio associado à requisição para resolver a empresa."""

    header_domain = request.headers.get("X-Company-Domain")
    if header_domain:
        normalized = _normalize_domain(header_domain)
        if normalized:
            return normalized

    host = request.host or request.headers.get("Host")
    return _normalize_domain(host)


def resolve_company(session: Session, domain: str | None) -> Optional[Company]:
    if not domain:
        return None
    statement = select(Company).where(func.lower(Company.domain) == func.lower(domain))
    return session.execute(statement).scalars().first()


def require_company(session: Session, domain: str | None) -> Company:
    company = resolve_company(session, domain)
    if company is None:
        raise CompanyNotFoundError(f"Empresa não encontrada para o domínio {domain!r}")
    return company


def queue_name_for_company(prefix: str, company_id: int) -> str:
    return f"{prefix}:company_{company_id}"


def redis_namespace(company_id: int) -> str:
    return f"company:{company_id}"


def namespaced_key(company_id: int, *parts: str) -> str:
    segments: list[str] = [redis_namespace(company_id)]
    segments.extend(part.strip(":") for part in parts if part)
    return ":".join(segments)


@dataclass(frozen=True)
class TenantContext:
    company_id: int
    label: str

    def namespaced_key(self, *parts: str) -> str:
        return namespaced_key(self.company_id, *parts)


def build_tenant_context(company: Company) -> TenantContext:
    return TenantContext(company_id=company.id, label=str(company.id))


def iter_companies(session_factory: Callable[[], Session]) -> Iterable[Company]:
    session = session_factory()
    try:
        return session.execute(select(Company).order_by(Company.id)).scalars().all()
    finally:
        session.close()
