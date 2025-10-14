from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("number", sa.String(length=32), nullable=False),
        sa.Column("user_name", sa.String(length=255)),
        sa.Column("last_message", sa.Text()),
        sa.Column("context_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_conversations_number", "conversations", ["number"])

    op.create_table(
        "delivery_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("number", sa.String(length=32), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=128)),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("delivery_logs")
    op.drop_index("ix_conversations_number", table_name="conversations")
    op.drop_table("conversations")
