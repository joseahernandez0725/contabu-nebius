"""add finding.agent_observation_en / agent_observation_es (FASE 11 — el ① revisor en los dos idiomas)

El ① revisor escribe su observación en inglés Y en español en la MISMA llamada (como ya hace
`personal_use.py` con `reason_en`/`reason_es`): la pantalla muestra la del idioma actual sin volver a
llamar a la IA. Los hallazgos guardados ANTES de esta migración siguen con una sola columna
(`agent_observation` + `agent_lang`): se ven en el idioma que tengan, con la marca de "otro idioma" que
ya existía, hasta la próxima revisión (que ya llena las dos columnas nuevas).

Revision ID: c3d4e5f6a7b9
Revises: a1f0c2d4e6b8
Create Date: 2026-10-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d4e5f6a7b9"
down_revision: Union[str, Sequence[str], None] = "a1f0c2d4e6b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("finding", sa.Column("agent_observation_en", sa.String(), nullable=True))
    op.add_column("finding", sa.Column("agent_observation_es", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("finding", "agent_observation_es")
    op.drop_column("finding", "agent_observation_en")
