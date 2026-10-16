"""add bankconnection.institution_id (FASE 10 — no vincular dos veces el mismo banco)

El id de la institución de Plaid (`ins_…`, viene en el `metadata` de Link). Con él y las máscaras de las
cuentas se detecta un banco que ya está vinculado ANTES de canjear el public_token: en el plan Trial de
Plaid cada conexión cuenta y no se recupera. Nulo en las conexiones anteriores (se comparan por nombre).

Revision ID: 8d9e0f1a2b3c
Revises: 7c8d9e0f1a2b
Create Date: 2026-10-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "8d9e0f1a2b3c"
down_revision: Union[str, Sequence[str], None] = "7c8d9e0f1a2b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("bankconnection", sa.Column("institution_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
    op.drop_column("bankconnection", "institution_id")
