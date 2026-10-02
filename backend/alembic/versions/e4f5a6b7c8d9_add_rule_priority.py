"""add categoryrule.priority (FASE 10 — prioridad manual de las reglas)

Una columna nullable. Vacía = el orden automático de siempre (la más larga gana; empate →
alfabético). Con número, la regla va ANTES que todas las automáticas, 1 primero — como subir una
fila en la hoja de Excel del bookkeeper.

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-10-01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e4f5a6b7c8d9"
down_revision: Union[str, Sequence[str], None] = "d3e4f5a6b7c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("categoryrule", sa.Column("priority", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("categoryrule", "priority")
