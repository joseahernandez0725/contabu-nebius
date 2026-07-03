"""add matched_rule_id to transaction (provenance, FASE 9)

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-07-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a4b5c6d7e8f9"
down_revision: Union[str, Sequence[str], None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Referencia SUAVE (sin FK a propósito): conserva la procedencia histórica aunque la regla
    # se edite/borre, y borrar reglas nunca falla por integridad referencial.
    op.add_column("transaction", sa.Column("matched_rule_id", sa.Integer(), nullable=True))
    op.create_index("ix_transaction_matched_rule_id", "transaction", ["matched_rule_id"])


def downgrade() -> None:
    op.drop_index("ix_transaction_matched_rule_id", table_name="transaction")
    op.drop_column("transaction", "matched_rule_id")
