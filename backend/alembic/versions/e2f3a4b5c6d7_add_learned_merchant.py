"""add learnedmerchant (AI-discovered gas/supermarket merchants)

Revision ID: e2f3a4b5c6d7
Revises: c5d6e7f8a9b0
Create Date: 2026-06-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, Sequence[str], None] = "c5d6e7f8a9b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # El enum 'rulescope' YA existe (lo usa categoryrule) → NO recrear el tipo. Usar el ENUM de
    # PostgreSQL con create_type=False (el sa.Enum genérico NO honra el flag y reintenta CREATE TYPE).
    rulescope = postgresql.ENUM("SECTOR", "COMPANY", name="rulescope", create_type=False)
    op.create_table(
        "learnedmerchant",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("scope", rulescope, nullable=False),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("company.id"), nullable=True),
        sa.Column("sector", sa.String(), nullable=True),
        sa.Column("match_text", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_learnedmerchant_company_id", "learnedmerchant", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_learnedmerchant_company_id", table_name="learnedmerchant")
    op.drop_table("learnedmerchant")
