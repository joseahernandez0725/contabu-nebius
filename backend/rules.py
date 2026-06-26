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
from models import AccountType, CategoryRule, LearnedMerchant, RuleScope, Transaction

# Umbrales por defecto si la empresa no fijó los suyos (editables por empresa, ver Company).
# Gasolinera $30: bajo eso suele ser combustible de equipos o compras pequeñas en la tienda;
# por encima, llenar el tanque del vehículo (auto expense).
DEFAULT_SUPERMARKET_THRESHOLD = Decimal("50")
DEFAULT_GAS_STATION_THRESHOLD = Decimal("30")

# Cerca del umbral la decisión binaria de la banda es poco confiable (un $28 vs $32 en gasolinera
# no es obvio): en esa "zona gris" (±25% del umbral) la banda PROPONE una categoría pero marca
# revisión humana en vez de decidir con confianza. Ataca el error silencioso de las bandas.
GRAY_ZONE_RATIO = Decimal("0.25")

# Un CHEQUE es un método de pago, no una contraparte: cada cheque puede ser para algo distinto
# (no se puede agrupar) y su descripción ("CHECK #1234") es única → NO sirve crear una regla.
_CHECK_RE = re.compile(r"\b(check|chk|cheque|e-?check)\b", re.IGNORECASE)

# Cargos que NO son una compra en el comercio sino una cuota/membresía/renovación (ej.
# "COSTCO *ANNUAL RENEWAL"): la banda de monto por supermercado/gasolinera NO aplica (no es ni
# comida ni combustible ni un retiro personal) → dejar que la IA lo categorice (Dues/Office Expense).
_MEMBERSHIP_RE = re.compile(r"\b(membership|annual|renewal|renew|subscription|dues|fee)\b", re.IGNORECASE)


def is_check(description: Optional[str]) -> bool:
    """True si la descripción parece un cheque (no agrupar, no crear regla)."""
    return bool(description and _CHECK_RE.search(description))


# Número de cheque tras la palabra "check" (ej. "CHECK 20169  04/01" → "20169"). Pide dígitos
# inmediatamente después → NO matchea "ATM CHECK DEPOSIT" (un depósito, no un cheque emitido).
_CHECK_NUM_RE = re.compile(r"\b(?:check|chk|cheque|e-?check)\s*#?\s*(\d+)", re.IGNORECASE)


def check_number(description: Optional[str]) -> Optional[str]:
    """Extrae el número de cheque de la descripción; None si no hay. El banco da el número pero
    NO el beneficiario → se guarda para identificar el cheque y preguntar en revisión."""
    if not description:
        return None
    m = _CHECK_NUM_RE.search(description)
    return m.group(1) if m else None


def normalize(text: str) -> str:
    """Normaliza una descripción para comparar: MAYÚSCULAS y solo alfanuméricos.

    'HOME DEPOT #2745 BUILDING' y 'HomeDepot.com' → 'HOMEDEPOT2745BUILDING' /
    'HOMEDEPOTCOM' (ambas contienen 'HOMEDEPOT'), así un match por subcadena funciona
    pese al ruido del extracto bancario.
    """
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def load_rules(session: Session, company_id: int, sector: str) -> list[CategoryRule]:
    """Carga las reglas **COMPANY** de la empresa, ordenadas por especificidad (más largo primero).

    Decisión 26-jun-2026: por ahora **solo reglas COMPANY** (privadas de la empresa). Las reglas
    **SECTOR** (compartidas) se desactivaron hasta tener volumen de datos — sin él meten ruido.
    Ver `DEUDA TECNICA #14` y `PROPUESTA DE REGLAS.md`. El campo `scope` se conserva en el modelo
    para reactivarlas **sin migración**: bastaría con volver a agregar la rama SECTOR a este query.
    (`sector` se mantiene en la firma para cuando se reactiven.)

    Solo devuelve reglas con `account_type` definido (necesario para el P&L) y `match_text` no vacío.
    El `match_text` más largo va primero → la regla **más específica gana** (`match_rule` toma el
    primero que matchea); las genéricas quedan para el final.
    """
    rules = session.exec(
        select(CategoryRule).where(
            (CategoryRule.scope == RuleScope.COMPANY)
            & (CategoryRule.company_id == company_id)
        )
    ).all()
    usable = [r for r in rules if r.account_type is not None and normalize(r.match_text)]
    usable.sort(key=lambda r: -len(normalize(r.match_text)))  # más específico (largo) primero
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


def apply_rule_to_transactions(
    session: Session, company_id: int, rule: CategoryRule, exclude_id: int | None = None
) -> int:
    """Aplica una regla a las transacciones YA EXISTENTES del mismo comercio (corregir una → corregir
    todas). Actualiza account_type/category de las que matchean la regla y aún no coinciden. Deja la
    `subcategory` (el comercio específico) de cada una. Devuelve cuántas actualizó.

    Se llama tras una corrección manual: el dueño arregla un movimiento y todas las demás del mismo
    comercio se alinean de una vez (consistencia retroactiva)."""
    if rule is None or rule.account_type is None:
        return 0
    txs = session.exec(
        select(Transaction).where(Transaction.company_id == company_id)
    ).all()
    updated = 0
    for t in txs:
        if exclude_id is not None and t.id == exclude_id:
            continue
        if is_check(t.description):
            continue  # los cheques no se agrupan por comercio
        if match_rule([rule], t.description or "") is None:
            continue
        if (t.account_type, t.category) == (rule.account_type, rule.category):
            continue  # ya está alineada
        t.account_type = rule.account_type
        t.category = rule.category
        t.source = "rule"
        t.needs_review = False
        t.review_question = None
        session.add(t)
        updated += 1
    return updated


def classify_merchant(
    description: str,
    extra_gas: Optional[list[str]] = None,
    extra_super: Optional[list[str]] = None,
) -> Optional[str]:
    """¿La descripción es de una gasolinera o un supermercado? → "gas_station" | "supermarket" | None.

    Se evalúa GASOLINERA primero (ej. "COSTCO GAS" es gasolinera, no supermercado).

    `extra_gas`/`extra_super`: comercios **aprendidos** (de la DB, por empresa) que se suman a las
    listas base. Así una gasolinera nueva que descubrió la IA se reconoce **sin tocar el código**.

    Match por **palabra/frase completa** (igual que `match_rule`), NO subcadena suelta: así
    "ARCO" (gasolinera) NO matchea "MARCOS" (un Zelle a una persona). Antes se usaba subcadena
    (`normalize(m) in norm`) y "MARCOS" → "MARCOSGARCIA" contenía "ARCO" → falso positivo que
    clasificaba el Zelle como combustible. Ver `docs/MEJORAS Y HALLAZGOS.md`."""
    phrase = f" {_phrase(description)} "
    if phrase == "  ":
        return None
    gas = [*GAS_STATIONS, *extra_gas] if extra_gas else GAS_STATIONS
    sup = [*SUPERMARKETS, *extra_super] if extra_super else SUPERMARKETS
    if any(f" {_phrase(m)} " in phrase for m in gas):
        return "gas_station"
    if any(f" {_phrase(m)} " in phrase for m in sup):
        return "supermarket"
    return None


def amount_band_category(
    description: str,
    amount: float,
    supermarket_threshold: Optional[Decimal] = None,
    gas_station_threshold: Optional[Decimal] = None,
    extra_gas: Optional[list[str]] = None,
    extra_super: Optional[list[str]] = None,
) -> Optional[tuple[AccountType, str, bool]]:
    """Desambigua por MONTO en comercios mixtos (super/gasolinera). Determinista, antes de la IA.

    Devuelve `(account_type, category, near_threshold)` o `None` si el comercio no es super/gasolinera.
    `near_threshold=True` cuando el monto cae en la **zona gris** (±25% del umbral): la banda propone
    una categoría pero pide revisión (su decisión binaria ahí es poco confiable).
    `amount` puede venir con signo; se usa su magnitud. `extra_gas`/`extra_super`: comercios aprendidos.

    --- MAPEO (centralizado aquí; ajustar cuando se defina el esquema final) ---
    Supermercado:  < umbral → comida (Meals, OpEx) · ≥ umbral → personal (Owner Draw, NON_PL)
    Gasolinera:    < umbral → maquinaria (Equipment Fuel, COGS) · ≥ umbral → vehículo (Vehicle Fuel, OpEx)
    """
    # Cuota/membresía/renovación (ej. "COSTCO *ANNUAL RENEWAL"): no es compra de comida/combustible
    # ni retiro → la banda NO aplica; que la IA decida (evita el falso "Owner Draw @1.00").
    if _MEMBERSHIP_RE.search(description or ""):
        return None

    kind = classify_merchant(description, extra_gas, extra_super)
    if kind is None:
        return None

    magnitude = abs(Decimal(str(amount)))

    if kind == "supermarket":
        threshold = supermarket_threshold or DEFAULT_SUPERMARKET_THRESHOLD
        result = ((AccountType.OPERATING_EXPENSE, "Meals") if magnitude < threshold
                  else (AccountType.NON_PL, "Owner Draw"))
    else:  # gas_station
        threshold = gas_station_threshold or DEFAULT_GAS_STATION_THRESHOLD
        result = ((AccountType.COGS, "Equipment Fuel") if magnitude < threshold
                  else (AccountType.OPERATING_EXPENSE, "Vehicle Fuel"))

    near = abs(magnitude - threshold) <= threshold * GRAY_ZONE_RATIO
    return (result[0], result[1], near)


def load_learned_merchants(
    session: Session, company_id: int, sector: str
) -> tuple[list[str], list[str]]:
    """Comercios APRENDIDOS aplicables: los de la empresa (COMPANY) + los compartidos del sector
    (SECTOR). Devuelve `(gasolineras, supermercados)` como listas de `match_text`, listas para
    pasarlas a `classify_merchant`/`amount_band_category` como `extra_gas`/`extra_super`."""
    rows = session.exec(
        select(LearnedMerchant).where(
            or_(
                (LearnedMerchant.scope == RuleScope.COMPANY)
                & (LearnedMerchant.company_id == company_id),
                (LearnedMerchant.scope == RuleScope.SECTOR)
                & (LearnedMerchant.sector == sector),
            )
        )
    ).all()
    gas = [r.match_text for r in rows if r.kind == "gas_station"]
    sup = [r.match_text for r in rows if r.kind == "supermarket"]
    return gas, sup


def upsert_learned_merchant(
    session: Session, company_id: int, sector: str, match_text: str, kind: str
) -> Optional[LearnedMerchant]:
    """Aprende un comercio nuevo a scope **COMPANY** (privado, reversible). Idempotente: no duplica,
    y no aprende lo que ya reconoce la lista base (`classify_merchant`). Devuelve la fila o None."""
    phrase = _phrase(match_text or "")
    if not phrase or kind not in ("gas_station", "supermarket"):
        return None
    if classify_merchant(match_text) is not None:
        return None  # ya es un comercio conocido (built-in) → no hace falta aprenderlo
    existing = session.exec(
        select(LearnedMerchant).where(
            (LearnedMerchant.scope == RuleScope.COMPANY)
            & (LearnedMerchant.company_id == company_id)
            & (LearnedMerchant.kind == kind)
        )
    ).all()
    for m in existing:
        if _phrase(m.match_text) == phrase:
            return m  # ya aprendido
    m = LearnedMerchant(
        scope=RuleScope.COMPANY, company_id=company_id, sector=sector,
        match_text=phrase, kind=kind,
    )
    session.add(m)
    return m


# Palabras genéricas que no aportan especificidad (no deben quedar como regla por sí solas).
_STOPWORDS = {
    "THE", "AND", "INC", "LLC", "CO", "CORP", "LTD", "PAYMENT", "POS", "DEBIT", "CREDIT",
    "PURCHASE", "ONLINE", "TRANSFER", "FROM", "TO", "OF", "FOR", "ORIG", "DESC", "ID", "CO",
}


def _phrase_in_description(dwords: list[str]) -> str:
    """Una frase CONSECUTIVA presente en la descripción: 2 palabras significativas seguidas si las
    hay, si no una sola palabra significativa, si no la primera palabra. Garantiza que `match_rule`
    (que matchea por palabra/frase completa) **sí** la encuentre en la descripción."""
    sig_idx = [i for i, w in enumerate(dwords)
               if not w.isdigit() and len(w) >= 2 and w not in _STOPWORDS]
    for i in sig_idx:  # 2 palabras significativas CONSECUTIVAS
        if i + 1 < len(dwords):
            nxt = dwords[i + 1]
            if not nxt.isdigit() and len(nxt) >= 2 and nxt not in _STOPWORDS:
                return f"{dwords[i]} {nxt}"
    if sig_idx:
        return dwords[sig_idx[0]]
    return dwords[0] if dwords else ""


def specific_phrase(description: Optional[str], merchant: Optional[str]) -> str:
    """Devuelve un `match_text` **específico** (~2 palabras) que **SÍ aparece en la descripción**,
    para que la regla matchee al menos su transacción de origen (no nazca muerta).

    Prioriza el comercio (subcategory) SOLO si sus palabras aparecen en la descripción; si el
    comercio es un nombre sintetizado por la IA que no está en el texto crudo (p. ej. "CLIENT
    PAYMENT" para "DEPOSIT ID NUMBER"), cae a una frase REAL tomada de la descripción."""
    mwords = _phrase(merchant or "").split()
    dwords = _phrase(description or "").split()
    dtext = f" {' '.join(dwords)} "

    # Comercio de ≥2 palabras: usarlo solo si aparece como frase consecutiva en la descripción.
    if len(mwords) >= 2:
        cand = " ".join(mwords[:2])
        if f" {cand} " in dtext:
            return cand

    # Comercio de 1 palabra: solo si está en la descripción. Se extiende con la palabra
    # INMEDIATAMENTE siguiente (consecutiva) si es significativa; si no, se queda sola.
    if len(mwords) == 1 and mwords[0] in dwords:
        w = mwords[0]
        i = dwords.index(w)
        if i + 1 < len(dwords):
            nxt = dwords[i + 1]
            if not nxt.isdigit() and len(nxt) >= 2 and nxt not in _STOPWORDS:
                return f"{w} {nxt}"
        return w

    # El comercio no aparece en la descripción → tomar una frase real de la descripción.
    return _phrase_in_description(dwords)


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
