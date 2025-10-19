"""Add reminder and reschedule fields to appointments

Revision ID: 0009_appointments_reminders_reschedule
Revises: 0008_agenda_cal_integration
Create Date: 2024-05-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_appointments_reminders_reschedule"
down_revision = "0008_agenda_cal_integration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "appointments",
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "appointments",
        sa.Column("reminder_24h_sent", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "appointments",
        sa.Column("reminder_1h_sent", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "appointments",
        sa.Column("no_show_checked", sa.DateTime(timezone=True), nullable=True),
    )
    op.alter_column("appointments", "status", server_default="pending")
    op.execute(
        """
        UPDATE appointments
        SET confirmed_at = created_at
        WHERE status = 'confirmed' AND confirmed_at IS NULL
        """
    )


def downgrade() -> None:
    op.alter_column("appointments", "status", server_default="confirmed")
    op.drop_column("appointments", "no_show_checked")
    op.drop_column("appointments", "reminder_1h_sent")
    op.drop_column("appointments", "reminder_24h_sent")
    op.drop_column("appointments", "confirmed_at")
