"""add booknote.paused_at (FASE 11 — pausar una nota del libro sin borrarla)

Ronda de diseño 2 (18-oct, aprobado por Jose): las notas tienen página propia y se pueden PAUSAR.
Pausada, ningún agente la lee y el código no la aplica (ni el chequeo 📌 ni el silencio 🚫). Nula =
activa; la fecha dice desde cuándo está en pausa. Activarla de nuevo la retoma tal cual (si el texto no
cambió, su chequeo sigue valiendo y no se vuelve a pagar).

Revision ID: 9e0f1a2b3c4d
Revises: 8d9e0f1a2b3c
Create Date: 2026-10-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9e0f1a2b3c4d"
down_revision: Union[str, Sequence[str], None] = "8d9e0f1a2b3c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("booknote", sa.Column("paused_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("booknote", "paused_at")
