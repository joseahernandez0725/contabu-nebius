"""
ContaBu — motor de reglas de categorización.

ANTES de llamar a la IA se consultan las `CategoryRule`: si un comercio/patrón ya tiene
regla (de la empresa o del sector), se aplica directo. Esto da:
- **consistencia** (mismo comercio → misma categoría siempre),
- **velocidad** y **ahorro** de llamadas a la IA,
- y construye el **moat** (las reglas crecen con las correcciones del usuario).

Prioridad: reglas **COMPANY** (privadas de la empresa) sobre **SECTOR** (genéricas);
a igualdad, gana la de `match_text` más específico (más largo).
"""

import re
from decimal import Decimal
from typing import Optional

from sqlalchemy import or_
from sqlmodel import Session, select

from merchants import GAS_STATIONS, SUPERMARKETS
from models import AccountType, CategoryRule, RuleScope

# Umbrales por defecto si la empresa no fijó los suyos (editables por empresa, ver Company).
DEFAULT_SUPERMARKET_THRESHOLD = Decimal("50")
DEFAULT_GAS_STATION_THRESHOLD = Decimal("75")


def normalize(text: str) -> str:
    """Normaliza una descripción para comparar: MAYÚSCULAS y solo alfanuméricos.

    'HOME DEPOT #2745 BUILDING' y 'HomeDepot.com' → 'HOMEDEPOT2745BUILDING' /
    'HOMEDEPOTCOM' (ambas contienen 'HOMEDEPOT'), así un match por subcadena funciona
    pese al ruido del extracto bancario.
    """
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def load_rules(session: Session, company_id: int, sector: str) -> list[CategoryRule]:
    """Carga (en UNA query) las reglas aplicables, ya ordenadas por prioridad.

    Solo devuelve reglas con `account_type` definido (necesario para clasificar el P&L)
    y `match_text` no vacío. Orden: COMPANY antes que SECTOR; dentro de cada grupo, el
    `match_text` más largo primero (más específico gana).
    """
    rules = session.exec(
        select(CategoryRule).where(
            or_(
                (CategoryRule.scope == RuleScope.COMPANY)
                & (CategoryRule.company_id == company_id),
                (CategoryRule.scope == RuleScope.SECTOR)
                & (CategoryRule.sector == sector),
            )
        )
    ).all()
    usable = [r for r in rules if r.account_type is not None and normalize(r.match_text)]
    usable.sort(
        key=lambda r: (0 if r.scope == RuleScope.COMPANY else 1, -len(normalize(r.match_text)))
    )
    return usable


def match_rule(rules: list[CategoryRule], description: str) -> CategoryRule | None:
    """Primera regla (de la lista ya ordenada por prioridad) cuyo `match_text` aparece
    en la descripción normalizada. `None` si ninguna aplica."""
    norm_desc = normalize(description)
    if not norm_desc:
        return None
    for rule in rules:
        if normalize(rule.match_text) in norm_desc:
            return rule
    return None


def find_matching_rule(
    session: Session, company_id: int, sector: str, description: str
) -> CategoryRule | None:
    """Conveniencia para un solo lookup: carga las reglas y matchea una descripción."""
    return match_rule(load_rules(session, company_id, sector), description)


def classify_merchant(description: str) -> Optional[str]:
    """¿La descripción es de una gasolinera o un supermercado? → "gas_station" | "supermarket" | None.

    Se evalúa GASOLINERA primero (ej. "COSTCO GAS" es gasolinera, no supermercado)."""
    norm = normalize(description)
    if not norm:
        return None
    if any(normalize(m) in norm for m in GAS_STATIONS):
        return "gas_station"
    if any(normalize(m) in norm for m in SUPERMARKETS):
        return "supermarket"
    return None


def amount_band_category(
    description: str,
    amount: float,
    supermarket_threshold: Optional[Decimal] = None,
    gas_station_threshold: Optional[Decimal] = None,
) -> Optional[tuple[AccountType, str]]:
    """Desambigua por MONTO en comercios mixtos (super/gasolinera). Determinista, antes de la IA.

    Devuelve `(account_type, category)` o `None` si el comercio no es super/gasolinera.
    `amount` puede venir con signo; se usa su magnitud.

    --- MAPEO (centralizado aquí; ajustar cuando se defina el esquema final) ---
    Supermercado:  < umbral → comida (Meals, OpEx) · ≥ umbral → personal (Owner Draw, NON_PL)
    Gasolinera:    < umbral → maquinaria (Equipment Fuel, COGS) · ≥ umbral → vehículo (Vehicle Fuel, OpEx)
    """
    kind = classify_merchant(description)
    if kind is None:
        return None

    magnitude = abs(Decimal(str(amount)))

    if kind == "supermarket":
        threshold = supermarket_threshold or DEFAULT_SUPERMARKET_THRESHOLD
        if magnitude < threshold:
            return (AccountType.OPERATING_EXPENSE, "Meals")
        return (AccountType.NON_PL, "Owner Draw")

    # gas_station
    threshold = gas_station_threshold or DEFAULT_GAS_STATION_THRESHOLD
    if magnitude < threshold:
        return (AccountType.COGS, "Equipment Fuel")
    return (AccountType.OPERATING_EXPENSE, "Vehicle Fuel")


def upsert_company_rule(
    session: Session,
    company_id: int,
    sector: str,
    match_text: Optional[str],
    account_type: Optional[AccountType],
    category: Optional[str],
) -> Optional[CategoryRule]:
    """Crea o actualiza la regla COMPANY de un comercio (memoria del cliente).

    **Idempotente:** si ya existe una regla COMPANY de esta empresa para el mismo comercio
    (comparando normalizado), la **actualiza** en vez de duplicarla. Devuelve la regla, o `None`
    si faltan datos para formar una regla útil (sin comercio / sin sección / sin categoría).

    Se llama tras `clarify` (la IA re-categoriza) y tras una corrección manual (`PATCH`).
    """
    if not match_text or account_type is None or not category:
        return None

    norm = normalize(match_text)
    if not norm:
        return None

    existing = session.exec(
        select(CategoryRule).where(
            (CategoryRule.scope == RuleScope.COMPANY)
            & (CategoryRule.company_id == company_id)
        )
    ).all()
    for rule in existing:
        if normalize(rule.match_text) == norm:
            rule.account_type = account_type
            rule.category = category
            rule.match_text = match_text  # refresca el texto visible (última versión)
            session.add(rule)
            return rule

    rule = CategoryRule(
        scope=RuleScope.COMPANY,
        company_id=company_id,
        sector=sector,
        match_text=match_text,
        account_type=account_type,
        category=category,
    )
    session.add(rule)
    return rule
