"""add account_type to transaction

Revision ID: e057a6fe0247
Revises: 839827b7be92
Create Date: 2026-06-14 22:21:07.346130

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'e057a6fe0247'
down_revision: Union[str, Sequence[str], None] = '839827b7be92'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Crear primero el tipo enum en Postgres (Alembic no lo hace solo en add_column).
    accounttype = sa.Enum(
        'REVENUE', 'COGS', 'OPERATING_EXPENSE', 'OTHER_INCOME', 'OTHER_EXPENSE',
        name='accounttype',
    )
    accounttype.create(op.get_bind(), checkfirst=True)
    op.add_column('transaction', sa.Column('account_type', accounttype, nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('transaction', 'account_type')
    sa.Enum(name='accounttype').drop(op.get_bind(), checkfirst=True)
