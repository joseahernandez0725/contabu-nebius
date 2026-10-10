"""add categoryrule.amount_min / amount_max (FASE 10 — la condición de monto de una regla)

Segunda condición de "la fórmula del libro" (Buzón 3-oct, punto 1; pedida por la F11): una regla puede
valer solo para movimientos de cierto monto, INCLUSIVO y sobre la MAGNITUD (`transaction.amount` ya es
positivo; el signo va en `is_inflow`). Ej.: "SAFEWAY y más de $50 → Owner Draw". NULL = sin límite.

Revision ID: d3c4e5f6a7b8
Revises: c2b3d4e5f6a7
Create Date: 2026-10-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d3c4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "c2b3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("categoryrule", sa.Column("amount_min", sa.Numeric(14, 2), nullable=True))
    op.add_column("categoryrule", sa.Column("amount_max", sa.Numeric(14, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("categoryrule", "amount_max")
    op.drop_column("categoryrule", "amount_min")
