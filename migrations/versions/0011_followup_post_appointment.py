from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0011_followup_post_appt"
down_revision = "0010_scheduling_ai_opt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "appointments",
        sa.Column(
            "allow_followup",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "appointments",
        sa.Column("followup_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "appointments",
        sa.Column("followup_response", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "appointments",
        sa.Column(
            "followup_next_scheduled",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE appointments SET allow_followup = :value WHERE allow_followup IS NULL"
        ),
        {"value": True},
    )


def downgrade() -> None:
    op.drop_column("appointments", "followup_next_scheduled")
    op.drop_column("appointments", "followup_response")
    op.drop_column("appointments", "followup_sent_at")
    op.drop_column("appointments", "allow_followup")
