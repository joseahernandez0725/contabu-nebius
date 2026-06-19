"""add fingerprint to transaction

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-06-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Huella de contenido para deduplicar re-subidas y rangos de fechas solapados.
    op.add_column("transaction", sa.Column("fingerprint", sa.String(), nullable=True))
    op.create_index("ix_transaction_fingerprint", "transaction", ["fingerprint"])


def downgrade() -> None:
    op.drop_index("ix_transaction_fingerprint", table_name="transaction")
    op.drop_column("transaction", "fingerprint")
