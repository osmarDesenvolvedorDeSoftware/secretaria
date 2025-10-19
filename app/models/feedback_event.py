from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from .base import Base


class FeedbackEvent(Base):
    __tablename__ = "feedback_events"

    id = Column(Integer, primary_key=True)
    company_id = Column(
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    number = Column(String(32), nullable=False)
    channel = Column(String(32), nullable=False, default="whatsapp")
    feedback_type = Column(String(32), nullable=False)
    score = Column(Integer, nullable=True)
    comment = Column(Text, nullable=True)
    details = Column("metadata", JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=True)

    company = relationship("Company", back_populates="feedback_events")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "company_id": self.company_id,
            "number": self.number,
            "channel": self.channel,
            "feedback_type": self.feedback_type,
            "score": self.score,
            "comment": self.comment,
            "metadata": self.details or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    @staticmethod
    def calculate_expiration(retention_days: int) -> datetime | None:
        if retention_days <= 0:
            return None
        return datetime.utcnow() + timedelta(days=retention_days)


__all__ = ["FeedbackEvent"]
