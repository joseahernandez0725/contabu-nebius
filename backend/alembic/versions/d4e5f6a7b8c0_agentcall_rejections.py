"""add agentcall.rejections (FASE 11 — por qué el validador rechazó las filas del ④)

Cada llamada del ④ redactor guarda, por motivo, cuántas de sus filas rechazó el validador
(`{"no_evidence": 8}`): el primer control que para cada fila. Antes solo quedaba `items_valid` y el motivo se
perdía (la prueba de campo del 20-oct tuvo que reproducir la corrida para saber por qué pasaron 0 de 8).
Nulo en las demás llamadas y en las de antes.

Revision ID: d4e5f6a7b8c0
Revises: c3d4e5f6a7b9
Create Date: 2026-10-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e5f6a7b8c0"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agentcall", sa.Column("rejections", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("agentcall", "rejections")
