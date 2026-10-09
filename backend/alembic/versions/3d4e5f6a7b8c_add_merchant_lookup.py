"""add merchantlookup + agentcall.credits (FASE 11 — el ② verificador de comercios con Tavily)

`merchantlookup` es la caché del verificador: UNA búsqueda por comercio y POR OFICINA (nada cruza
entre oficinas). Guarda un dato estructurado (tipo de negocio de una lista cerrada, confianza, enlace),
nunca el texto de la página. `agentcall.credits`: los créditos de Tavily que gastó cada búsqueda.

Revision ID: 3d4e5f6a7b8c
Revises: 2c3d4e5f6a7b
Create Date: 2026-10-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "3d4e5f6a7b8c"
down_revision: Union[str, Sequence[str], None] = "2c3d4e5f6a7b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agentcall", sa.Column("credits", sa.Integer(), nullable=False, server_default="0"))
    op.create_table(
        "merchantlookup",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("firm_id", sa.Integer(), nullable=True),
        sa.Column("company_id", sa.Integer(), nullable=True),
        sa.Column("fingerprint", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("query", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("business_type", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("confidence", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("source_url", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("source_domain", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("results_count", sa.Integer(), nullable=False),
        sa.Column("credits", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_merchantlookup_firm_id"), "merchantlookup", ["firm_id"], unique=False)
    op.create_index(op.f("ix_merchantlookup_company_id"), "merchantlookup", ["company_id"], unique=False)
    op.create_index(op.f("ix_merchantlookup_fingerprint"), "merchantlookup", ["fingerprint"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_merchantlookup_fingerprint"), table_name="merchantlookup")
    op.drop_index(op.f("ix_merchantlookup_company_id"), table_name="merchantlookup")
    op.drop_index(op.f("ix_merchantlookup_firm_id"), table_name="merchantlookup")
    op.drop_table("merchantlookup")
    op.drop_column("agentcall", "credits")
