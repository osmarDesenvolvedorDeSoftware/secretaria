from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.models.base import Base


class Profile(Base):
    __tablename__ = "profile"

    id = Column(Integer, primary_key=True)
    full_name = Column(String(120), nullable=False)
    role = Column(String(100), default="Desenvolvedor Freelancer")
    specialization = Column(String(200))
    bio = Column(Text)
    education = Column(Text)
    current_studies = Column(Text)
    certifications = Column(Text)
    experience_years = Column(Integer, default=0)
    availability = Column(String(100), default="Disponível para novos projetos")
    languages = Column(String(200), default="Português, Inglês (em aprendizado)")
    website = Column(String(200))
    github_url = Column(String(200))
    linkedin_url = Column(String(200))
    email = Column(String(200))
    avatar_url = Column(String(300))
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


__all__ = ["Profile"]
