"""scheduling ai optimization"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from alembic import op


revision = "0010_scheduling_ai_optimization"
down_revision = "0009_appointments_reminders_reschedule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scheduling_insights",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("hour", sa.Integer(), nullable=False),
        sa.Column("attendance_rate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("no_show_prob", sa.Float(), nullable=False, server_default="0"),
        sa.Column("suggested_slot", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("company_id", "weekday", "hour", name="uix_scheduling_insights_company_slot"),
    )
    op.create_index(
        "ix_scheduling_insights_company_id",
        "scheduling_insights",
        ["company_id"],
    )
    op.execute(
        sa.text(
            "UPDATE scheduling_insights SET updated_at = :now",
        ),
        {"now": datetime.utcnow()},
    )


def downgrade() -> None:
    op.drop_index("ix_scheduling_insights_company_id", table_name="scheduling_insights")
    op.drop_table("scheduling_insights")
