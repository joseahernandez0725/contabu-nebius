"""add transaction note (FASE 10 — la nota de la línea)

La nota del bookkeeper sobre UNA transacción: texto libre, única de esa línea. Distinta de
`sheetlabel` (catálogo reutilizable) y de la observación (la escribe el agente auditor).

Revision ID: a0b1c2d3e4f5
Revises: f9a0b1c2d3e4
"""
from alembic import op
import sqlalchemy as sa
import sqlmodel

revision = "a0b1c2d3e4f5"
down_revision = "f9a0b1c2d3e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transaction", sa.Column("note", sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
    op.drop_column("transaction", "note")
