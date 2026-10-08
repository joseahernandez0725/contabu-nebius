"""add booknote (FASE 11 — las notas del libro)

Lo que el bookkeeper le escribe al agente sobre cada cliente: 📌 tenlo en cuenta / 🚫 no me reportes,
con prioridad, alcance (comercio) y vigencia opcionales. Es la entrada del agente ④ (redactor de la
fórmula del libro). Una nota nunca categoriza. Tabla nueva, sin datos que migrar.

Revision ID: 9a8b7c6d5e4f
Revises: f0e1d2c3b4a5
Create Date: 2026-10-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "9a8b7c6d5e4f"
down_revision: Union[str, Sequence[str], None] = "f0e1d2c3b4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "booknote",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("kind", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("priority", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("text", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("merchant", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_booknote_company_id"), "booknote", ["company_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_booknote_company_id"), table_name="booknote")
    op.drop_table("booknote")
