from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, String, Text, func

from app.models.base import Base


class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True)
    name = Column(String(150), nullable=False)
    client = Column(String(100))
    description = Column(Text)
    status = Column(String(50), default="ativo")
    created_at = Column(DateTime, default=func.now())


__all__ = ["Project"]
