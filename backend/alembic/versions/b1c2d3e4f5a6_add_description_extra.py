"""add description_extra (FASE 10 — texto del bookkeeper sobre la descripción del banco)

El extracto bancario es la EVIDENCIA: `description` no se toca. Este campo guarda el texto que el
bookkeeper AGREGA (a quién fue el cheque, qué era ese ACH críptico) y entra al matcheo de reglas.

Revision ID: b1c2d3e4f5a6
Revises: a0b1c2d3e4f5
"""
from alembic import op
import sqlalchemy as sa
import sqlmodel

revision = "b1c2d3e4f5a6"
down_revision = "a0b1c2d3e4f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transaction",
        sa.Column("description_extra", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transaction", "description_extra")
