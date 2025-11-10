"""Add github_url column to projects"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0012_add_github_url_to_projects"
down_revision = "0011_followup_post_appointment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("github_url", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "github_url")
