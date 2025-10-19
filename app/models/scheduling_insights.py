from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import relationship

from .base import Base


class SchedulingInsights(Base):
    __tablename__ = "scheduling_insights"
    __table_args__ = (
        UniqueConstraint("company_id", "weekday", "hour", name="uix_scheduling_insights_company_slot"),
    )

    id = Column(Integer, primary_key=True)
    company_id = Column(
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    weekday = Column(Integer, nullable=False)
    hour = Column(Integer, nullable=False)
    attendance_rate = Column(Float, nullable=False, default=0.0)
    no_show_prob = Column(Float, nullable=False, default=0.0)
    suggested_slot = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    company = relationship("Company", back_populates="scheduling_insights")


__all__ = ["SchedulingInsights"]
