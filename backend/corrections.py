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

from sqlmodel import Session, select

from models import AccountType, CategorizationChange, Transaction

# Códigos de `via` (estables, en inglés: los leen métricas y agentes).
VIA_MANUAL = "manual"                # el bookkeeper editó la celda
VIA_FOLLOW_RULE = "follow_rule"      # "volver a seguir la regla" (la celda vuelve a ser fórmula)
VIA_RULE_UPDATE = "rule_update"      # corrigió la celda Y la regla que la cubría
VIA_RULE_APPLIED = "rule_applied"    # una regla creada/editada alineó esta transacción
VIA_RULE_PRECEDENCE = "rule_precedence"  # cambió qué regla gana (p. ej. una prioridad)
VIA_UNDO = "undo"                    # el bookkeeper deshizo un cambio (Ctrl+Z / botón Deshacer)
VIA_BANK_MODIFIED = "bank_modified"  # el banco vinculado cambió el movimiento y el motor lo re-decidió
VIA_RULE_DELETED = "rule_deleted"    # se borró la regla que la decidía: el motor la re-decidió (Jose, 4-oct)
# El PLAN DE CUENTAS cambió (18-oct): la categoría de la fila se renombró (o se fusionó con otra) o se movió
# de sección. No es una corrección de la fila: Deshacer no la toma y el ④ no aprende de ella.
VIA_CHART_RENAME = "chart_rename"
VIA_CHART_MOVE = "chart_move"
CHART_VIAS = (VIA_CHART_RENAME, VIA_CHART_MOVE)

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


def matches_after(tx: Transaction, change: CategorizationChange) -> bool:
    """¿La transacción sigue EXACTAMENTE como la dejó este cambio? Si alguien la tocó después,
    deshacer este cambio pisaría lo posterior: no se hace."""
    now = snapshot(tx)
    return (now["account_type"], now["category"], now["subcategory"], now["source"], now["matched_rule_id"]) == (
        change.account_type_after, change.category_after, change.subcategory_after,
        change.source_after, change.matched_rule_id_after)


def undo_change(session: Session, tx: Transaction, change: CategorizationChange, *,
                user_id: Optional[int] = None) -> bool:
    """Devuelve la transacción al ANTES de `change` (categoría, sección, subcategoría, origen y la
    regla que la decidía) y deja su propia fila en el historial (`via="undo"`). No hace commit."""
    if not matches_after(tx, change):
        return False
    before = snapshot(tx)
    tx.account_type = AccountType(change.account_type_before) if change.account_type_before else None
    tx.category = change.category_before
    tx.subcategory = change.subcategory_before
    tx.source = change.source_before
    tx.matched_rule_id = change.matched_rule_id_before
    session.add(tx)
    record_change(session, tx, before, via=VIA_UNDO, user_id=user_id)
    return True


def last_change(session: Session, tx: Transaction) -> Optional[CategorizationChange]:
    """El último cambio que se puede DESHACER: un cambio del plan de cuentas no cuenta (deshacerlo
    devolvería la fila a un nombre que el plan ya no tiene)."""
    return session.exec(select(CategorizationChange)
                        .where(CategorizationChange.transaction_id == tx.id)
                        .where(CategorizationChange.via.not_in(CHART_VIAS))
                        .order_by(CategorizationChange.id.desc())).first()
