"""add categoryrule.origin (FASE 10 — de dónde salió la regla)

Para que "Ver como fórmula" marque las reglas que propuso el agente (④, FASE 11). Valores:
"agent" (aprobada desde una propuesta del agente) · "import" (de un archivo) · NULL (escrita en la hoja o
aprendida de una corrección: lo de siempre). Solo informativo: no cambia qué categoriza la regla.

Revision ID: c2b3d4e5f6a7
Revises: b1a2c3d4e5f6
Create Date: 2026-10-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c2b3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "b1a2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("categoryrule", sa.Column("origin", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("categoryrule", "origin")
