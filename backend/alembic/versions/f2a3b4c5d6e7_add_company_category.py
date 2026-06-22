"""add company_category (custom P&L categories)

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-06-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, Sequence[str], None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # El tipo enum 'accounttype' YA existe (migraciones previas). Se referencia con el ENUM del
    # dialecto de Postgres y create_type=False: su .create() es no-op → NO emite "CREATE TYPE"
    # dentro de create_table (el sa.Enum genérico sí lo emitía → "type already exists").
    account_type = postgresql.ENUM(
        "REVENUE", "COGS", "OPERATING_EXPENSE", "OTHER_INCOME", "OTHER_EXPENSE", "NON_PL",
        name="accounttype", create_type=False,
    )
    # Categorías personalizadas que una empresa agrega a su plan de cuentas (P&L personalizable v1).
    op.create_table(
        "companycategory",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("company.id"), nullable=False),
        sa.Column("account_type", account_type, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_companycategory_company_id", "companycategory", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_companycategory_company_id", table_name="companycategory")
    op.drop_table("companycategory")
