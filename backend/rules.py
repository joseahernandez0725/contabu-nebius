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
# Gasolinera $30: bajo eso suele ser combustible de equipos o compras pequeñas en la tienda;
# por encima, llenar el tanque del vehículo (auto expense).
DEFAULT_SUPERMARKET_THRESHOLD = Decimal("50")
DEFAULT_GAS_STATION_THRESHOLD = Decimal("30")


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


def _phrase(text: str) -> str:
    """Texto → PALABRAS en mayúsculas separadas por un solo espacio.
    'HOME DEPOT #2745' → 'HOME DEPOT 2745'. Sirve para match por **límite de palabra**."""
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9]+", " ", text.upper())).strip()


def match_rule(rules: list[CategoryRule], description: str) -> CategoryRule | None:
    """Primera regla (de la lista ya ordenada por prioridad) cuyo `match_text` aparece como
    **palabra/frase completa** en la descripción. `None` si ninguna aplica.

    Match por límite de palabra (no subcadena suelta) → reglas **específicas, sin conflicto**:
    "Shell" matchea "SHELL OIL 4471" pero NO "MICHELLE"; "Home Depot" matchea "HOME DEPOT 1234"."""
    desc = f" {_phrase(description)} "
    if desc == "  ":
        return None
    for rule in rules:
        token = _phrase(rule.match_text)
        if token and f" {token} " in desc:
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


# Palabras genéricas que no aportan especificidad (no deben quedar como regla por sí solas).
_STOPWORDS = {
    "THE", "AND", "INC", "LLC", "CO", "CORP", "LTD", "PAYMENT", "POS", "DEBIT", "CREDIT",
    "PURCHASE", "ONLINE", "TRANSFER", "FROM", "TO", "OF", "FOR", "ORIG", "DESC", "ID", "CO",
}


def specific_phrase(description: Optional[str], merchant: Optional[str]) -> str:
    """Devuelve un `match_text` **específico**: una frase de ~2 palabras cuando sea posible,
    para que el lookup no cree interferencias.

    Prioriza el comercio (subcategory); si trae **una sola** palabra, intenta unirle una palabra
    significativa contigua de la descripción → frase compuesta. Sin comercio: 2 palabras
    significativas de la descripción. Devuelve MAYÚSCULAS (espejo de la subcategoría)."""
    mwords = _phrase(merchant or "").split()
    dwords = _phrase(description or "").split()

    if len(mwords) >= 2:
        return " ".join(mwords[:2])

    if len(mwords) == 1:
        w = mwords[0]
        if w in dwords:
            i = dwords.index(w)
            for nxt in dwords[i + 1:]:
                if not nxt.isdigit() and len(nxt) >= 2 and nxt not in _STOPWORDS:
                    return f"{w} {nxt}"
        return w  # comercio de una palabra sin vecino útil → se queda solo

    # Sin comercio: primeras 2 palabras significativas de la descripción.
    sig = [w for w in dwords if not w.isdigit() and len(w) >= 2 and w not in _STOPWORDS]
    if len(sig) >= 2:
        return " ".join(sig[:2])
    return sig[0] if sig else (dwords[0] if dwords else "")


def upsert_company_rule(
    session: Session,
    company_id: int,
    sector: str,
    match_text: Optional[str],
    account_type: Optional[AccountType],
    category: Optional[str],
    description: Optional[str] = None,
) -> Optional[CategoryRule]:
    """Crea o actualiza la regla COMPANY de un comercio (memoria del cliente).

    El `match_text` se hace **específico** (frase compuesta de ~2 palabras vía `specific_phrase`)
    para no crear interferencias. **Idempotente:** si ya existe una regla con la misma frase, la
    actualiza en vez de duplicarla. Devuelve la regla o `None` si faltan datos.

    Se llama tras `clarify` (la IA re-categoriza) y tras una corrección manual (`PATCH`).
    """
    if account_type is None or not category:
        return None

    phrase = specific_phrase(description, match_text) if description is not None else _phrase(match_text or "")
    if not phrase:
        return None

    existing = session.exec(
        select(CategoryRule).where(
            (CategoryRule.scope == RuleScope.COMPANY)
            & (CategoryRule.company_id == company_id)
        )
    ).all()
    for rule in existing:
        if _phrase(rule.match_text) == phrase:
            rule.account_type = account_type
            rule.category = category
            rule.match_text = phrase
            session.add(rule)
            return rule

    rule = CategoryRule(
        scope=RuleScope.COMPANY,
        company_id=company_id,
        sector=sector,
        match_text=phrase,
        account_type=account_type,
        category=category,
    )
    session.add(rule)
    return rule
