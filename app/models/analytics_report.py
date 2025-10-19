from __future__ import annotations

from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import Column, DateTime, Enum as SAEnum, ForeignKey, Integer, Numeric, UniqueConstraint
from sqlalchemy.orm import relationship

from .base import Base


class AnalyticsGranularity(str, PyEnum):
    DAILY = "daily"
    WEEKLY = "weekly"


class AnalyticsReport(Base):
    __tablename__ = "analytics_reports"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "granularity",
            "period_start",
            name="uq_analytics_company_period",
        ),
    )

    id = Column(Integer, primary_key=True)
    company_id = Column(
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    granularity = Column(SAEnum(AnalyticsGranularity), nullable=False)
    period_start = Column(DateTime, nullable=False, index=True)
    period_end = Column(DateTime, nullable=False)
    messages_inbound = Column(Integer, nullable=False, default=0)
    messages_outbound = Column(Integer, nullable=False, default=0)
    tokens_inbound = Column(Integer, nullable=False, default=0)
    tokens_outbound = Column(Integer, nullable=False, default=0)
    responses_count = Column(Integer, nullable=False, default=0)
    response_time_total = Column(Numeric(18, 6), nullable=False, default=0)
    estimated_cost = Column(Numeric(18, 6), nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    company = relationship("Company", back_populates="analytics_reports")

    @property
    def average_response_time(self) -> float:
        total = float(self.response_time_total or 0)
        count = int(self.responses_count or 0)
        if count <= 0:
            return 0.0
        return round(total / count, 4)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "company_id": self.company_id,
            "granularity": self.granularity.value if isinstance(self.granularity, AnalyticsGranularity) else str(self.granularity),
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "messages_inbound": int(self.messages_inbound or 0),
            "messages_outbound": int(self.messages_outbound or 0),
            "tokens_inbound": int(self.tokens_inbound or 0),
            "tokens_outbound": int(self.tokens_outbound or 0),
            "responses_count": int(self.responses_count or 0),
            "average_response_time": self.average_response_time,
            "estimated_cost": float(self.estimated_cost or 0),
        }


__all__ = ["AnalyticsReport", "AnalyticsGranularity"]
