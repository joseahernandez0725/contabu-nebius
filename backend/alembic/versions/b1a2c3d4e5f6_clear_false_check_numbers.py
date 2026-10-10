"""clear false transaction.check_number (FASE 10 — "CHK" no es un cheque)

Datos, no esquema. Hasta el 8-oct `rules.check_number` sacaba un "número de cheque" de cualquier
descripción con CHECK / CHK en cualquier lugar: "Online Transfer to CHK ...2930" (Chase / Bank of America:
CHK es la cuenta de origen) quedaba con `check_number = "2930"`. Ahora un cheque es solo una descripción
que EMPIEZA con CHECK / CHEQUE. Esto borra los números que no cumplen eso.

Lo escrito a mano NO se toca (`source = 'manual'`): es la regla del proyecto. Irreversible a propósito:
`downgrade` no vuelve a inventar números falsos.

Revision ID: b1a2c3d4e5f6
Revises: 3d4e5f6a7b8c
Create Date: 2026-10-08

"""
from typing import Sequence, Union

from alembic import op


revision: str = "b1a2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "3d4e5f6a7b8c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # `~*` = expresión regular sin distinguir mayúsculas (Postgres). Misma regla que `rules._CHECK_RE`.
    op.execute("""
        UPDATE transaction SET check_number = NULL
        WHERE check_number IS NOT NULL
          AND COALESCE(source, '') <> 'manual'
          AND description !~* '^\\s*(check|cheque)([^a-z0-9_]|$)'
    """)


def downgrade() -> None:
    pass  # no se vuelven a inventar números de cheque
