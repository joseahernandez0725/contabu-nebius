"""add merchantjudgment (FASE 11 — ¿personal PARA ESTE NEGOCIO?)

Nemotron opina, UNA vez por comercio y libro, si un comercio es de uso normal para ESE negocio, mixto o
probablemente personal. Se guarda por libro (la respuesta depende del negocio), aparte de la caché del ②
(que es evidencia de internet, por oficina). Nunca categoriza: alimenta el hallazgo `personal_use`.

Revision ID: 6a7b8c9d0e1f
Revises: 5f6a7b8c9d0e
Create Date: 2026-10-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "6a7b8c9d0e1f"
down_revision: Union[str, Sequence[str], None] = "5f6a7b8c9d0e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "merchantjudgment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("merchant_key", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("keyword", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("verdict", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("confidence", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("reason_en", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("reason_es", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("context_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("model", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_merchantjudgment_company_id"), "merchantjudgment", ["company_id"], unique=False)
    op.create_index(op.f("ix_merchantjudgment_merchant_key"), "merchantjudgment", ["merchant_key"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_merchantjudgment_merchant_key"), table_name="merchantjudgment")
    op.drop_index(op.f("ix_merchantjudgment_company_id"), table_name="merchantjudgment")
    op.drop_table("merchantjudgment")
