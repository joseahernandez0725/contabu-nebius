"""add merchantlookup.search_depth (FASE 11 — Tavily rediseñado para gastar poco)

Primero una búsqueda "basic" (1 crédito); si la confianza no sale alta, UNA "advanced" (2 créditos). Se
guarda cuál se usó: "basic" o "basic+advanced". Nulo en las filas anteriores y en las sembradas del demo.

Revision ID: 7c8d9e0f1a2b
Revises: 6a7b8c9d0e1f
Create Date: 2026-10-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "7c8d9e0f1a2b"
down_revision: Union[str, Sequence[str], None] = "6a7b8c9d0e1f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("merchantlookup", sa.Column("search_depth", sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
    op.drop_column("merchantlookup", "search_depth")
