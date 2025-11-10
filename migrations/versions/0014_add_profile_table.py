"""add profile table"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0014_add_profile_table"
down_revision = "0013_add_locked_flag_to_projects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "profile",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("full_name", sa.String(length=120), nullable=False),
        sa.Column("role", sa.String(length=100), nullable=True),
        sa.Column("specialization", sa.String(length=200), nullable=True),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("education", sa.Text(), nullable=True),
        sa.Column("current_studies", sa.Text(), nullable=True),
        sa.Column("certifications", sa.Text(), nullable=True),
        sa.Column("experience_years", sa.Integer(), nullable=True),
        sa.Column("availability", sa.String(length=100), nullable=True),
        sa.Column("languages", sa.String(length=200), nullable=True),
        sa.Column("website", sa.String(length=200), nullable=True),
        sa.Column("github_url", sa.String(length=200), nullable=True),
        sa.Column("linkedin_url", sa.String(length=200), nullable=True),
        sa.Column("email", sa.String(length=200), nullable=True),
        sa.Column("avatar_url", sa.String(length=300), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    profile_table = sa.table(
        "profile",
        sa.column("full_name", sa.String()),
        sa.column("role", sa.String()),
        sa.column("specialization", sa.String()),
        sa.column("bio", sa.Text()),
        sa.column("education", sa.Text()),
        sa.column("current_studies", sa.Text()),
        sa.column("certifications", sa.Text()),
        sa.column("experience_years", sa.Integer()),
        sa.column("availability", sa.String()),
        sa.column("languages", sa.String()),
        sa.column("website", sa.String()),
        sa.column("github_url", sa.String()),
        sa.column("linkedin_url", sa.String()),
        sa.column("email", sa.String()),
        sa.column("avatar_url", sa.String()),
        sa.column("updated_at", sa.DateTime()),
    )

    op.bulk_insert(
        profile_table,
        [
            {
                "full_name": "Osmar Cavalcante",
                "role": "Desenvolvedor Freelancer",
                "specialization": "Android, Python, IoT e Automação com n8n",
                "bio": (
                    "Sou um desenvolvedor apaixonado por criar sistemas que realmente resolvem problemas reais. "
                    "Trabalho com Android, Python e IoT, e tenho foco em automação de processos, integração de APIs "
                    "e soluções inteligentes para empresas e profissionais."
                ),
                "education": (
                    "Pós-graduação em Cloud Computing (Unopar, 2024) e Pós em Big Data, IoT e Inteligência Artificial "
                    "(Faculdade Metropolitana, em andamento)"
                ),
                "current_studies": "Estudando Android avançado (Kotlin + Jetpack), Inteligência Artificial e Automação com n8n",
                "certifications": "Certificação em Desenvolvimento Android, Python Avançado e IA aplicada",
                "experience_years": 4,
                "availability": "Disponível para novos projetos",
                "languages": "Português nativo, Inglês intermediário (fluente em leitura técnica)",
                "website": "https://osmardev.online",
                "github_url": "https://github.com/osmarDesenvolvedorDeSoftware",
                "linkedin_url": "https://linkedin.com/in/osmardev",
                "email": "osmar@osmardev.online",
                "avatar_url": "",
                "updated_at": datetime.utcnow(),
            }
        ],
    )


def downgrade() -> None:
    op.drop_table("profile")
