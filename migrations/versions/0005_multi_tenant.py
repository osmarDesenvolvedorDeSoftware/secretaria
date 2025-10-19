"""multi-tenant foundations"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0005_multi_tenant"
down_revision = "0004_style_preferences"
branch_labels = None
depends_on = None


company_status_enum = sa.Enum("ativo", "suspenso", "cancelado", name="company_status")
subscription_status_enum = sa.Enum(
    "ativa", "pendente", "cancelada", "suspensa", name="subscription_status"
)


def upgrade() -> None:
    company_status_enum.create(op.get_bind(), checkfirst=True)
    subscription_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False, unique=True),
        sa.Column("description", sa.Text()),
        sa.Column("limite_mensagens", sa.Integer(), nullable=False, server_default="1000"),
        sa.Column("limite_tokens", sa.Integer(), nullable=False, server_default="500000"),
        sa.Column("preco", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("features", sa.JSON(), nullable=False, server_default="[]"),
    )

    op.create_table(
        "companies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False, unique=True),
        sa.Column("status", company_status_enum, nullable=False, server_default="ativo"),
        sa.Column("current_plan_id", sa.Integer(), sa.ForeignKey("plans.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    op.create_index("ix_companies_domain", "companies", ["domain"], unique=True)

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ciclo", sa.String(length=32), nullable=False, server_default="mensal"),
        sa.Column("status", subscription_status_enum, nullable=False, server_default="pendente"),
        sa.Column("vencimento", sa.Date()),
        sa.Column("started_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("ended_at", sa.DateTime()),
    )

    plans_table = sa.table(
        "plans",
        sa.column("id", sa.Integer()),
        sa.column("name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("limite_mensagens", sa.Integer()),
        sa.column("limite_tokens", sa.Integer()),
        sa.column("preco", sa.Numeric()),
        sa.column("features", sa.JSON()),
    )

    companies_table = sa.table(
        "companies",
        sa.column("id", sa.Integer()),
        sa.column("name", sa.String()),
        sa.column("domain", sa.String()),
        sa.column("status", company_status_enum),
        sa.column("current_plan_id", sa.Integer()),
    )

    subscriptions_table = sa.table(
        "subscriptions",
        sa.column("company_id", sa.Integer()),
        sa.column("plan_id", sa.Integer()),
        sa.column("ciclo", sa.String()),
        sa.column("status", subscription_status_enum),
    )

    op.bulk_insert(
        plans_table,
        [
            {
                "id": 1,
                "name": "Starter",
                "description": "Plano padrão com limites básicos",
                "limite_mensagens": 1000,
                "limite_tokens": 500000,
                "preco": 0,
                "features": ["contexto_compartilhado", "painel_basico"],
            }
        ],
    )

    op.bulk_insert(
        companies_table,
        [
            {
                "id": 1,
                "name": "Empresa Padrão",
                "domain": "default.local",
                "status": "ativo",
                "current_plan_id": 1,
            }
        ],
    )

    op.bulk_insert(
        subscriptions_table,
        [
            {
                "company_id": 1,
                "plan_id": 1,
                "ciclo": "mensal",
                "status": "ativa",
            }
        ],
    )

    # Add company references ---------------------------------------------------------
    tables_with_company = [
        "projects",
        "customer_contexts",
        "personalization_configs",
        "delivery_logs",
        "conversations",
    ]

    for table in tables_with_company:
        op.add_column(
            table,
            sa.Column("company_id", sa.Integer(), nullable=True),
        )
        op.create_index(f"ix_{table}_company_id", table, ["company_id"])
        op.create_foreign_key(
            f"fk_{table}_company_id_companies",
            table,
            "companies",
            ["company_id"],
            ["id"],
            ondelete="CASCADE",
        )

    # Drop old unique constraint on number for customer_contexts
    op.drop_constraint(
        "customer_contexts_number_key",
        "customer_contexts",
        type_="unique",
    )

    op.create_unique_constraint(
        "uq_customer_context_company_number",
        "customer_contexts",
        ["company_id", "number"],
    )

    op.create_unique_constraint(
        "uq_personalization_company",
        "personalization_configs",
        ["company_id"],
    )

    op.create_unique_constraint(
        "uq_conversation_company_number",
        "conversations",
        ["company_id", "number"],
    )

    connection = op.get_bind()
    for table in tables_with_company:
        connection.execute(sa.text(f"UPDATE {table} SET company_id = 1 WHERE company_id IS NULL"))
        op.alter_column(table, "company_id", existing_type=sa.Integer(), nullable=False)


def downgrade() -> None:
    tables_with_company = [
        "projects",
        "customer_contexts",
        "personalization_configs",
        "delivery_logs",
        "conversations",
    ]

    for table in tables_with_company:
        op.drop_constraint(f"fk_{table}_company_id_companies", table, type_="foreignkey")
        op.drop_index(f"ix_{table}_company_id", table)
        op.drop_column(table, "company_id")

    op.drop_constraint("uq_conversation_company_number", "conversations", type_="unique")
    op.drop_constraint("uq_personalization_company", "personalization_configs", type_="unique")
    op.drop_constraint("uq_customer_context_company_number", "customer_contexts", type_="unique")

    op.create_unique_constraint(
        "customer_contexts_number_key",
        "customer_contexts",
        ["number"],
    )

    op.drop_table("subscriptions")
    op.drop_index("ix_companies_domain", "companies")
    op.drop_table("companies")
    op.drop_table("plans")

    subscription_status_enum.drop(op.get_bind(), checkfirst=True)
    company_status_enum.drop(op.get_bind(), checkfirst=True)
