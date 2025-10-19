from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, Date, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.models.base import Base


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True)
    company_id = Column(
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plan_id = Column(
        ForeignKey("plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ciclo = Column(String(32), nullable=False, default="mensal")
    status = Column(
        Enum("ativa", "pendente", "cancelada", "suspensa", name="subscription_status"),
        nullable=False,
        default="pendente",
    )
    vencimento = Column(Date, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    ended_at = Column(DateTime)

    company = relationship("Company", back_populates="subscriptions")
    plan = relationship("Plan", back_populates="subscriptions")

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "company_id": self.company_id,
            "plan_id": self.plan_id,
            "ciclo": self.ciclo,
            "status": self.status,
            "vencimento": self.vencimento.isoformat() if self.vencimento else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
        }


__all__ = ["Subscription"]
