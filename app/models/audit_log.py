from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from .base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True)
    company_id = Column(
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor = Column(String(120), nullable=False)
    actor_type = Column(String(32), nullable=False, default="system")
    action = Column(String(64), nullable=False)
    resource = Column(String(128), nullable=False)
    ip_address = Column(String(64), nullable=True)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    company = relationship("Company", back_populates="audit_logs")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "company_id": self.company_id,
            "actor": self.actor,
            "actor_type": self.actor_type,
            "action": self.action,
            "resource": self.resource,
            "ip_address": self.ip_address,
            "payload": self.payload or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


__all__ = ["AuditLog"]
