"""drop bookcriterion + company amount thresholds (FASE 13 — fuera los ajustes por monto)

Decisión de Jose (1-oct-2026): se eliminan los ajustes por monto. Lo que el motor no decida con una
REGLA queda Pendiente; el criterio del bookkeeper vive en las notas de la pestaña Auditoría (F11).

Borra:
  - la tabla `bookcriterion` (los criterios del libro, 1-oct)
  - `company.supermarket_threshold` y `company.gas_station_threshold`
Se QUEDA `company.settlement_buffer_days`: los días de espera no son un monto (Ajustes del libro).

Antes de borrar la tabla copia los días de espera que solo vivieran en ella (una tarjeta
"settlement" con la columna de la empresa vacía), para no perder lo que el bookkeeper configuró.

Desbloqueada por Movimientos (📬 Buzón, 1-oct): el motor ya solo usa `criteria.settlement_days()`.

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-10-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f5a6b7c8d9e0"
down_revision: Union[str, Sequence[str], None] = "e4f5a6b7c8d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) No perder los días de espera que solo estuvieran en una tarjeta.
    op.execute("""
        UPDATE company c SET settlement_buffer_days = b.wait_days
        FROM bookcriterion b
        WHERE b.company_id = c.id AND b.kind = 'settlement'
          AND b.wait_days IS NOT NULL AND c.settlement_buffer_days IS NULL
    """)
    # 2) Fuera los criterios del libro.
    op.drop_index("ix_bookcriterion_company_id", table_name="bookcriterion")
    op.drop_table("bookcriterion")
    # 3) Fuera los umbrales por monto de la empresa.
    op.drop_column("company", "supermarket_threshold")
    op.drop_column("company", "gas_station_threshold")


def downgrade() -> None:
    op.add_column("company", sa.Column("gas_station_threshold", sa.Numeric(14, 2), nullable=True))
    op.add_column("company", sa.Column("supermarket_threshold", sa.Numeric(14, 2), nullable=True))
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
    # Nota: los VALORES que tenían las columnas y la tabla no vuelven (eran datos de desarrollo).
