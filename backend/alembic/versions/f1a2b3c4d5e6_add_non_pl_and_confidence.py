"""add NON_PL account type and confidence column

Revision ID: f1a2b3c4d5e6
Revises: e057a6fe0247
Create Date: 2026-06-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'e057a6fe0247'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nuevo valor del enum: movimientos que NO entran al P&L (transferencias, draws, etc.).
    # Postgres 12+ permite ADD VALUE dentro de una transacción (mientras no se use en la misma tx).
    op.execute("ALTER TYPE accounttype ADD VALUE IF NOT EXISTS 'NON_PL'")

    # Persistir la confianza de la IA (0-1): se usa para revisión y para las reglas Pro.
    op.add_column('transaction', sa.Column('confidence', sa.Float(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('transaction', 'confidence')
    # Nota: PostgreSQL no permite quitar un valor de un enum de forma simple; 'NON_PL' permanece.
