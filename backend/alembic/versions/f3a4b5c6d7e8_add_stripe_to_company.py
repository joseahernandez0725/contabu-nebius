"""add stripe/subscription fields to company

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-06-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, Sequence[str], None] = "e2f3a4b5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("company", sa.Column("stripe_customer_id", sa.String(), nullable=True))
    op.add_column("company", sa.Column("stripe_subscription_id", sa.String(), nullable=True))
    op.add_column("company", sa.Column("subscription_status", sa.String(), nullable=True))
    op.add_column("company", sa.Column("tx_quota", sa.Integer(), nullable=True))
    op.create_index("ix_company_stripe_customer_id", "company", ["stripe_customer_id"])
    op.create_index("ix_company_stripe_subscription_id", "company", ["stripe_subscription_id"])


def downgrade() -> None:
    op.drop_index("ix_company_stripe_subscription_id", table_name="company")
    op.drop_index("ix_company_stripe_customer_id", table_name="company")
    op.drop_column("company", "tx_quota")
    op.drop_column("company", "subscription_status")
    op.drop_column("company", "stripe_subscription_id")
    op.drop_column("company", "stripe_customer_id")
