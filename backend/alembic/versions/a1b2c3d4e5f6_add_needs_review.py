"""add needs_review and review_question to transaction

Revision ID: a1b2c3d4e5f6
Revises: f1a2b3c4d5e6
Create Date: 2026-06-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # needs_review NOT NULL → server_default false para las filas que ya existen.
    op.add_column(
        "transaction",
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "transaction",
        sa.Column("review_question", sa.String(), nullable=True),
    )
    # De aquí en adelante el valor lo pone la app (no la DB).
    op.alter_column("transaction", "needs_review", server_default=None)


def downgrade() -> None:
    op.drop_column("transaction", "review_question")
    op.drop_column("transaction", "needs_review")
