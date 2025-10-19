from __future__ import annotations

from sqlalchemy import JSON, Column, Integer, Numeric, String, Text
from sqlalchemy.orm import relationship

from app.models.base import Base


class Plan(Base):
    __tablename__ = "plans"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(Text)
    limite_mensagens = Column(Integer, nullable=False, default=1000)
    limite_tokens = Column(Integer, nullable=False, default=500000)
    preco = Column(Numeric(10, 2), nullable=False, default=0)
    features = Column(JSON, nullable=False, default=list)

    companies = relationship("Company", back_populates="plan")
    subscriptions = relationship(
        "Subscription",
        back_populates="plan",
        cascade="all, delete-orphan",
    )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "limite_mensagens": self.limite_mensagens,
            "limite_tokens": self.limite_tokens,
            "preco": float(self.preco or 0),
            "features": self.features or [],
        }


__all__ = ["Plan"]
