from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Column, DateTime, Integer, String, Text

from .base import Base


class DeliveryLog(Base):
    __tablename__ = "delivery_logs"

    id = Column(Integer, primary_key=True)
    number = Column(String(32), nullable=False)
    body = Column(Text, nullable=False)
    status = Column(String(32), nullable=False)
    external_id = Column(String(128))
    error = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

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
