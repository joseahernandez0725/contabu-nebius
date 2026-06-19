"""add settlement_buffer_days to company

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-06-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, Sequence[str], None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Ventana de asentamiento por empresa (días). NULL = usar el default del sistema (5).
    op.add_column("company", sa.Column("settlement_buffer_days", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("company", "settlement_buffer_days")
