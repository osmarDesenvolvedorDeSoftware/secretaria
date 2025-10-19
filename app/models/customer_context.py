from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column, DateTime, Integer, String, Text

from .base import Base


class CustomerContext(Base):
    __tablename__ = "customer_contexts"

    id = Column(Integer, primary_key=True)
    number = Column(String(32), unique=True, index=True, nullable=False)
    frequent_topics = Column(JSON, nullable=False, default=list)
    product_mentions = Column(JSON, nullable=False, default=list)
    preferences = Column(JSON, nullable=False, default=dict)
    embedding = Column(JSON, nullable=True)
    last_subject = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "number": self.number,
            "frequent_topics": self.frequent_topics or [],
            "product_mentions": self.product_mentions or [],
            "preferences": self.preferences or {},
            "embedding": self.embedding,
            "last_subject": self.last_subject,
            "updated_at": self.updated_at,
            "created_at": self.created_at,
        }
