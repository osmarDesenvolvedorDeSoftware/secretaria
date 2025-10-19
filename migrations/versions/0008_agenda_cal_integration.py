"""Agenda inteligente e integração Cal.com

Revision ID: 0008_agenda_cal_integration
Revises: 0007_business_ai
Create Date: 2024-04-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0008_agenda_cal_integration"
down_revision = "0007_business_ai"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column("cal_api_key", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "companies",
        sa.Column("cal_webhook_secret", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "companies",
        sa.Column("cal_default_user_id", sa.String(length=64), nullable=True),
    )

    op.create_table(
        "appointments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "company_id",
            sa.Integer(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("client_name", sa.String(length=150), nullable=False),
        sa.Column("client_phone", sa.String(length=32), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("cal_booking_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="confirmed"),
        sa.Column("meeting_url", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_appointments_company_id", "appointments", ["company_id"])
    op.create_index("ix_appointments_status", "appointments", ["status"])


def downgrade() -> None:
    op.drop_index("ix_appointments_status", table_name="appointments")
    op.drop_index("ix_appointments_company_id", table_name="appointments")
    op.drop_table("appointments")

    op.drop_column("companies", "cal_default_user_id")
    op.drop_column("companies", "cal_webhook_secret")
    op.drop_column("companies", "cal_api_key")
