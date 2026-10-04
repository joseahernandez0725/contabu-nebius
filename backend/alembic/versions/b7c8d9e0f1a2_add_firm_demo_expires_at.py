"""add firm.demo_expires_at (FASE 13 — el acceso por visitante del demo)

Cada visitante del demo público recibe su propia oficina (login anónimo de Firebase). Esas oficinas se
borran solas a los 7 días (Neon Free tiene tope de disco). Esta columna dice CUÁNDO, y es también el
candado: la limpieza solo puede borrar oficinas con esta marca vencida. Una oficina real la tiene en NULL,
así que un servicio de demo mal configurado contra la base real no puede borrar nada que no sea demo.

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
Create Date: 2026-10-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, Sequence[str], None] = "a6b7c8d9e0f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("firm", sa.Column("demo_expires_at", sa.DateTime(), nullable=True))
    op.create_index("ix_firm_demo_expires_at", "firm", ["demo_expires_at"])


def downgrade() -> None:
    op.drop_index("ix_firm_demo_expires_at", table_name="firm")
    op.drop_column("firm", "demo_expires_at")
