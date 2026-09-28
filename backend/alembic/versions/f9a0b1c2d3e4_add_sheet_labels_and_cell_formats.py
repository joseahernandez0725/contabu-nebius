"""add sheet labels and cell formats (FASE 10 — la hoja)

Dos cosas distintas a propósito:
  - `sheetlabel` / `transactionlabel`: marcas CON SIGNIFICADO que el bookkeeper define
    ("falta el recibo"). Son dato: se filtran y la IA las puede leer.
  - `cellformat`: formato visual libre (negrilla, colores, tipo y tamaño de letra). Es
    presentación: no entra al P&L ni lo lee la IA.

Revision ID: f9a0b1c2d3e4
Revises: e8f9a0b1c2d3
"""
from alembic import op
import sqlalchemy as sa
import sqlmodel

revision = "f9a0b1c2d3e4"
down_revision = "e8f9a0b1c2d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sheetlabel",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("color", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_sheetlabel_company_id"), "sheetlabel", ["company_id"])

    op.create_table(
        "transactionlabel",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("label_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["transaction_id"], ["transaction.id"]),
        sa.ForeignKeyConstraint(["label_id"], ["sheetlabel.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_transactionlabel_transaction_id"), "transactionlabel", ["transaction_id"])
    op.create_index(op.f("ix_transactionlabel_label_id"), "transactionlabel", ["label_id"])

    op.create_table(
        "cellformat",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        # `col`, no `column`: "column" es palabra reservada en SQL.
        sa.Column("col", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("bold", sa.Boolean(), nullable=False),
        sa.Column("italic", sa.Boolean(), nullable=False),
        sa.Column("bg", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("fg", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("font_size", sa.Integer(), nullable=True),
        sa.Column("font_family", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["transaction_id"], ["transaction.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_cellformat_transaction_id"), "cellformat", ["transaction_id"])
    op.create_index(op.f("ix_cellformat_col"), "cellformat", ["col"])
    # Una sola fila de formato por (transacción, columna): aplicar formato es un UPSERT.
    op.create_unique_constraint("uq_cellformat_tx_col", "cellformat", ["transaction_id", "col"])


def downgrade() -> None:
    op.drop_constraint("uq_cellformat_tx_col", "cellformat", type_="unique")
    op.drop_table("cellformat")
    op.drop_table("transactionlabel")
    op.drop_table("sheetlabel")
