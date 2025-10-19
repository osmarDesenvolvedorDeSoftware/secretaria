from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from .base import Base


class CustomerContext(Base):
    __tablename__ = "customer_contexts"
    __table_args__ = (
        UniqueConstraint("company_id", "number", name="uq_customer_context_company_number"),
    )

    id = Column(Integer, primary_key=True)
    company_id = Column(
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    number = Column(String(32), index=True, nullable=False)
    frequent_topics = Column(JSON, nullable=False, default=list)
    product_mentions = Column(JSON, nullable=False, default=list)
    preferences = Column(JSON, nullable=False, default=dict)
    embedding = Column(JSON, nullable=True)
    last_subject = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    company = relationship("Company", back_populates="customer_contexts")

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
