"""add ai original prediction to transaction (online accuracy, FASE 9 Paso 6)

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
Create Date: 2026-07-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c6d7e8f9a0b1"
down_revision: Union[str, Sequence[str], None] = "b5c6d7e8f9a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Se escribe UNA vez cuando la IA categoriza y nunca se toca al corregir: comparar contra la
    # categoría final (la verdad del dueño) = precisión ONLINE del cerebro en producción.
    op.add_column("transaction", sa.Column("ai_original_account_type", sa.String(), nullable=True))
    op.add_column("transaction", sa.Column("ai_original_category", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("transaction", "ai_original_category")
    op.drop_column("transaction", "ai_original_account_type")
