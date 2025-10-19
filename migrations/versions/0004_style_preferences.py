from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0004_style_preferences"
down_revision = "0003_context_and_personalization"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "personalization_configs",
        sa.Column("formality_level", sa.Integer(), nullable=False, server_default="50"),
    )
    op.add_column(
        "personalization_configs",
        sa.Column("empathy_level", sa.Integer(), nullable=False, server_default="70"),
    )
    op.add_column(
        "personalization_configs",
        sa.Column("adaptive_humor", sa.Boolean(), nullable=False, server_default=sa.sql.expression.true()),
    )
    op.alter_column("personalization_configs", "formality_level", server_default=None)
    op.alter_column("personalization_configs", "empathy_level", server_default=None)
    op.alter_column("personalization_configs", "adaptive_humor", server_default=None)


def downgrade() -> None:
    op.drop_column("personalization_configs", "adaptive_humor")
    op.drop_column("personalization_configs", "empathy_level")
    op.drop_column("personalization_configs", "formality_level")
