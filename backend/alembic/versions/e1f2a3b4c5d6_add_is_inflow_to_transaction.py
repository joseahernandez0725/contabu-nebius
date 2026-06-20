"""add is_inflow to transaction

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-06-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "d0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Dirección real del movimiento en el banco: True = entró plata, False = salió. NULL = desconocida.
    op.add_column("transaction", sa.Column("is_inflow", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("transaction", "is_inflow")
