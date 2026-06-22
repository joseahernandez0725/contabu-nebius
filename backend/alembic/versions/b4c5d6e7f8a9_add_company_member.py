"""add companymember (roles & permissions per company)

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-06-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b4c5d6e7f8a9"
down_revision: Union[str, Sequence[str], None] = "a3b4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enum 'role' es NUEVO (no existe aún) → se crea aquí (create_type por defecto = True).
    role = sa.Enum("OWNER", "MEMBER", "VIEWER", name="role")
    op.create_table(
        "companymember",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("company.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("role", role, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_companymember_company_id", "companymember", ["company_id"])
    op.create_index("ix_companymember_user_id", "companymember", ["user_id"])
    op.create_index("ix_companymember_email", "companymember", ["email"])


def downgrade() -> None:
    op.drop_index("ix_companymember_email", table_name="companymember")
    op.drop_index("ix_companymember_user_id", table_name="companymember")
    op.drop_index("ix_companymember_company_id", table_name="companymember")
    op.drop_table("companymember")
    sa.Enum(name="role").drop(op.get_bind(), checkfirst=True)
