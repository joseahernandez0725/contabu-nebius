"""add bookcriterion (FASE 13 — los criterios del libro)

Una tabla: los criterios con que el bookkeeper decide, POR LIBRO, los casos que se repiten
(supermercados, gasolineras, tarjetas propias) y cuántos días esperar lo reciente.

NO borra las columnas viejas de `company` (supermarket_threshold, gas_station_threshold,
settlement_buffer_days): el motor de subida todavía las lee hasta que el área Movimientos lo
cambie a leer criterios. Se borran en una migración APARTE, después. Cambiar el modelo y quitar lo
viejo en el mismo paso no deja saber cuál de los dos rompió algo.

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-10-01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d3e4f5a6b7c8"
down_revision: Union[str, Sequence[str], None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # `accounttype` YA existe (lo usan transaction y categoryrule) → no se vuelve a crear.
    accounttype = postgresql.ENUM(name="accounttype", create_type=False)
    op.create_table(
        "bookcriterion",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("company.id"), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("keywords", sa.JSON(), nullable=True),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("limit_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("below_account_type", accounttype, nullable=True),
        sa.Column("below_category", sa.String(), nullable=True),
        sa.Column("above_account_type", accounttype, nullable=True),
        sa.Column("above_category", sa.String(), nullable=True),
        sa.Column("review_near", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("wait_days", sa.Integer(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_bookcriterion_company_id", "bookcriterion", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_bookcriterion_company_id", table_name="bookcriterion")
    op.drop_table("bookcriterion")
