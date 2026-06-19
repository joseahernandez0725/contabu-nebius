"""add user table and company.user_id (multi-tenant)

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-06-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, Sequence[str], None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Usuario autenticado (Firebase). firebase_uid es la identidad estable (única).
    op.create_table(
        "user",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("firebase_uid", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_firebase_uid", "user", ["firebase_uid"], unique=True)

    # Dueño de la empresa (multi-tenant). Nullable: datos sembrados antes de la auth quedan sin dueño.
    op.add_column("company", sa.Column("user_id", sa.Integer(), nullable=True))
    op.create_index("ix_company_user_id", "company", ["user_id"])
    op.create_foreign_key("fk_company_user_id", "company", "user", ["user_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_company_user_id", "company", type_="foreignkey")
    op.drop_index("ix_company_user_id", table_name="company")
    op.drop_column("company", "user_id")
    op.drop_index("ix_user_firebase_uid", table_name="user")
    op.drop_table("user")
