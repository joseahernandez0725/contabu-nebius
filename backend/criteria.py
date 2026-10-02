"""
ContaBu — CRITERIOS DEL LIBRO (FASE 13, área `Firm / bookkeepers`).

Cómo decide ContaBu, POR LIBRO, los casos que se repiten: compras en supermercados, cargos en
gasolineras, tarjetas que crea el bookkeeper ("Home Depot: menos de $500 → Materiales") y cuántos
días esperar los movimientos recientes.

Reemplaza a los umbrales fijos (50 / 30 / 5) y, sobre todo, a las CATEGORÍAS que estaban escritas
en el código (`rules.amount_band_category`): ahí el bookkeeper podía cambiar el número, pero no a
dónde iba cada lado. Aquí elige las dos, del plan de cuentas de ESE libro.

Este módulo NO toca el motor de subida (`ingestion.py`, área Movimientos). Expone
`evaluate_book()` y `settlement_days()` para que el motor los llame en lugar de
`amount_band_category()` y de `company.settlement_buffer_days`. Mientras ese cambio no se haga, los
criterios se usan en la vista previa y al "aplicar a lo ya registrado".

Determinista: sin IA. La Regla de Oro se respeta (la IA no decide ni calcula nada aquí).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from sqlmodel import Session, select

from categories import categories_for_company
from models import AccountType, BookCriterion, Company, Transaction
# Se reutilizan TAL CUAL las piezas del motor, para que una tarjeta reconozca un comercio igual que
# la banda de siempre: mismas listas de gasolineras/supermercados, mismos comercios aprendidos,
# misma coincidencia por palabra completa (`_phrase`: "ARCO" no matchea "MARCOS").
from rules import (GRAY_ZONE_RATIO, _MEMBERSHIP_RE, _phrase, classify_merchant,
                   load_learned_merchants)

KINDS_THAT_CATEGORIZE = ("supermarket", "gas_station", "custom")
MODES = ("split", "always", "pending")

# Valores con los que arranca un libro que todavía no tiene criterios. Son los de siempre, pero
# ahora VISIBLES y editables por libro — ya no un valor escondido en el código.
_START_SUPERMARKET = Decimal("50")
_START_GAS = Decimal("30")
_START_WAIT_DAYS = 5

# Solo estas fuentes las puede reescribir "aplicar a lo ya registrado". NUNCA: "manual" (lo corrigió
# el bookkeeper a mano), "rule" (lo decidió una regla exacta, que manda sobre los criterios) ni
# "transfer" (transferencia entre cuentas propias).
_REWRITABLE_SOURCES = (None, "band", "ai")


@dataclass
class Decision:
    """Lo que decide un criterio sobre UN movimiento. `pending=True` → dejarlo Pendiente."""
    account_type: Optional[AccountType] = None
    category: Optional[str] = None
    near: bool = False
    pending: bool = False
    side: Optional[str] = None  # "below" | "above" | "always" — para la vista previa


# ── Arranque de un libro ────────────────────────────────────────────────────────────────────────

def _starting_criteria(company: Company) -> list[BookCriterion]:
    """Los criterios con que nace un libro, según su sector — y conservando lo que el libro ya tenía
    en las columnas viejas (`supermarket_threshold`, etc.), para no perder nada al pasar a tarjetas."""
    from categories import categories_for  # local: igual que lo hace rules.py
    cogs = categories_for(company.sector or "").get("COGS", [])

    sup = BookCriterion(
        company_id=company.id, kind="supermarket", mode="split", position=0,
        limit_amount=company.supermarket_threshold or _START_SUPERMARKET,
        below_account_type=AccountType.OPERATING_EXPENSE, below_category="Meals",
        above_account_type=AccountType.NON_PL, above_category="Owner Draw",
    )
    if "Fuel" in cogs:
        # Transporte: el diésel ES el costo directo del flete — sin división por monto.
        gas = BookCriterion(company_id=company.id, kind="gas_station", mode="always", position=1,
                            below_account_type=AccountType.COGS, below_category="Fuel")
    elif "Equipment Fuel" not in cogs:
        # Sectores sin maquinaria: el lado "bajo" no puede ser maquinaria → todo es del vehículo.
        gas = BookCriterion(company_id=company.id, kind="gas_station", mode="always", position=1,
                            below_account_type=AccountType.OPERATING_EXPENSE, below_category="Vehicle Fuel")
    else:
        gas = BookCriterion(
            company_id=company.id, kind="gas_station", mode="split", position=1,
            limit_amount=company.gas_station_threshold or _START_GAS,
            below_account_type=AccountType.COGS, below_category="Equipment Fuel",
            above_account_type=AccountType.OPERATING_EXPENSE, above_category="Vehicle Fuel",
        )
    wait = BookCriterion(
        company_id=company.id, kind="settlement", mode="split", position=99,
        wait_days=company.settlement_buffer_days if company.settlement_buffer_days is not None
        else _START_WAIT_DAYS,
    )
    return [sup, gas, wait]


def ensure_criteria(session: Session, company: Company) -> list[BookCriterion]:
    """Los criterios del libro, en orden. Si el libro aún no tiene, los crea (una sola vez)."""
    rows = session.exec(
        select(BookCriterion).where(BookCriterion.company_id == company.id)
        .order_by(BookCriterion.position, BookCriterion.id)
    ).all()
    if rows:
        return list(rows)
    for c in _starting_criteria(company):
        session.add(c)
    session.commit()
    return ensure_criteria(session, company)


def settlement_days(session: Session, company: Company) -> int:
    """Días a esperar antes de registrar lo reciente (para el motor de subida).

    🔄 1-oct (decisión de Jose: fuera los ajustes por monto). Los días de espera NO son un monto y se
    quedan, pero vuelven a ser un ajuste simple del libro: `Company.settlement_buffer_days`, que se
    edita en Ajustes del libro (`PATCH /api/companies/{id}/settlement`). La firma de esta función no
    cambia a propósito: el motor (`ingestion.py`, área Movimientos) la sigue llamando igual.
    """
    return company.settlement_buffer_days if company.settlement_buffer_days is not None else _START_WAIT_DAYS


# ── Validación ──────────────────────────────────────────────────────────────────────────────────

def chart_has(session: Session, company: Company, account_type: Optional[AccountType],
              category: Optional[str]) -> bool:
    """¿La categoría existe en el plan de cuentas de ESTE libro, en esa sección?"""
    if account_type is None or not category:
        return False
    chart = categories_for_company(session, company.id, company.sector or "")
    return category in chart.get(account_type.value, [])


def validation_error(session: Session, company: Company, c: BookCriterion) -> Optional[str]:
    """Texto del problema (en inglés, lo traduce el frontend por código) o None si está bien."""
    if c.kind == "settlement":
        return None if (c.wait_days is not None and 0 <= c.wait_days <= 30) else "wait_days_range"
    if c.mode not in MODES:
        return "bad_mode"
    if c.kind == "custom" and not [k for k in (c.keywords or []) if str(k).strip()]:
        return "keywords_required"
    if c.mode == "pending":
        return None
    if not chart_has(session, company, c.below_account_type, c.below_category):
        return "category_not_in_chart"
    if c.mode == "split":
        if c.limit_amount is None or c.limit_amount <= 0:
            return "limit_required"
        if not chart_has(session, company, c.above_account_type, c.above_category):
            return "category_not_in_chart"
    return None


# ── Decidir ─────────────────────────────────────────────────────────────────────────────────────

def _matches(c: BookCriterion, description: str, extra_gas: list[str], extra_super: list[str]) -> bool:
    if c.kind in ("supermarket", "gas_station"):
        # Una cuota o renovación ("COSTCO *ANNUAL RENEWAL") no es comida ni combustible.
        if _MEMBERSHIP_RE.search(description or ""):
            return False
        return classify_merchant(description, extra_gas, extra_super) == c.kind
    if c.kind == "custom":
        phrase = f" {_phrase(description)} "
        return any(f" {_phrase(str(k))} " in phrase for k in (c.keywords or []) if str(k).strip())
    return False


def decide(c: BookCriterion, amount: Decimal) -> Decision:
    """Lo que dice el criterio para un monto (ya se sabe que el movimiento le corresponde)."""
    if c.mode == "pending":
        return Decision(pending=True, side="pending")
    if c.mode == "always":
        return Decision(c.below_account_type, c.below_category, side="always")
    magnitude = abs(Decimal(str(amount)))
    limit = c.limit_amount or Decimal("0")
    below = magnitude < limit
    near = bool(c.review_near and limit > 0 and abs(magnitude - limit) <= limit * GRAY_ZONE_RATIO)
    if below:
        return Decision(c.below_account_type, c.below_category, near=near, side="below")
    return Decision(c.above_account_type, c.above_category, near=near, side="above")


def evaluate_book(description: str, amount, criteria: list[BookCriterion],
                  extra_gas: list[str], extra_super: list[str]
                  ) -> Optional[tuple[BookCriterion, Decision]]:
    """El PRIMER criterio del libro que le corresponde al movimiento, y lo que decide.

    Es la función que el motor de subida llama en lugar de `amount_band_category()`. Las reglas
    exactas (`CategoryRule`) se evalúan ANTES y mandan sobre los criterios. `None` = ningún criterio
    aplica (el motor sigue con transferencias/IA/Pendiente como siempre).
    """
    for c in criteria:
        if c.kind not in KINDS_THAT_CATEGORIZE:
            continue
        if _matches(c, description, extra_gas, extra_super):
            return c, decide(c, amount)
    return None


def review_question(c: BookCriterion, d: Decision) -> Optional[str]:
    """Pregunta para la zona gris (en inglés: dato contable interno)."""
    if not d.near or c.mode != "split":
        return None
    return f"Is this {c.below_category} or {c.above_category}?"


def write_decision(tx: Transaction, c: BookCriterion, d: Decision) -> None:
    """Escribe la decisión en la transacción IGUAL que el motor escribe una banda de monto
    (`ingestion._apply_categorization`): misma fuente, misma confianza, misma zona gris."""
    if d.pending:
        tx.account_type = None
        tx.category = None
        tx.source = None
        tx.confidence = None
        tx.needs_review = False
        tx.review_question = None
        return
    tx.account_type = d.account_type
    tx.category = d.category
    tx.ai_categorized = False
    tx.source = "band"           # procedencia: "criterio del libro" (el valor se conserva por compatibilidad)
    tx.matched_rule_id = None
    if d.near:
        tx.confidence = 0.5
        tx.needs_review = True
        tx.review_question = review_question(c, d)
    else:
        tx.confidence = 0.85
        tx.needs_review = False
        tx.review_question = None


# ── Vista previa y aplicar a lo ya registrado ───────────────────────────────────────────────────

def _book_transactions(session: Session, company_id: int) -> list[Transaction]:
    return list(session.exec(select(Transaction).where(Transaction.company_id == company_id)).all())


def preview(session: Session, company: Company, c: BookCriterion) -> dict:
    """Qué haría este criterio con los movimientos que YA están en el libro.

    Cuenta todo lo que le corresponde, separa lo que cambiaría de lo PROTEGIDO (corregido a mano o
    decidido por una regla exacta: eso no se toca) y devuelve unos ejemplos. Sin IA.
    """
    extra_gas, extra_super = load_learned_merchants(session, company.id, company.sector or "")
    out = {"matched": 0, "below": 0, "above": 0, "always": 0, "pending": 0, "review": 0,
           "protected": 0, "would_change": 0, "examples": []}
    if c.kind not in KINDS_THAT_CATEGORIZE:
        return out
    for tx in _book_transactions(session, company.id):
        desc = f"{tx.description} {tx.description_extra or ''}".strip()
        if not _matches(c, desc, extra_gas, extra_super):
            continue
        d = decide(c, tx.amount)
        out["matched"] += 1
        out[d.side or "always"] += 1
        if d.near:
            out["review"] += 1
        if tx.source not in _REWRITABLE_SOURCES:
            out["protected"] += 1
            continue
        if (d.pending and tx.category is not None) or (
            not d.pending and (tx.category != d.category or tx.account_type != d.account_type)
        ):
            out["would_change"] += 1
        if len(out["examples"]) < 5:
            out["examples"].append({
                "date": tx.date.isoformat(), "description": tx.description,
                "amount": float(tx.amount), "category": None if d.pending else d.category,
                "near": d.near,
            })
    return out


def apply_to_book(session: Session, company: Company, c: BookCriterion) -> int:
    """Aplica el criterio a lo YA registrado. Nunca toca lo corregido a mano ni lo que decidió una
    regla exacta. Con modo "pendiente" solo devuelve a Pendiente lo que había decidido un criterio
    (no borra lo que categorizó la IA). Devuelve cuántas transacciones cambiaron."""
    if c.kind not in KINDS_THAT_CATEGORIZE:
        return 0
    extra_gas, extra_super = load_learned_merchants(session, company.id, company.sector or "")
    changed = 0
    for tx in _book_transactions(session, company.id):
        if tx.source not in _REWRITABLE_SOURCES:
            continue
        desc = f"{tx.description} {tx.description_extra or ''}".strip()
        if not _matches(c, desc, extra_gas, extra_super):
            continue
        d = decide(c, tx.amount)
        if d.pending and tx.source != "band":
            continue
        before = (tx.account_type, tx.category, tx.needs_review)
        write_decision(tx, c, d)
        if (tx.account_type, tx.category, tx.needs_review) != before:
            changed += 1
            session.add(tx)
    session.commit()
    return changed
