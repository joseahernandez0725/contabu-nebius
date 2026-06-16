"""add account_type to categoryrule

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # El tipo enum 'accounttype' ya existe → create_type=False para no recrearlo.
    accounttype = sa.Enum(
        "REVENUE", "COGS", "OPERATING_EXPENSE", "OTHER_INCOME", "OTHER_EXPENSE", "NON_PL",
        name="accounttype", create_type=False,
    )
    op.add_column("categoryrule", sa.Column("account_type", accounttype, nullable=True))


def downgrade() -> None:
    op.drop_column("categoryrule", "account_type")
