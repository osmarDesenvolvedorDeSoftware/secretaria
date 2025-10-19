from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.models import AuditLog

LOGGER = structlog.get_logger().bind(service="audit")


class AuditService:
    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory

    def _session(self) -> Session:
        return self.session_factory()  # type: ignore[call-arg]

    def record(
        self,
        *,
        company_id: int,
        actor: str,
        action: str,
        resource: str,
        payload: dict[str, Any] | None = None,
        actor_type: str = "system",
        ip_address: str | None = None,
    ) -> AuditLog:
        session = self._session()
        try:
            log = AuditLog(
                company_id=company_id,
                actor=actor,
                actor_type=actor_type,
                action=action,
                resource=resource,
                payload=payload or {},
                ip_address=ip_address,
                created_at=datetime.utcnow(),
            )
            session.add(log)
            session.commit()
            return log
        except Exception:
            session.rollback()
            LOGGER.warning(
                "audit_log_failed",
                company_id=company_id,
                action=action,
                resource=resource,
            )
            raise
        finally:
            session.close()


__all__ = ["AuditService"]
