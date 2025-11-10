from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, Boolean, func
from sqlalchemy.orm import relationship

from app.models.base import Base


class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True)
    company_id = Column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(150), nullable=False)
    client = Column(String(100))
    description = Column(Text)
    status = Column(String(50), default="ativo")
    github_url = Column(String(255))
    locked = Column(Boolean, default=False, nullable=False, comment="locked=True → impede sincronização automática pelo GitHub")
    created_at = Column(DateTime, default=func.now())

    company = relationship("Company", back_populates="projects")


__all__ = ["Project"]
