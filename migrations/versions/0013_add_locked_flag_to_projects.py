"""Add locked flag to projects"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "0013_add_locked_flag_to_projects"
down_revision = "0012_add_github_url_to_projects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add locked flag column to projects table."""
    op.add_column(
        "projects",
        sa.Column(
            "locked",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
            comment="locked=True → impede sincronização automática pelo GitHub",
        ),
    )
    op.execute(text("UPDATE projects SET locked = FALSE WHERE locked IS NULL"))
    op.alter_column(
        "projects",
        "locked",
        server_default=None,
    )


def downgrade() -> None:
    """Remove locked flag column from projects table."""
    op.drop_column("projects", "locked")
