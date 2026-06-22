"""add check_number to transaction

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-06-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c5d6e7f8a9b0"
down_revision: Union[str, Sequence[str], None] = "b4c5d6e7f8a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("transaction", sa.Column("check_number", sa.String(), nullable=True))
    op.create_index("ix_transaction_check_number", "transaction", ["check_number"])


def downgrade() -> None:
    op.drop_index("ix_transaction_check_number", table_name="transaction")
    op.drop_column("transaction", "check_number")
