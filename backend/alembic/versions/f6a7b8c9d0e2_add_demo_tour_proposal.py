"""add demotourproposal (FASE 13 — las reglas que el ④ propuso al crear la visita guiada)

Las propuestas del ④ no se guardan en ningún otro lado (se aprueban en pantalla). La visita guiada es de solo
lectura y su recorrido las muestra: se guardan aquí al crearla (`demo.py tour`), tal cual `agents.proposal_dict`.
Tabla nueva, sin tocar las de otras áreas: Auditoría corre exámenes sobre copias sin migrar.

Revision ID: f6a7b8c9d0e2
Revises: e5f6a7b8c9d1
Create Date: 2026-10-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6a7b8c9d0e2"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "demotourproposal",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("company.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_demotourproposal_company_id", "demotourproposal", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_demotourproposal_company_id", table_name="demotourproposal")
    op.drop_table("demotourproposal")
