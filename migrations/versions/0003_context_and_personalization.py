"""add context and personalization tables"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0003_context_and_personalization"
down_revision = "0002_add_projects_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customer_contexts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("number", sa.String(length=32), nullable=False),
        sa.Column("frequent_topics", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("product_mentions", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("preferences", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column("last_subject", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("number", name="uq_customer_contexts_number"),
    )
    op.create_index(
        "ix_customer_contexts_number",
        "customer_contexts",
        ["number"],
        unique=False,
    )

    op.create_table(
        "personalization_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tone_of_voice", sa.String(length=64), nullable=False, server_default="amigavel"),
        sa.Column("message_limit", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("opening_phrases", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("ai_enabled", sa.Boolean(), nullable=False, server_default=sa.sql.expression.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )



def downgrade() -> None:
    op.drop_table("personalization_configs")
    op.drop_index("ix_customer_contexts_number", table_name="customer_contexts")
    op.drop_table("customer_contexts")
