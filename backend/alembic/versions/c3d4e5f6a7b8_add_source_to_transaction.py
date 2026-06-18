"""add source to transaction

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Quién categorizó la transacción: "rule" | "ai" | "manual" (NULL = sin categorizar).
    op.add_column("transaction", sa.Column("source", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("transaction", "source")
