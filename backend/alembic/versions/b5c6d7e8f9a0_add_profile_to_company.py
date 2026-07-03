"""add profile (bookkeeper interview) to company (FASE 9 Paso 5)

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-07-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b5c6d7e8f9a0"
down_revision: Union[str, Sequence[str], None] = "a4b5c6d7e8f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Hechos de la ENTREVISTA del bookkeeper (siempre en inglés). JSON y no columnas sueltas:
    # el cuestionario va a evolucionar y los hechos son un bloque que viaja junto a los prompts.
    op.add_column("company", sa.Column("profile", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("company", "profile")
