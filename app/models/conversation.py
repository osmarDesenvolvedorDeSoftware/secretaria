from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column, DateTime, Integer, String, Text

from .base import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True)
    number = Column(String(32), index=True, nullable=False)
    user_name = Column(String(255))
    last_message = Column(Text)
    context_json = Column(JSON, nullable=False, default=list)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "number": self.number,
            "user_name": self.user_name,
            "last_message": self.last_message,
            "context_json": self.context_json,
            "updated_at": self.updated_at,
            "created_at": self.created_at,
        }
