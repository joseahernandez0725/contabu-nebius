"""add subcategory to categoryrule (manual rules, hallazgo 8.10)

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
Create Date: 2026-07-12

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e8f9a0b1c2d3"
down_revision: Union[str, Sequence[str], None] = "d7e8f9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Subcategoría que asigna la regla (comercio presentable). NULL → se usa el match_text.
    op.add_column("categoryrule", sa.Column("subcategory", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("categoryrule", "subcategory")
