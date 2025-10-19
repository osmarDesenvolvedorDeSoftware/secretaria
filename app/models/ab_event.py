from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import relationship

from .base import Base


class ABEvent(Base):
    __tablename__ = "ab_events"
    __table_args__ = (
        UniqueConstraint("ab_test_id", "variant", "bucket_date", name="uq_abevent_bucket"),
    )

    id = Column(Integer, primary_key=True)
    company_id = Column(
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ab_test_id = Column(
        ForeignKey("ab_tests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    variant = Column(String(1), nullable=False)
    bucket_date = Column(Date, nullable=False, default=datetime.utcnow)
    impressions = Column(Integer, nullable=False, default=0)
    responses = Column(Integer, nullable=False, default=0)
    conversions = Column(Integer, nullable=False, default=0)
    clicks = Column(Integer, nullable=False, default=0)
    response_time_total = Column(Numeric(18, 6), nullable=False, default=0)
    response_time_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=True)

    ab_test = relationship("ABTest", back_populates="events")

    def to_dict(self) -> dict[str, Any]:
        avg_response = 0.0
        if self.response_time_count:
            avg_response = float(self.response_time_total or 0) / float(self.response_time_count)
        return {
            "id": self.id,
            "ab_test_id": self.ab_test_id,
            "variant": self.variant,
            "bucket_date": self.bucket_date.isoformat() if self.bucket_date else None,
            "impressions": int(self.impressions or 0),
            "responses": int(self.responses or 0),
            "conversions": int(self.conversions or 0),
            "clicks": int(self.clicks or 0),
            "average_response_time": round(avg_response, 4),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


__all__ = ["ABEvent"]
