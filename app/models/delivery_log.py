from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from .base import Base


class DeliveryLog(Base):
    __tablename__ = "delivery_logs"

    id = Column(Integer, primary_key=True)
    company_id = Column(
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    number = Column(String(32), nullable=False)
    body = Column(Text, nullable=False)
    status = Column(String(32), nullable=False)
    external_id = Column(String(128))
    error = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    company = relationship("Company", back_populates="delivery_logs")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "number": self.number,
            "body": self.body,
            "status": self.status,
            "external_id": self.external_id,
            "error": self.error,
            "created_at": self.created_at,
        }
