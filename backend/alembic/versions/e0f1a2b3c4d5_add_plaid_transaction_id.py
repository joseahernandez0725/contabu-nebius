"""add transaction.plaid_transaction_id + bank_removed_at (FASE 10 — avisos del banco vinculado)

- `plaid_transaction_id`: el identificador de Plaid de cada movimiento que vino de un banco
  vinculado (nullable: las filas de un CSV no lo tienen). Con él, re-sincronizar deduplica EXACTO y
  los avisos del banco (`modified` / `removed`) se aplican a LA fila que corresponde. Con índice.
- `bank_removed_at`: el banco RETIRÓ el movimiento después de registrado. No se borra: se marca, se
  ve en la Hoja y el bookkeeper decide.

Nueva y no dentro de `c8d9e0f1a2b3` (como pedía el chat central si seguía siendo la cabeza): la
cabeza real ya era `d9e0f1a2b3c4`. Nullable; sin datos que migrar.

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
Create Date: 2026-10-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e0f1a2b3c4d5"
down_revision: Union[str, Sequence[str], None] = "d9e0f1a2b3c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("transaction", sa.Column("plaid_transaction_id", sa.String(), nullable=True))
    op.create_index("ix_transaction_plaid_transaction_id", "transaction", ["plaid_transaction_id"])
    op.add_column("transaction", sa.Column("bank_removed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("transaction", "bank_removed_at")
    op.drop_index("ix_transaction_plaid_transaction_id", table_name="transaction")
    op.drop_column("transaction", "plaid_transaction_id")
