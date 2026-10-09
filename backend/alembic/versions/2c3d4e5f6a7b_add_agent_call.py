"""add agentcall (FASE 11 — lo que cuesta cada llamada de un agente a Nemotron)

Tokens (entrada, salida, razonamiento estimado), costo en dólares, latencia y resultado de cada
llamada de los agentes ④ (redactor) y ① (revisor). Sirve para medir desde el principio y para el tope
de llamadas por oficina del demo. Nunca guarda el contenido del prompt ni de la respuesta.

Revision ID: 2c3d4e5f6a7b
Revises: 9a8b7c6d5e4f
Create Date: 2026-10-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "2c3d4e5f6a7b"
down_revision: Union[str, Sequence[str], None] = "9a8b7c6d5e4f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agentcall",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("firm_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("agent", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("model", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("reasoning", sa.Boolean(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("reasoning_tokens_est", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("finish_reason", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("outcome", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("items_out", sa.Integer(), nullable=False),
        sa.Column("items_valid", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agentcall_company_id"), "agentcall", ["company_id"], unique=False)
    op.create_index(op.f("ix_agentcall_firm_id"), "agentcall", ["firm_id"], unique=False)
    op.create_index(op.f("ix_agentcall_agent"), "agentcall", ["agent"], unique=False)
    op.create_index(op.f("ix_agentcall_created_at"), "agentcall", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_agentcall_created_at"), table_name="agentcall")
    op.drop_index(op.f("ix_agentcall_agent"), table_name="agentcall")
    op.drop_index(op.f("ix_agentcall_firm_id"), table_name="agentcall")
    op.drop_index(op.f("ix_agentcall_company_id"), table_name="agentcall")
    op.drop_table("agentcall")
