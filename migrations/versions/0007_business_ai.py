"""Business AI core tables"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0007_business_ai"
down_revision = "0006_analytics_reports"
branch_labels = None
depends_on = None


abtest_status_enum = postgresql.ENUM(
    "draft",
    "running",
    "stopped",
    "completed",
    name="abtest_status",
    create_type=False,
)


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            CREATE TYPE abtest_status AS ENUM ('draft', 'running', 'stopped', 'completed');
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END;
        $$;
        """
    )

    op.create_table(
        "ab_tests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("template_base", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=True),
        sa.Column("variant_a", sa.JSON(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("variant_b", sa.JSON(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("target_metrics", sa.JSON(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("epsilon", sa.Float(), nullable=False, server_default="0.1"),
        sa.Column("status", abtest_status_enum, nullable=False, server_default="draft"),
        sa.Column("period_start", sa.DateTime(), nullable=True),
        sa.Column("period_end", sa.DateTime(), nullable=True),
        sa.Column("winning_variant", sa.String(length=1), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("company_id", "template_base", name="uq_abtest_company_template"),
    )
    op.create_index("ix_ab_tests_company_id", "ab_tests", ["company_id"])
    op.create_index("ix_ab_tests_status", "ab_tests", ["status"])

    op.create_table(
        "ab_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ab_test_id", sa.Integer(), sa.ForeignKey("ab_tests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("variant", sa.String(length=1), nullable=False),
        sa.Column("bucket_date", sa.Date(), nullable=False, server_default=sa.text("CURRENT_DATE")),
        sa.Column("impressions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("responses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("conversions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("clicks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("response_time_total", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("response_time_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("ab_test_id", "variant", "bucket_date", name="uq_abevent_bucket"),
    )
    op.create_index("ix_ab_events_company_id", "ab_events", ["company_id"])
    op.create_index("ix_ab_events_ab_test_id", "ab_events", ["ab_test_id"])

    op.create_table(
        "feedback_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("number", sa.String(length=32), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False, server_default="whatsapp"),
        sa.Column("feedback_type", sa.String(length=32), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_feedback_events_company_id", "feedback_events", ["company_id"])
    op.create_index("ix_feedback_events_number", "feedback_events", ["number"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor", sa.String(length=120), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False, server_default="system"),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("resource", sa.String(length=128), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_audit_logs_company_id", "audit_logs", ["company_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_action", table_name="audit_logs")
    op.drop_index("ix_audit_logs_company_id", table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index("ix_feedback_events_number", table_name="feedback_events")
    op.drop_index("ix_feedback_events_company_id", table_name="feedback_events")
    op.drop_table("feedback_events")

    op.drop_index("ix_ab_events_ab_test_id", table_name="ab_events")
    op.drop_index("ix_ab_events_company_id", table_name="ab_events")
    op.drop_table("ab_events")

    op.drop_index("ix_ab_tests_status", table_name="ab_tests")
    op.drop_index("ix_ab_tests_company_id", table_name="ab_tests")
    op.drop_table("ab_tests")

    op.execute("DROP TYPE IF EXISTS abtest_status")
