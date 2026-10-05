"""add categoryrule.valid_from / valid_to (FASE 10 — la ventana de fechas de una regla)

La primera condición nueva de "la fórmula del libro" (Jose, 3-oct): una regla puede valer solo entre
dos fechas, INCLUSIVAS (p. ej. "STARBUCKS del 3 al 17-ago → Owner Draw": el dueño estaba de
vacaciones). NULL = sin límite por ese lado; las dos NULL = la regla de siempre. Sin datos que migrar.

Revision ID: f0e1d2c3b4a5
Revises: e0f1a2b3c4d5
Create Date: 2026-10-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f0e1d2c3b4a5"
down_revision: Union[str, Sequence[str], None] = "e0f1a2b3c4d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("categoryrule", sa.Column("valid_from", sa.Date(), nullable=True))
    op.add_column("categoryrule", sa.Column("valid_to", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("categoryrule", "valid_to")
    op.drop_column("categoryrule", "valid_from")
