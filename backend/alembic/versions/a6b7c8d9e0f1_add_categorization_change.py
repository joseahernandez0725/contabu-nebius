"""add categorizationchange (FASE 10 — historial de correcciones ANTES → DESPUÉS)

Una tabla nueva, sin claves foráneas (referencias suaves, como `matched_rule_id`): es historia y no
debe impedir borrar una transacción o una regla. Una fila por cada cambio REAL de `account_type`,
`category` o `subcategory` de una transacción ya registrada (ver `backend/corrections.py`).

Revision ID: a6b7c8d9e0f1
Revises: f5a6b7c8d9e0
Create Date: 2026-10-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "a6b7c8d9e0f1"
down_revision: Union[str, Sequence[str], None] = "f5a6b7c8d9e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "categorizationchange",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("changed_at", sa.DateTime(), nullable=False),
        sa.Column("changed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("via", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("source_before", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("source_after", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("account_type_before", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("account_type_after", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("category_before", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("category_after", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("subcategory_before", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("subcategory_after", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("matched_rule_id_before", sa.Integer(), nullable=True),
        sa.Column("matched_rule_id_after", sa.Integer(), nullable=True),
        sa.Column("reason", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    for col in ("transaction_id", "company_id", "changed_at", "changed_by_user_id", "via"):
        op.create_index(op.f(f"ix_categorizationchange_{col}"), "categorizationchange", [col])


def downgrade() -> None:
    for col in ("via", "changed_by_user_id", "changed_at", "company_id", "transaction_id"):
        op.drop_index(op.f(f"ix_categorizationchange_{col}"), table_name="categorizationchange")
    op.drop_table("categorizationchange")
