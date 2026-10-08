"""
ContaBu — LA FÓRMULA DEL LIBRO: el formato de una fila y su VALIDADOR (FASE 11, Auditoría / IA ·
4-oct-2026). Ver `docs/FASE 11.md` → "🔄 Actualización (3-oct) — La fórmula del libro".

Cada libro tiene su fórmula de categorización: la hoja de reglas, leída como una cadena de "SI" con
condiciones (palabra clave, ventana de fechas, prioridad). El agente ④ (redactor) solo puede producir
FILAS de esa fórmula. Este módulo es la puerta por la que tiene que pasar cada fila antes de que el
bookkeeper la vea.

🔴 SEGURIDAD — NO NEGOCIABLE (Jose, 3-oct):
- El agente solo produce FILAS DE LA FÓRMULA, validadas contra un esquema: campos permitidos,
  operadores permitidos y categorías del plan de cuentas. Nunca código.
- El motor INTERPRETA esas filas y JAMÁS ejecuta texto: ningún eval, ningún exec. Aquí tampoco: una
  fila es un dict de datos que se compara campo por campo; el texto de la palabra clave solo se usa
  como patrón en `rules.match_rule` (búsqueda de palabras), nunca se evalúa.
- Una fila que no cumple se DESCARTA y se cuenta (el porcentaje de rechazo es parte de la medición).
  No se "arregla" en silencio.

Condiciones que el motor ya entiende (y por eso las únicas permitidas hoy): palabra clave (con `*`),
ventana de fechas (`valid_from`/`valid_to`), prioridad. Monto, cuenta y entrada/salida NO se aceptan
todavía: el motor las ignoraría y una fila aprobada valdría para más movimientos de los que dice.
Se agregan cuando Movimientos las construya en el motor (📬 Buzón, 4-oct).

Determinista, sin IA. La simulación usa las MISMAS funciones del motor (`rules.match_rule` con la
fecha, `rules.classify_transfer`): no hay una segunda lógica de categorización.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlmodel import Session, select

from categories import categories_for_company
from models import AccountType, BookNote, CategorizationChange, CategoryRule, Company, RuleScope, Transaction
from rules import (_MERCHANT_NOISE, classify_transfer, load_rules, match_rule, matchable_text,
                   normalize, rule_order)

# La palabra clave es un PATRÓN de palabras, no un programa: solo letras, números, espacio y los
# signos que de verdad aparecen en los nombres de comercio del banco (PG&E, LOWE'S, AMAZON.COM, #12)
# más `*` (el comodín del motor). Cualquier otro carácter — ( ) ; = < > { } $ ` \ " _ … — es rechazo:
# así tampoco pasa un marcador de persona sin resolver (`PERSON_1`).
_KEYWORD_RE = re.compile(r"^[A-Z0-9 &'.\-/#*]+$")
_SUBCATEGORY_RE = re.compile(r"^[A-Za-z0-9 &'.\-/#]+$")
_FIRST_DAY, _LAST_DAY = date(2000, 1, 1), date(2100, 12, 31)
MAX_EXPLANATION = 600


class FormulaRow(BaseModel):
    """UNA fila de la fórmula, tal como la puede proponer el agente ④. `extra="forbid"`: un campo que
    no está aquí es rechazo (no se ignora)."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # ── Condiciones (lo que el motor interpreta) ──
    keyword: str = Field(min_length=2, max_length=60)        # → CategoryRule.match_text
    valid_from: Optional[date] = None                        # ventana INCLUSIVA (rules.in_window)
    valid_to: Optional[date] = None
    priority: Optional[int] = Field(default=None, ge=1, le=999)
    # ── Resultado ──
    account_type: AccountType
    category: str = Field(min_length=1, max_length=80)
    subcategory: Optional[str] = Field(default=None, max_length=60)
    # ── Lo que ve el bookkeeper (el motor jamás lo lee) ──
    explanation: str = Field(default="", max_length=MAX_EXPLANATION)
    from_notes: list[int] = Field(default_factory=list, max_length=20)     # evidencia: notas
    from_changes: list[int] = Field(default_factory=list, max_length=50)   # evidencia: correcciones


@dataclass
class Checked:
    """Resultado de validar una fila: `row` si pasó; si no, `errors` (códigos estables en inglés)."""
    raw: dict
    row: Optional[FormulaRow] = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    matches: int = 0   # movimientos del libro que la fila caza (dentro de su ventana)

    @property
    def ok(self) -> bool:
        return self.row is not None and not self.errors


def _schema_errors(e: ValidationError) -> list[str]:
    """Errores de pydantic → códigos estables: `unknown_field:<campo>`, `bad_value:<campo>`."""
    out = []
    for err in e.errors():
        where = ".".join(str(p) for p in err.get("loc", ())) or "row"
        out.append(f"unknown_field:{where}" if err.get("type") == "extra_forbidden" else f"bad_value:{where}")
    return sorted(set(out))


def _keyword_errors(keyword: str) -> list[str]:
    kw = keyword.upper()
    if not _KEYWORD_RE.match(kw):
        return ["keyword_bad_chars"]
    words = [w for w in re.split(r"[^A-Z0-9]+", kw) if w]
    meaningful = [w for w in words if any(c.isalpha() for c in w) and w not in _MERCHANT_NOISE]
    if not normalize(kw) or not meaningful:
        return ["keyword_too_generic"]   # solo números o solo ruido del banco (PAYMENT, ONLINE…)
    return []


class BookContext:
    """Lo que el validador necesita de UN libro, cargado una vez: plan de cuentas, reglas vivas,
    movimientos, ids de notas y de correcciones. Así validar N filas no hace N consultas."""

    def __init__(self, session: Session, company: Company):
        self.company = company
        self.chart = categories_for_company(session, company.id, company.sector or "")
        self.rules = load_rules(session, company.id, company.sector or "")
        self.txs = session.exec(select(Transaction).where(Transaction.company_id == company.id)).all()
        self.note_ids = set(session.exec(select(BookNote.id).where(BookNote.company_id == company.id)).all())
        self.change_ids = set(session.exec(
            select(CategorizationChange.id).where(CategorizationChange.company_id == company.id)).all())


def check_row(raw: dict, ctx: BookContext) -> Checked:
    """Valida UNA fila cruda (lo que devolvió el modelo) contra el esquema y contra ESTE libro."""
    result = Checked(raw=raw)
    if not isinstance(raw, dict):
        result.errors = ["not_an_object"]
        return result
    try:
        row = FormulaRow.model_validate(raw)
    except ValidationError as e:
        result.errors = _schema_errors(e)
        return result

    errors = _keyword_errors(row.keyword)
    at = row.account_type.value
    if row.category not in ctx.chart.get(at, []):
        in_other = any(row.category in cats for sec, cats in ctx.chart.items() if sec != at)
        errors.append("category_wrong_section" if in_other else "category_not_in_chart")
    if row.subcategory and not _SUBCATEGORY_RE.match(row.subcategory):
        errors.append("subcategory_bad_chars")
    for d in (row.valid_from, row.valid_to):
        if d is not None and not (_FIRST_DAY <= d <= _LAST_DAY):
            errors.append("date_out_of_range")
            break
    if row.valid_from and row.valid_to and row.valid_from > row.valid_to:
        errors.append("bad_window")
    if set(row.from_notes) - ctx.note_ids:
        errors.append("unknown_note")
    if set(row.from_changes) - ctx.change_ids:
        errors.append("unknown_change")

    if not errors:
        rule = to_rule(row, ctx.company.id)
        kw = normalize(row.keyword)
        if any(normalize(r.match_text) == kw and r.valid_from == row.valid_from and r.valid_to == row.valid_to
               and r.category == row.category for r in ctx.rules):
            errors.append("duplicate_rule")
        result.matches = sum(
            1 for t in ctx.txs
            if match_rule([rule], matchable_text(t.description, t.description_extra), t.date) is not None)
        if result.matches == 0:
            errors.append("matches_nothing")   # una fila muerta no se le muestra al bookkeeper

    result.errors = errors
    result.row = row if not errors else None
    return result


def check_rows(raws: list, ctx: BookContext) -> list[Checked]:
    if not isinstance(raws, list):
        return [Checked(raw={"value": raws}, errors=["not_a_list"])]
    return [check_row(r, ctx) for r in raws]


def to_rule(row: FormulaRow, company_id: int) -> CategoryRule:
    """La fila validada → una `CategoryRule` SIN GUARDAR, idéntica a la que crearía el bookkeeper.
    Es lo que el motor interpreta: el mismo objeto sirve para simular y, si se aprueba, para guardar."""
    return CategoryRule(
        scope=RuleScope.COMPANY, company_id=company_id, match_text=row.keyword.upper(),
        account_type=row.account_type, category=row.category,
        subcategory=row.subcategory, priority=row.priority,
        valid_from=row.valid_from, valid_to=row.valid_to,
    )


# ── Simulación con el motor (sin escribir nada) ─────────────────────────────────────────────────

@dataclass(frozen=True)
class Decision:
    """Lo que el motor decidiría para un movimiento. `source` = "rule" | "transfer" | None (Pendiente)."""
    account_type: Optional[str]
    category: Optional[str]
    source: Optional[str]


def decide(rules_sorted: list[CategoryRule], tx: Transaction) -> Decision:
    """Precedencia del motor (`ingestion._apply_categorization`): regla (con su ventana de fechas) →
    transferencia → Pendiente. Ignora `source="manual"` a propósito: la medición pregunta qué habría
    decidido el motor ANTES de que el bookkeeper corrigiera a mano."""
    rule = match_rule(rules_sorted, matchable_text(tx.description, tx.description_extra), tx.date)
    if rule is not None:
        return Decision(rule.account_type.value if rule.account_type else None, rule.category, "rule")
    transfer = classify_transfer(tx.description)
    if transfer is not None:
        return Decision(transfer[0].value, transfer[1], "transfer")
    return Decision(None, None, None)


def with_rows(base_rules: list[CategoryRule], rows: list[FormulaRow], company_id: int) -> list[CategoryRule]:
    """Las reglas vivas + las filas propuestas, en el ORDEN del motor (`rules.rule_order`)."""
    return sorted(list(base_rules) + [to_rule(r, company_id) for r in rows], key=rule_order)
