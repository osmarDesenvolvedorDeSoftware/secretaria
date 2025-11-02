"""Analytics reports table"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0006_analytics_reports"
down_revision = "0005_multi_tenant"
branch_labels = None
depends_on = None


analytics_granularity_enum = postgresql.ENUM(
    "daily", "weekly", name="analyticsgranularity", create_type=False
)


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            CREATE TYPE analyticsgranularity AS ENUM ('daily', 'weekly');
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END;
        $$;
        """
    )

    op.create_table(
        "analytics_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "company_id",
            sa.Integer(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("granularity", analytics_granularity_enum, nullable=False),
        sa.Column("period_start", sa.DateTime(), nullable=False),
        sa.Column("period_end", sa.DateTime(), nullable=False),
        sa.Column("messages_inbound", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("messages_outbound", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_inbound", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_outbound", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("responses_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("response_time_total", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("estimated_cost", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "company_id",
            "granularity",
            "period_start",
            name="uq_analytics_company_period",
        ),
    )
    op.create_index("ix_analytics_reports_company_id", "analytics_reports", ["company_id"])
    op.create_index("ix_analytics_reports_period_start", "analytics_reports", ["period_start"])


def downgrade() -> None:
    op.drop_index("ix_analytics_reports_period_start", table_name="analytics_reports")
    op.drop_index("ix_analytics_reports_company_id", table_name="analytics_reports")
    op.drop_table("analytics_reports")
    op.execute("DROP TYPE IF EXISTS analyticsgranularity")
