"""add demooffice + demoaccess + democodeattempt (FASE 13 — las dos puertas del demo)

- `demooffice`: el papel de una oficina en el demo — la visita guiada (tour), una en espera (pool) o una
  asignada a quien canjeó un código (live).
- `demoaccess`: qué usuario canjeó qué código ("devpost" | "guest"; el nombre, nunca el código).
- `democodeattempt`: los intentos fallidos, para el límite por usuario y por IP (la IP como huella).

Tablas nuevas, sin tocar columnas de otras: Auditoría corre exámenes sobre copias sin migrar.

Revision ID: e5f6a7b8c9d1
Revises: d4e5f6a7b8c0
Create Date: 2026-10-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5f6a7b8c9d1"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "demooffice",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("firm_id", sa.Integer(), sa.ForeignKey("firm.id"), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_demooffice_firm_id", "demooffice", ["firm_id"], unique=True)
    op.create_index("ix_demooffice_kind", "demooffice", ["kind"])

    op.create_table(
        "demoaccess",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("code_name", sa.String(), nullable=False),
        sa.Column("granted_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_demoaccess_user_id", "demoaccess", ["user_id"], unique=True)

    op.create_table(
        "democodeattempt",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("ip_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_democodeattempt_user_id", "democodeattempt", ["user_id"])
    op.create_index("ix_democodeattempt_ip_hash", "democodeattempt", ["ip_hash"])
    op.create_index("ix_democodeattempt_created_at", "democodeattempt", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_democodeattempt_created_at", table_name="democodeattempt")
    op.drop_index("ix_democodeattempt_ip_hash", table_name="democodeattempt")
    op.drop_index("ix_democodeattempt_user_id", table_name="democodeattempt")
    op.drop_table("democodeattempt")
    op.drop_index("ix_demoaccess_user_id", table_name="demoaccess")
    op.drop_table("demoaccess")
    op.drop_index("ix_demooffice_kind", table_name="demooffice")
    op.drop_index("ix_demooffice_firm_id", table_name="demooffice")
    op.drop_table("demooffice")
