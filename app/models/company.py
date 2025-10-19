from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.models.base import Base


class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True)
    name = Column(String(150), nullable=False)
    domain = Column(String(255), nullable=False, unique=True, index=True)
    status = Column(
        Enum("ativo", "suspenso", "cancelado", name="company_status"),
        nullable=False,
        default="ativo",
    )
    current_plan_id = Column(ForeignKey("plans.id", ondelete="SET NULL"), nullable=True)
    cal_api_key = Column(String(255), nullable=True)
    cal_webhook_secret = Column(String(255), nullable=True)
    cal_default_user_id = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    plan = relationship("Plan", back_populates="companies")
    subscriptions = relationship(
        "Subscription",
        back_populates="company",
        cascade="all, delete-orphan",
    )
    projects = relationship("Project", back_populates="company")
    customer_contexts = relationship("CustomerContext", back_populates="company")
    personalization_configs = relationship(
        "PersonalizationConfig",
        back_populates="company",
    )
    conversations = relationship("Conversation", back_populates="company")
    delivery_logs = relationship("DeliveryLog", back_populates="company")
    analytics_reports = relationship(
        "AnalyticsReport",
        back_populates="company",
        cascade="all, delete-orphan",
    )
    ab_tests = relationship(
        "ABTest",
        back_populates="company",
        cascade="all, delete-orphan",
    )
    feedback_events = relationship(
        "FeedbackEvent",
        back_populates="company",
        cascade="all, delete-orphan",
    )
    audit_logs = relationship(
        "AuditLog",
        back_populates="company",
        cascade="all, delete-orphan",
    )
    appointments = relationship(
        "Appointment",
        back_populates="company",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover - utilitário de debug
        return f"<Company id={self.id} name={self.name!r} domain={self.domain!r}>"


__all__ = ["Company"]
