"""add categoryrule.style (FASE 10 — resaltar filas en la hoja de Reglas)

Presentación de la fila de una regla en la hoja de Reglas: {bg, bold, italic}. Es solo cómo se ve
(como `cellformat` en la Hoja): no cambia qué categoriza la regla. Nullable; sin datos que migrar.

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-10-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d9e0f1a2b3c4"
down_revision: Union[str, Sequence[str], None] = "c8d9e0f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("categoryrule", sa.Column("style", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("categoryrule", "style")
