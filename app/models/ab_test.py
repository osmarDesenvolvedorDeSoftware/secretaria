from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column, DateTime, Enum, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from .base import Base


ABTestStatus = ("draft", "running", "stopped", "completed")


class ABTest(Base):
    __tablename__ = "ab_tests"
    __table_args__ = (
        UniqueConstraint("company_id", "template_base", name="uq_abtest_company_template"),
    )

    id = Column(Integer, primary_key=True)
    company_id = Column(
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    template_base = Column(String(120), nullable=False)
    name = Column(String(120), nullable=True)
    variant_a = Column(JSON, nullable=False, default=dict)
    variant_b = Column(JSON, nullable=False, default=dict)
    target_metrics = Column(JSON, nullable=False, default=list)
    epsilon = Column(Float, nullable=False, default=0.1)
    status = Column(Enum(*ABTestStatus, name="abtest_status"), nullable=False, default="draft")
    period_start = Column(DateTime, nullable=True)
    period_end = Column(DateTime, nullable=True)
    winning_variant = Column(String(1), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    company = relationship("Company", back_populates="ab_tests")
    events = relationship(
        "ABEvent",
        back_populates="ab_test",
        cascade="all, delete-orphan",
    )

    def to_dict(self, include_events: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "company_id": self.company_id,
            "template_base": self.template_base,
            "name": self.name,
            "variant_a": self.variant_a or {},
            "variant_b": self.variant_b or {},
            "target_metrics": self.target_metrics or [],
            "epsilon": float(self.epsilon or 0),
            "status": self.status,
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "winning_variant": self.winning_variant,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_events:
            payload["events"] = [event.to_dict() for event in self.events]
        return payload

    @property
    def is_running(self) -> bool:
        return self.status == "running"

    @property
    def is_completed(self) -> bool:
        return self.status == "completed"


__all__ = ["ABTest", "ABTestStatus"]
