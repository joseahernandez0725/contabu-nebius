"""
ContaBu — días de espera del libro (FASE 13, área `Firm / bookkeepers`).

Lo que queda de los "criterios del libro" (1-oct-2026). Jose decidió eliminar los ajustes por monto
(supermercado, gasolinera, tarjetas propias): lo que el motor no decida con una REGLA queda Pendiente,
y el criterio del bookkeeper vive en las notas de la pestaña Auditoría (F11). La tabla `bookcriterion`
se borró en la migración `f5a6b7c8d9e0`.

Solo se conserva `settlement_days()`, con su firma EXACTA, porque el motor de subida la llama
(`ingestion.py:368`, área Movimientos). Si Movimientos prefiere, puede mudarla a `ingestion.py` y
borrar este módulo (aviso en el 📬 Buzón del tablero, 2-oct).
"""

from sqlmodel import Session

from models import Company

# Días que se espera, si el libro no eligió otros (Ajustes del libro → Movimientos recientes).
_START_WAIT_DAYS = 5


def settlement_days(session: Session, company: Company) -> int:
    """Días a esperar antes de registrar lo reciente, por si el banco aún lo está procesando.

    Es un ajuste simple del libro: `Company.settlement_buffer_days`, editable en Ajustes del libro
    (`PATCH /api/companies/{id}/settlement`). `session` no se usa, pero se conserva en la firma para
    no obligar a cambiar a quien la llama.
    """
    return company.settlement_buffer_days if company.settlement_buffer_days is not None else _START_WAIT_DAYS
