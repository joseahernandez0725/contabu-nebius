"""
ContaBu — HISTORIAL DE CORRECCIONES (FASE 10, área Movimientos · 2-oct-2026).

Cada vez que una transacción YA registrada cambia de categorización, queda una fila
`CategorizationChange` con el par ANTES → DESPUÉS. Es la materia prima del bucle que planea Jose:
él opera el libro, corrige, y los agentes aprenden de esas correcciones.

Uso (siempre en dos tiempos, para no olvidar el "antes"):

    before = snapshot(tx)
    ... se cambia tx ...
    record_change(session, tx, before, via="manual", user_id=user.id)

`record_change` no escribe nada si no cambió `account_type`, `category` ni `subcategory` (estampar
solo la procedencia, p. ej. `matched_rule_id`, no es una corrección). No hace commit: lo hace quien
llama, junto con el cambio de la transacción.
"""

from typing import Optional

from sqlmodel import Session

from models import CategorizationChange, Transaction

# Códigos de `via` (estables, en inglés: los leen métricas y agentes).
VIA_MANUAL = "manual"                # el bookkeeper editó la celda
VIA_FOLLOW_RULE = "follow_rule"      # "volver a seguir la regla" (la celda vuelve a ser fórmula)
VIA_RULE_UPDATE = "rule_update"      # corrigió la celda Y la regla que la cubría
VIA_RULE_APPLIED = "rule_applied"    # una regla creada/editada alineó esta transacción
VIA_RULE_PRECEDENCE = "rule_precedence"  # cambió qué regla gana (p. ej. una prioridad)

_FIELDS = ("account_type", "category", "subcategory")


def _val(v) -> Optional[str]:
    return getattr(v, "value", v)  # AccountType → "COGS"; str/None quedan igual


def snapshot(tx: Transaction) -> dict:
    """Foto de la categorización actual de `tx` (tomarla ANTES de cambiarla)."""
    return {
        "source": tx.source,
        "account_type": _val(tx.account_type),
        "category": tx.category,
        "subcategory": tx.subcategory,
        "matched_rule_id": tx.matched_rule_id,
    }


def record_change(session: Session, tx: Transaction, before: dict, *, via: str,
                  user_id: Optional[int] = None,
                  reason: Optional[str] = None) -> Optional[CategorizationChange]:
    """Agrega a la sesión la fila ANTES → DESPUÉS si la categorización cambió de verdad."""
    after = snapshot(tx)
    if all(before[f] == after[f] for f in _FIELDS):
        return None
    row = CategorizationChange(
        transaction_id=tx.id, company_id=tx.company_id, changed_by_user_id=user_id, via=via,
        source_before=before["source"], source_after=after["source"],
        account_type_before=before["account_type"], account_type_after=after["account_type"],
        category_before=before["category"], category_after=after["category"],
        subcategory_before=before["subcategory"], subcategory_after=after["subcategory"],
        matched_rule_id_before=before["matched_rule_id"],
        matched_rule_id_after=after["matched_rule_id"],
        reason=(reason or "").strip() or None,
    )
    session.add(row)
    return row
