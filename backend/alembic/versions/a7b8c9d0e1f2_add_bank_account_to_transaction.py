"""add bank_account to transaction

Revision ID: a7b8c9d0e1f2
Revises: d4e5f6a7b8c9
Create Date: 2026-06-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Cuenta bancaria de origen de la transacción (la detecta la IA del archivo). NULL = desconocida.
    op.add_column("transaction", sa.Column("bank_account", sa.String(), nullable=True))
    op.create_index("ix_transaction_bank_account", "transaction", ["bank_account"])


def downgrade() -> None:
    op.drop_index("ix_transaction_bank_account", table_name="transaction")
    op.drop_column("transaction", "bank_account")
