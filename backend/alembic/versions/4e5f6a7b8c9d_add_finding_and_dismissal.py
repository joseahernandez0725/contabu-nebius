"""add finding + proposaldismissal (FASE 11 — los hallazgos guardados y los descartes con motivo)

`finding`: cada hallazgo del auditor con su estado (open / ok / gone), el veredicto del ① revisor y lo
que dijo el bookkeeper. Lo que el bookkeeper ya dijo "está bien" no vuelve a salir.
`proposaldismissal`: las filas del ④ que el bookkeeper descartó, con su motivo; el ④ no las repite.

Revision ID: 4e5f6a7b8c9d
Revises: d3c4e5f6a7b8
Create Date: 2026-10-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "4e5f6a7b8c9d"
down_revision: Union[str, Sequence[str], None] = "d3c4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = sqlmodel.sql.sqltypes.AutoString


def upgrade() -> None:
    op.create_table(
        "finding",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("key", S(), nullable=False),
        sa.Column("type", S(), nullable=False),
        sa.Column("severity", S(), nullable=False),
        sa.Column("merchant", S(), nullable=True),
        sa.Column("transaction_ids", sa.JSON(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("status", S(), nullable=False),
        sa.Column("agent_verdict", S(), nullable=True),
        sa.Column("agent_observation", S(), nullable=True),
        sa.Column("agent_lang", S(), nullable=True),
        sa.Column("agent_reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_by_user_id", sa.Integer(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("resolution_note", S(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_finding_company_id"), "finding", ["company_id"], unique=False)
    op.create_index(op.f("ix_finding_key"), "finding", ["key"], unique=False)
    op.create_index(op.f("ix_finding_status"), "finding", ["status"], unique=False)
    op.create_table(
        "proposaldismissal",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("keyword", S(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("account_type", S(), nullable=True),
        sa.Column("category", S(), nullable=True),
        sa.Column("reason_code", S(), nullable=False),
        sa.Column("reason_text", S(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_proposaldismissal_company_id"), "proposaldismissal", ["company_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_proposaldismissal_company_id"), table_name="proposaldismissal")
    op.drop_table("proposaldismissal")
    op.drop_index(op.f("ix_finding_status"), table_name="finding")
    op.drop_index(op.f("ix_finding_key"), table_name="finding")
    op.drop_index(op.f("ix_finding_company_id"), table_name="finding")
    op.drop_table("finding")
