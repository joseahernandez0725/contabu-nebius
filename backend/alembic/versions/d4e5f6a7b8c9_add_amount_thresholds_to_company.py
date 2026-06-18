"""add amount thresholds to company

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Umbrales editables por empresa para desambiguar por monto (super / gasolinera). NULL = default.
    op.add_column("company", sa.Column("supermarket_threshold", sa.Numeric(14, 2), nullable=True))
    op.add_column("company", sa.Column("gas_station_threshold", sa.Numeric(14, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("company", "gas_station_threshold")
    op.drop_column("company", "supermarket_threshold")
