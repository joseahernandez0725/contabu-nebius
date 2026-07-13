"""add subscription_started_at to company (quota month floor, hallazgo 8.6)

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
Create Date: 2026-07-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d7e8f9a0b1c2"
down_revision: Union[str, Sequence[str], None] = "c6d7e8f9a0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Cuándo arrancó la suscripción vigente: el cupo mensual cuenta desde max(mes, este momento),
    # así la prueba gratis (100 con IA) usada antes de pagar NO se come el primer mes pagado.
    op.add_column("company", sa.Column("subscription_started_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("company", "subscription_started_at")
