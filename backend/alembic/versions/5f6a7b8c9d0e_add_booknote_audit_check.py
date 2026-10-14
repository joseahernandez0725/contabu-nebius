"""add booknote.audit_check (FASE 11 — la nota del bookkeeper traducida a un CHEQUEO del auditor)

Nemotron lee una nota 📌 ("las compras de supermercado de más de $50 suelen ser personales") y la
traduce UNA vez a un chequeo estructurado (tipos de comercio de la lista cerrada, comercios del libro,
monto mínimo escrito en la nota). Se guarda aquí; el auditor lo aplica en código, gratis, cada vez.
Si el texto o la ventana de la nota cambian, el chequeo queda viejo y se rehace.

Revision ID: 5f6a7b8c9d0e
Revises: 4e5f6a7b8c9d
Create Date: 2026-10-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "5f6a7b8c9d0e"
down_revision: Union[str, Sequence[str], None] = "4e5f6a7b8c9d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("booknote", sa.Column("audit_check", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("booknote", "audit_check")
