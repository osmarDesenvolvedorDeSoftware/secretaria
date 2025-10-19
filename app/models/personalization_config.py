from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String

from .base import Base


class PersonalizationConfig(Base):
    __tablename__ = "personalization_configs"

    id = Column(Integer, primary_key=True)
    tone_of_voice = Column(String(64), nullable=False, default="amigavel")
    message_limit = Column(Integer, nullable=False, default=5)
    opening_phrases = Column(JSON, nullable=False, default=list)
    ai_enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tone_of_voice": self.tone_of_voice,
            "message_limit": self.message_limit,
            "opening_phrases": self.opening_phrases or [],
            "ai_enabled": bool(self.ai_enabled),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
