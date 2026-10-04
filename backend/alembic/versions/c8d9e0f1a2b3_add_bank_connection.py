"""add bankconnection (FASE 10 — bancos vinculados por Plaid)

Una fila por Item de Plaid (una institución, una o varias cuentas), POR LIBRO. El access_token va
CIFRADO (`access_token_enc`, Fernet); nunca se guarda en claro. `next_cursor` es el cursor de
`/transactions/sync`: "Actualizar" pide solo lo que cambió desde ahí. Única por (company_id, item_id).

(Nació el 3-oct para Teller y se rehízo el mismo día para Plaid — Teller cerró su API —, antes de
commitearse: por eso no hay una segunda migración.)

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-10-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "c8d9e0f1a2b3"
down_revision: Union[str, Sequence[str], None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bankconnection",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("environment", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("item_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("institution_name", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("access_token_enc", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("accounts", sa.JSON(), nullable=True),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("last_error", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("next_cursor", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "item_id", name="uq_bankconnection_company_item"),
    )
    op.create_index(op.f("ix_bankconnection_company_id"), "bankconnection", ["company_id"])
    op.create_index(op.f("ix_bankconnection_item_id"), "bankconnection", ["item_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_bankconnection_item_id"), table_name="bankconnection")
    op.drop_index(op.f("ix_bankconnection_company_id"), table_name="bankconnection")
    op.drop_table("bankconnection")
