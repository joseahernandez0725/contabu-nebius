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
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlmodel import Session, select

from merchants import GAS_STATIONS, SUPERMARKETS
from corrections import VIA_RULE_APPLIED, VIA_RULE_PRECEDENCE, record_change, snapshot
from models import AccountType, CategoryRule, RuleScope, Transaction

# 1-oct-2026 (Jose): el motor YA NO categoriza por monto — se fue `amount_band_category()` con sus
# umbrales por defecto (supermercado $50, gasolinera $30). Lo que no cubre una regla queda Pendiente.

# Un CHEQUE es un método de pago, no una contraparte: cada cheque puede ser para algo distinto
# (no se puede agrupar) y su descripción ("CHECK #1234") es única → NO sirve crear una regla.
# CHEQUE = la descripción EMPIEZA con CHECK / CHEQUE ("CHECK 20169  04/01"). Corregido el 8-oct (bug
# comprobado contra un extracto real de Chase): antes bastaba la palabra en cualquier lugar, y "CHK"
# también contaba. Pero en Chase / Bank of America "CHK" es la CUENTA de origen ("Online Transfer to
# CHK ...2930"), y "ATM CHECK DEPOSIT" es un DEPÓSITO: las dos se guardaban como cheque, con número
# falso, y ninguna regla creada después las tocaba. La columna "Check or Slip #" de Chase tampoco
# basta sola: también trae el número de boleta de los depósitos.
_CHECK_RE = re.compile(r"^\s*(?:check|cheque)\b", re.IGNORECASE)
# Las palabras de cheque que se QUITAN al armar la firma del comercio (`merchant_signature`). Se
# conserva la lista vieja a propósito: cambiarla movería cómo se agrupan los comercios en el auditor.
_CHECK_WORDS_RE = re.compile(r"\b(check|chk|cheque|e-?check)\b", re.IGNORECASE)


def is_check(description: Optional[str]) -> bool:
    """True si la descripción es un cheque emitido (empieza con CHECK / CHEQUE): no agrupar, no
    crear regla sin el texto del bookkeeper. "CHK" (la cuenta) y "ATM CHECK DEPOSIT" no lo son."""
    return bool(description and _CHECK_RE.search(description))


# Número de cheque tras CHECK / CHEQUE al INICIO (ej. "CHECK 20169  04/01" → "20169").
_CHECK_NUM_RE = re.compile(r"^\s*(?:check|cheque)\s*#?\s*(\d+)", re.IGNORECASE)


def check_number(description: Optional[str]) -> Optional[str]:
    """Extrae el número de cheque de la descripción; None si no hay. El banco da el número pero
    NO el beneficiario → se guarda para identificar el cheque y preguntar en revisión."""
    if not description:
        return None
    m = _CHECK_NUM_RE.search(description)
    return m.group(1) if m else None


# MÁSCARAS DE CUENTA: el banco tapa el número con una cantidad de X (o *) que cambia de banco a
# banco y hasta de mes a mes — "XXXX7581", "XXXXXXXX7581", "****7581". Se quitan ANTES de comparar,
# así la regla se escribe con los dígitos que importan (`TRANSFER*TO*7581`) y caza cualquier
# máscara. Solo cuenta como máscara una racha de 2+ X/* pegada a dígitos: "XBOX" o "TAXES" no se tocan.
_MASK_RE = re.compile(r"\b(?:X{2,}|\*{2,})(?=\d)")


def _unmask(text: str) -> str:
    """'XXXXXXXX7581' → ' 7581' (en mayúsculas). Ver `_MASK_RE`."""
    return _MASK_RE.sub(" ", text.upper())


def normalize(text: str) -> str:
    """Normaliza una descripción para comparar: MAYÚSCULAS y solo alfanuméricos.

    'HOME DEPOT #2745 BUILDING' y 'HomeDepot.com' → 'HOMEDEPOT2745BUILDING' /
    'HOMEDEPOTCOM' (ambas contienen 'HOMEDEPOT'), así un match por subcadena funciona
    pese al ruido del extracto bancario.
    """
    return re.sub(r"[^A-Z0-9]", "", _unmask(text))


# --- Identidad de comercio (merchant signature) -------------------------------------------------
# Prefijos de pasarela/procesador: el COMERCIO real viene DESPUÉS (SQ *JOES COFFEE → JOES COFFEE).
_PROCESSOR_PREFIXES = {
    "SQ", "TST", "SPO", "DD", "DOORDASH", "UBER", "INTUIT", "GOOGLE", "MICROSOFT", "MSFT",
    "GEICO", "HIS", "AMZN", "AMAZON", "PAYPAL", "PP", "PY", "EB", "WL", "CKE", "GUSTO", "WPY",
}
# Palabras de ruido del banco que NO identifican al comercio (se descartan de la firma).
_MERCHANT_NOISE = {
    "POS", "DEBIT", "CREDIT", "PURCHASE", "PAYMENT", "PMT", "ONLINE", "RECURRING", "ACH",
    "CARD", "VISA", "MASTERCARD", "MC", "ID", "REF", "TRANSACTION", "AUTH", "PENDING",
    "THE", "AND", "INC", "LLC", "CO", "CORP", "LTD", "OF", "FOR", "COM", "WWW", "NET", "BILL",
}


def merchant_signature(description: Optional[str], tokens: int = 2) -> str:
    """Firma del COMERCIO: agrupa transacciones del mismo comercio pese al ruido del banco
    (número de tienda `#1234`, ref#, fechas, cuentas enmascaradas `XXXX2961`, prefijos de procesador
    `SQ*`/`TST*`). Dos descripciones del mismo comercio → la **misma firma**. `''` si no hay señal.

    Devuelve hasta `tokens` palabras significativas concatenadas (sin espacios). Sirve para (1) agrupar
    transacciones por comercio y (2) detectar si una regla ya lo cubre (dedup / keyword muerto).

    Limitación honesta: NO resuelve nombres truncados/garbleados sin tokens en común (ej. `# DAY` vs
    `DAY DARMET`) — esos se dejan a la **aprobación humana** del proponedor (un merge falso miscategoriza,
    peor que no mergear)."""
    if not description:
        return ""
    text = description.upper()
    text = _CHECK_WORDS_RE.sub(" ", text)                 # palabras de cheque (y CHK)
    text = re.sub(r"X{2,}\d+", " ", text)                 # cuentas enmascaradas XXXX2961
    text = re.sub(r"#\s*\d+", " ", text)                  # número de tienda / ref #1234
    text = re.sub(r"\bREF\s*#?\s*\w+", " ", text)         # REF# dinámico
    text = re.sub(r"\d{1,2}[/-]\d{1,2}([/-]\d{2,4})?", " ", text)  # fechas
    words = re.sub(r"[^A-Z0-9]+", " ", text).split()
    if words and words[0] in _PROCESSOR_PREFIXES:         # quitar prefijo de procesador
        words = words[1:]
    sig = [w for w in words
           if any(c.isalpha() for c in w) and len(w) >= 2 and w not in _MERCHANT_NOISE]
    return "".join(sig[:tokens])


def matchable_text(description: Optional[str], extra: Optional[str] = None) -> str:
    """El texto contra el que se evalúan las reglas: la descripción del banco **más** lo que el
    bookkeeper agregó (`Transaction.description_extra`).

    Existe porque el extracto casi nunca basta: un cheque llega como "CHECK 1234" y una ACH como
    "ACH DEBIT 0398174 CO REF" — ninguna regla puede engancharse a eso. Escribiendo a quién fue el
    cheque, esa transacción entra al reglamento como cualquier otra. El texto del banco NO se
    modifica (es la evidencia); solo se concatena para buscar.
    """
    return " ".join(x for x in (description, extra) if x)


def load_rules(session: Session, company_id: int, sector: str) -> list[CategoryRule]:
    """Carga las reglas **COMPANY** de la empresa, ordenadas por especificidad (más largo primero).

    Decisión 26-jun-2026: por ahora **solo reglas COMPANY** (privadas de la empresa). Las reglas
    **SECTOR** (compartidas) se desactivaron hasta tener volumen de datos — sin él meten ruido.
    Ver `DEUDA TECNICA #14` y `PROPUESTA DE REGLAS.md`. El campo `scope` se conserva en el modelo
    para reactivarlas **sin migración**: bastaría con volver a agregar la rama SECTOR a este query.
    (`sector` se mantiene en la firma para cuando se reactiven.)

    Solo devuelve reglas con `account_type` definido (necesario para el P&L) y `match_text` no vacío.
    El `match_text` más largo va primero → la regla **más específica gana** (`match_rule` toma el
    primero que matchea); las genéricas quedan para el final. Las que tienen **prioridad manual**
    (`priority`, 1 = primero) van antes que todas — es el "subir la fila" del Excel del bookkeeper.
    """
    rules = session.exec(
        select(CategoryRule).where(
            (CategoryRule.scope == RuleScope.COMPANY)
            & (CategoryRule.company_id == company_id)
        )
    ).all()
    usable = [r for r in rules if r.account_type is not None and normalize(r.match_text)]
    # Más específico (largo) primero. EMPATE de largo → orden ALFABÉTICO (decisión de Jose, 1-oct):
    # sin este segundo criterio ganaba la que la base devolviera primero, y el resultado podía
    # cambiar sin que nadie tocara una regla.
    usable.sort(key=rule_order)
    return usable


def rule_order(r: CategoryRule) -> tuple:
    """Clave del orden de evaluación: prioridad manual (1, 2, 3…) → sin prioridad, las que tienen
    VENTANA DE FECHAS antes que las generales → la más larga → alfabético (decisión de Jose, 1-oct: sin
    esto ganaba la que la base devolviera primero, y el resultado podía cambiar sin que nadie tocara
    una regla).

    La ventana va ANTES del largo (chat central + Jose, 4-oct): una ventana es una excepción explícita
    del bookkeeper, y "STARBUCKS del 3 al 17-ago" debe ganarle a "STARBUCKS COFFEE" sin ventana aunque
    esa tenga la palabra más larga (era la historia del demo y perdía sin aviso). La prioridad manual
    sigue mandando sobre todo: es la decisión más explícita que hay."""
    return (r.priority is None, r.priority or 0, not is_exception(r),
            -len(normalize(r.match_text)), _phrase(r.match_text))


def place_priority(session: Session, company_id: int, rule: CategoryRule, priority: Optional[int]) -> None:
    """PRIORIDADES SIN REPETIR, como filas de Excel (chat central + Jose, 4-oct): poner la 1 a una regla
    la INSERTA en ese lugar y empuja a las demás; quitarle el número (None) cierra el hueco. Quedan
    siempre 1, 2, 3… sin saltos (un número mayor que la cantidad se vuelve el último)."""
    ranked = [r for r in session.exec(
        select(CategoryRule).where(CategoryRule.company_id == company_id)
        .where(CategoryRule.scope == RuleScope.COMPANY)
        .where(CategoryRule.priority != None)  # noqa: E711
    ).all() if r is not rule and (rule.id is None or r.id != rule.id)]
    ranked.sort(key=lambda r: (r.priority, r.id or 0))
    if priority is not None:
        ranked.insert(max(1, min(priority, len(ranked) + 1)) - 1, rule)
    else:
        rule.priority = None
        session.add(rule)
    for i, r in enumerate(ranked, start=1):
        if r.priority != i:
            r.priority = i
            session.add(r)


def rule_overlaps(rules: list[CategoryRule], txs: list) -> dict[int, dict]:
    """Qué reglas CAZAN LOS MISMOS movimientos (4-oct): por regla, en cuántas filas gana (`wins`), a
    quién le gana (`beats`) y contra quién pierde (`lost_to`) — `{otra_id: filas}`. `rules` ya
    ordenadas (`load_rules`); `txs` sin lo escrito a mano (no lo decide ninguna regla)."""
    out = {r.id: {"wins": 0, "beats": Counter(), "lost_to": Counter()} for r in rules}
    for t in txs:
        text = matchable_text(t.description, t.description_extra)
        hit = [r for r in rules if match_rule([r], text, t.date, t.amount) is not None]
        if not hit:
            continue
        winner = hit[0]
        out[winner.id]["wins"] += 1
        for loser in hit[1:]:
            out[winner.id]["beats"][loser.id] += 1
            out[loser.id]["lost_to"][winner.id] += 1
    return out


def simulate_delete(rules: list[CategoryRule], rule_id: int, txs: list) -> dict:
    """A dónde irían los movimientos de una regla si se borra (sin tocar nada): otra regla →
    transferencia → Pendiente. `txs` = las filas que decide esa regla."""
    rest = [r for r in rules if r.id != rule_id]
    to_rules: Counter = Counter()
    to_transfer = to_pending = 0
    for t in txs:
        w = match_rule(rest, matchable_text(t.description, t.description_extra), t.date, t.amount)
        if w is not None:
            to_rules[w.id] += 1
        elif classify_transfer(t.description) is not None:
            to_transfer += 1
        else:
            to_pending += 1
    return {"to_rules": to_rules, "to_transfer": to_transfer, "to_pending": to_pending}


def simulate_rule_change(rules: list[CategoryRule], rule_id: int, changed: CategoryRule, txs: list) -> dict:
    """Qué pasaría si la regla `rule_id` pasa a ser `changed` (otra palabra clave), sin tocar nada
    (Jose, 14-oct: editar la palabra clave con vista previa). Hace lo mismo que el PATCH: las filas
    que la regla deja de ganar se SUELTAN al motor (otra regla → transferencia → Pendiente) y las que
    pasa a ganar se TOMAN, vengan de donde vengan. `txs` = todas las del libro. Lo escrito a mano y los
    cheques sin texto no entran (ninguna regla los toca); lo manual que la nueva palabra cazaría se
    cuenta aparte, para decirlo."""
    after = sorted([changed if r.id == rule_id else r for r in rules], key=rule_order)
    to_rules: Counter = Counter()
    from_rules: Counter = Counter()
    out = {"keep": 0, "release": 0, "to_transfer": 0, "to_pending": 0,
           "take": 0, "from_pending": 0, "from_transfer": 0, "from_other": 0, "manual": 0,
           "release_examples": [], "take_examples": []}
    for t in txs:
        text = matchable_text(t.description, t.description_extra)
        if t.source == "manual":
            out["manual"] += match_rule([changed], text, t.date, t.amount) is not None
            continue
        if is_check(t.description) and not t.description_extra:
            continue
        winner = match_rule(after, text, t.date, t.amount)
        mine_now = t.source == "rule" and t.matched_rule_id == rule_id
        mine_after = winner is not None and winner.id == rule_id
        if mine_now and mine_after:
            out["keep"] += 1
        elif mine_now:
            out["release"] += 1
            if winner is not None:
                to_rules[winner.id] += 1
            elif classify_transfer(t.description) is not None:
                out["to_transfer"] += 1
            else:
                out["to_pending"] += 1
            if len(out["release_examples"]) < 3:
                out["release_examples"].append(t.description)
        elif mine_after:
            out["take"] += 1
            if t.source == "rule" and t.matched_rule_id is not None:
                from_rules[t.matched_rule_id] += 1
            elif t.source == "transfer":
                out["from_transfer"] += 1
            elif t.category is None:
                out["from_pending"] += 1
            else:
                out["from_other"] += 1  # historia vieja (banda de monto, IA)
            if len(out["take_examples"]) < 3:
                out["take_examples"].append(t.description)
    return {**out, "to_rules": to_rules, "from_rules": from_rules}


def rule_trace(rules: list[CategoryRule], tx) -> dict:
    """POR QUÉ una fila tiene su categoría (Jose, 14-oct: la fórmula, pero de UNA fila). Recorre las reglas en
    el orden real (`rules` ya ordenadas, `load_rules`) y devuelve solo las que MENCIONAN la descripción, cada
    una con lo que pasó: `wins` (decide), `date` / `amount` (la palabra coincide pero la condición no) o
    `after` (también coincide, pero va detrás de la que ganó). Si ninguna gana: transferencia o Pendiente.
    Es lo mismo que decide `match_rule`; no toca nada. Lo escrito a mano se explica igual (qué diría la
    fórmula), aunque ninguna regla lo toque."""
    text = matchable_text(tx.description, getattr(tx, "description_extra", None))
    desc = f" {_phrase(text)} "
    bare_check = is_check(tx.description) and not getattr(tx, "description_extra", None)
    steps, winner = [], None
    for i, r in enumerate(rules, start=1):
        if desc == "  " or not _matches(r.match_text, desc):
            continue
        if winner is not None:
            result = "after"
        elif not in_window(r, tx.date):
            result = "date"
        elif not in_amount(r, tx.amount):
            result = "amount"
        else:
            result, winner = "wins", r
        steps.append({"id": r.id, "order": i, "keyword": r.match_text, "result": result,
                      "account_type": r.account_type.value if r.account_type else None, "category": r.category,
                      "valid_from": r.valid_from.isoformat() if r.valid_from else None,
                      "valid_to": r.valid_to.isoformat() if r.valid_to else None,
                      "amount_min": float(r.amount_min) if r.amount_min is not None else None,
                      "amount_max": float(r.amount_max) if r.amount_max is not None else None})
    transfer = classify_transfer(tx.description) if winner is None else None
    return {"steps": steps, "checked": len(rules), "winner_id": winner.id if winner else None,
            "decision": "rule" if winner else ("transfer" if transfer else "pending"),
            "transfer": {"account_type": transfer[0].value, "category": transfer[1]} if transfer else None,
            "manual": tx.source == "manual", "bare_check": bare_check}


def has_window(r: CategoryRule) -> bool:
    return getattr(r, "valid_from", None) is not None or getattr(r, "valid_to", None) is not None


def has_amount(r: CategoryRule) -> bool:
    return getattr(r, "amount_min", None) is not None or getattr(r, "amount_max", None) is not None


def is_exception(r: CategoryRule) -> bool:
    """Una regla con CONDICIONES (fechas o monto) es una excepción explícita del bookkeeper: en el orden
    va antes que las generales de su misma prioridad (8-oct: el monto se sumó a la ventana)."""
    return has_window(r) or has_amount(r)


def in_amount(r: CategoryRule, amount) -> bool:
    """¿La MAGNITUD del movimiento cae en el rango de monto de la regla (INCLUSIVO)? Sin rango, siempre.
    Con rango y sin monto conocido, no (como la ventana: una condición nunca se aplica a ciegas)."""
    if not has_amount(r):
        return True
    if amount is None:
        return False
    a = abs(Decimal(str(amount)))
    if r.amount_min is not None and a < Decimal(str(r.amount_min)):
        return False
    if r.amount_max is not None and a > Decimal(str(r.amount_max)):
        return False
    return True


def in_window(r: CategoryRule, on: Optional[date]) -> bool:
    """¿La fecha `on` cae en la ventana de la regla (INCLUSIVA)? Sin ventana, siempre. Con ventana y
    sin fecha conocida, no: una excepción por fechas jamás se aplica a ciegas."""
    if not has_window(r):
        return True
    if on is None:
        return False
    if r.valid_from is not None and on < r.valid_from:
        return False
    if r.valid_to is not None and on > r.valid_to:
        return False
    return True


def _phrase(text: str) -> str:
    """Texto → PALABRAS en mayúsculas separadas por un solo espacio.
    'HOME DEPOT #2745' → 'HOME DEPOT 2745'. Sirve para match por **límite de palabra**."""
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9]+", " ", _unmask(text))).strip()


def _matches(match_text: str, desc_padded: str) -> bool:
    """¿El `match_text` aparece en la descripción (ya en forma ` _phrase ` con espacios a los lados)?

    - Sin `*`: la **frase completa** debe aparecer como palabra/frase (límite de palabra).
    - Con `*` (comodín = "lo que sea en medio"): cada parte debe aparecer **EN ORDEN**, con cualquier
      cosa entre ellas. Ej. `ONLINE TRANSFER*TO*7581` matchea
      "ONLINE TRANSFER REF #ABC TO CARD XXXX7581" (útil para transferencias/ACH con basura intercalada;
      la máscara `XXXX` la quita `_unmask`, así que da igual cuántas X traiga el banco).
    - El `*` va ENTRE palabras: no parte una palabra (`AMAZON` no caza "AMAZONMKTPL").

    Cada parte se compara por **límite de palabra** (la seguridad de `match_rule`: "Shell" ≠ "MICHELLE")."""
    pos = 0
    matched_any = False
    for part in match_text.split("*"):
        token = _phrase(part)
        if not token:
            continue  # parte vacía (`*` doble o al borde) → se ignora
        matched_any = True
        needle = f" {token} "
        idx = desc_padded.find(needle, pos)
        if idx == -1:
            return False
        pos = idx + len(needle) - 1  # -1: el espacio final sirve de espacio inicial del siguiente token
    return matched_any  # False si el keyword no tenía ninguna palabra real (ej. solo "*")


def match_rule(rules: list[CategoryRule], description: str, on: Optional[date] = None,
               amount=None) -> CategoryRule | None:
    """Primera regla (de la lista ya ordenada por prioridad) cuyo `match_text` aparece como
    **palabra/frase completa** en la descripción (con soporte de comodín `*`) y cuya **ventana de
    fechas** incluye `on` (la fecha del movimiento; ver `in_window`). `None` si ninguna aplica.

    Match por límite de palabra (no subcadena suelta) → reglas **específicas, sin conflicto**:
    "Shell" matchea "SHELL OIL 4471" pero NO "MICHELLE"; "Home Depot" matchea "HOME DEPOT 1234".
    Con `*`, ver `_matches` (anclas en orden, comodín en medio)."""
    desc = f" {_phrase(description)} "
    if desc == "  ":
        return None
    for rule in rules:
        if _matches(rule.match_text, desc) and in_window(rule, on) and in_amount(rule, amount):
            return rule
    return None


# --- Transferencias entre cuentas propias (determinista, NON_PL) -------------------------------
# Una transferencia/ACH entre cuentas del dueño NO toca el P&L (NON_PL). El DESTINO define la cuenta:
# tarjeta → pago de tarjeta; préstamo/línea → abono a capital; savings/checking → transferencia simple.
# Solo dispara si hay "transfer" Y una palabra de destino → NO confunde un Zelle a una persona (§D).
_TRANSFER_RE = re.compile(r"\b(transfer|xfer)\b", re.IGNORECASE)
_DEST_CARD_RE = re.compile(r"\b(credit\s*card|card|crd|cc)\b", re.IGNORECASE)
_DEST_LOAN_RE = re.compile(r"\b(loan|mortgage|heloc|bloc|loc|line\s*of\s*credit|credit\s*line)\b", re.IGNORECASE)
_DEST_ASSET_RE = re.compile(r"\b(savings|checking|chk|sav|money\s*market|mmkt)\b", re.IGNORECASE)


def classify_transfer(description: Optional[str]) -> Optional[tuple[AccountType, str]]:
    """Transferencia entre cuentas propias → `(NON_PL, categoría)` según el DESTINO, o `None` si no es
    una transferencia con destino claro (entonces va a la IA/revisión). Determinista, antes de la IA.

    - tarjeta → `Credit Card Payment` · préstamo/línea → `Loan Principal Payment` · savings/checking →
    `Transfer`. La **dirección** (TO/FROM) no cambia la categoría (sí el signo, vía `is_inflow`)."""
    if not description or not _TRANSFER_RE.search(description):
        return None
    if _DEST_CARD_RE.search(description):
        return (AccountType.NON_PL, "Credit Card Payment")
    if _DEST_LOAN_RE.search(description):
        return (AccountType.NON_PL, "Loan Principal Payment")
    if _DEST_ASSET_RE.search(description):
        return (AccountType.NON_PL, "Transfer")
    return None  # "transfer" sin destino claro → que decida la IA / revisión


def find_matching_rule(
    session: Session, company_id: int, sector: str, description: str
) -> CategoryRule | None:
    """Conveniencia para un solo lookup: carga las reglas y matchea una descripción."""
    return match_rule(load_rules(session, company_id, sector), description)


# Un comercio que aparece ≥ este nº de veces (y no tiene regla) es candidato a regla.
MIN_RULE_CANDIDATE_COUNT = 2


def find_rule_candidates(rules: list[CategoryRule], items: list[dict]) -> list[dict]:
    """Base del PROPONEDOR (determinista, sin IA). Dada una lista de transacciones ya categorizadas
    (`items`: dicts con `description`, `account_type`, `category`, `is_inflow`), agrupa por **comercio**
    (`merchant_signature`) y devuelve los comercios **recurrentes que NINGUNA regla cubre** → candidatos
    a regla, para que la IA/usuario los confirmen.

    - Salta cheques/genéricos (firma vacía) y lo ya cubierto por una regla (`match_rule`).
    - **Split por signo (§B.8):** un comercio con entradas Y salidas se separa en dos candidatos
      (ej. Clover: depósitos +REVENUE vs fees −EXPENSE).
    - Marca `mixed=True` si la IA categorizó el grupo de forma inconsistente (más razón para una regla).
    """
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for it in items:
        desc = it.get("description") or ""
        sig = merchant_signature(desc)
        if not sig:
            continue  # cheque/genérico → no se agrupa por comercio
        if match_rule(rules, desc, it.get("date"), it.get("amount")) is not None:
            continue  # ya cubierto por una regla existente
        # (Antes se saltaban gasolineras y supermercados: los decidía la banda de monto. Desde el
        # 1-oct ya no hay banda, así que ellos también necesitan regla → se proponen como cualquiera.)
        sign = "in" if it.get("is_inflow") else "out"
        groups[(sig, sign)].append(it)

    candidates: list[dict] = []
    for (sig, sign), its in groups.items():
        if len(its) < MIN_RULE_CANDIDATE_COUNT:
            continue
        cats = Counter((it.get("account_type"), it.get("category")) for it in its)
        (at, cat), _ = cats.most_common(1)[0]  # categoría mayoritaria = sugerencia
        candidates.append({
            "signature": sig,
            "sign": sign,
            "count": len(its),
            "suggested_account_type": at,
            "suggested_category": cat,
            "examples": [it.get("description") for it in its[:3]],
            "mixed": len(cats) > 1,
        })
    candidates.sort(key=lambda c: -c["count"])
    return candidates


def parse_priority(raw) -> Optional[int]:
    """'3' / '3.0' / ' 3 ' → 3. Vacío, 0, negativo o texto → None (orden automático)."""
    try:
        n = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return None
    return n if n >= 1 else None


def reapply_rule_precedence(session: Session, company_id: int, *,
                            user_id: Optional[int] = None) -> int:
    """Re-decide QUÉ regla gana en lo ya registrado tras cambiar el reglamento (p. ej. una
    prioridad). Solo toca transacciones que decidió una regla (`source="rule"`): lo corregido a mano
    y la IA no se mueven. Devuelve cuántas cambiaron de regla. Cada cambio real queda en el
    historial de correcciones (`via="rule_precedence"`, `user_id` = quien cambió el reglamento).

    Hace falta porque `apply_rule_to_transactions` aplica UNA regla a todo lo que matchea, sin mirar
    si otra debería ganarle."""
    rules = load_rules(session, company_id, "")
    txs = session.exec(
        select(Transaction).where(Transaction.company_id == company_id)
        .where(Transaction.source == "rule")
    ).all()
    moved = 0
    for t in txs:
        if is_check(t.description) and not t.description_extra:
            continue
        winner = match_rule(rules, matchable_text(t.description, t.description_extra), t.date, t.amount)
        if winner is None:
            continue
        sub = winner.subcategory.upper() if winner.subcategory else t.subcategory
        if winner.id == t.matched_rule_id and (t.account_type, t.category, t.subcategory) == (
                winner.account_type, winner.category, sub):
            continue
        before = snapshot(t)
        t.account_type = winner.account_type
        t.category = winner.category
        if winner.subcategory:
            t.subcategory = winner.subcategory.upper()
        t.matched_rule_id = winner.id
        t.needs_review = False
        t.review_question = None
        session.add(t)
        record_change(session, t, before, via=VIA_RULE_PRECEDENCE, user_id=user_id)
        moved += 1
    return moved


def redecide_transaction(tx: Transaction, rules: list[CategoryRule]) -> None:
    """El motor vuelve a decidir la categoría de una fila ya registrada: regla → transferencia →
    Pendiente (el mismo orden que al entrar). Lo usan los avisos del banco (`bank.apply_modified`) y
    el cambio de la ventana de fechas de una regla (las filas que quedan fuera se sueltan). Quien llama
    se asegura de que la fila no sea manual."""
    rule = match_rule(rules, matchable_text(tx.description, tx.description_extra), tx.date, tx.amount)
    if rule is not None:
        tx.account_type, tx.category = rule.account_type, rule.category
        tx.subcategory = (rule.subcategory or rule.match_text).upper()
        tx.source, tx.matched_rule_id = "rule", rule.id
        return
    transfer = classify_transfer(tx.description)
    tx.matched_rule_id = None
    if transfer is not None:
        tx.account_type, tx.category = transfer
        tx.source = "transfer"
        return
    tx.account_type = tx.category = tx.subcategory = None
    tx.source = None


def apply_rule_to_transactions(
    session: Session, company_id: int, rule: CategoryRule, exclude_id: int | None = None, *,
    user_id: Optional[int] = None,
) -> int:
    """Aplica una regla a las transacciones YA EXISTENTES del mismo comercio (corregir una → corregir
    todas). Actualiza account_type/category/subcategory de las que matchean la regla y aún no
    coinciden. Si la regla no trae subcategoría, se deja la de cada una. Devuelve cuántas actualizó.

    Se llama tras una corrección manual: el dueño arregla un movimiento y todas las demás del mismo
    comercio se alinean de una vez (consistencia retroactiva). Cada transacción que cambia de
    categoría queda en el historial de correcciones (`via="rule_applied"`, `user_id` = quien tocó
    el reglamento).

    🔴 LO ESCRITO A MANO MANDA (decisión de Jose, 3-oct-2026): una fila con `source="manual"` está
    DESCONECTADA del reglamento — ninguna regla la toca, ni al crearse ni al editarse. Es el valor
    escrito sobre la fórmula en Excel. Se reconecta solo si el bookkeeper lo pide ("volver a seguir
    la regla", `rule_action="follow_rule"`)."""
    if rule is None or rule.account_type is None:
        return 0
    if rule.id is None:
        session.flush()  # la regla recién creada necesita su id para la procedencia
    txs = session.exec(
        select(Transaction).where(Transaction.company_id == company_id)
    ).all()
    # Qué regla GANA en cada fila: aplicar esta no debe pisar las filas que otra le gana (p. ej. la
    # ventana "STARBUCKS del 3 al 17-ago" al editar "STARBUCKS"). Antes se pisaban y luego
    # `reapply_rule_precedence` las devolvía, dejando dos cambios falsos en el historial por fila.
    ranked = load_rules(session, company_id, "")
    updated = 0
    for t in txs:
        if exclude_id is not None and t.id == exclude_id:
            continue
        if t.source == "manual":
            continue  # escrita a mano → desconectada de las reglas (Jose, 3-oct)
        # Un cheque SIN texto del bookkeeper sigue excluido (no hay nada a qué engancharse). Con
        # texto ("JOE'S PLUMBING") entra al juego normal: el plomero mensual ya puede tener regla.
        if is_check(t.description) and not getattr(t, "description_extra", None):
            continue
        text = matchable_text(t.description, getattr(t, "description_extra", None))
        if match_rule([rule], text, t.date, t.amount) is None:
            continue
        winner = match_rule(ranked, text, t.date, t.amount)
        if winner is not None and winner.id != rule.id:
            continue  # otra regla le gana a esta en esa fila
        # La subcategoría también cuenta (Jose, 4-oct: "modifiqué la subcategoría en Reglas y no veo
        # el cambio en el libro" — antes solo se comparaba la categoría y el cambio no bajaba).
        sub = rule.subcategory.upper() if rule.subcategory else t.subcategory
        if (t.account_type, t.category, t.subcategory) == (rule.account_type, rule.category, sub):
            # Ya tiene los valores correctos, pero puede que le falte la PROCEDENCIA: esta regla sí
            # la cubre, así que se estampa igual. Sin esto la hoja no puede mostrar la marca `ƒ`
            # (celda = "fórmula") ni el auditor puede detectar un desvío más adelante.
            if t.matched_rule_id != rule.id:
                t.matched_rule_id = rule.id
                t.source = "rule"
                session.add(t)
            continue
        before = snapshot(t)
        t.account_type = rule.account_type
        t.category = rule.category
        if rule.subcategory:  # la regla trae comercio presentable → unifica; si no, se respeta el de cada tx
            t.subcategory = rule.subcategory.upper()
        t.source = "rule"
        t.matched_rule_id = rule.id  # procedencia: esta regla la alineó
        t.needs_review = False
        t.review_question = None
        session.add(t)
        record_change(session, t, before, via=VIA_RULE_APPLIED, user_id=user_id)
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


# ── Sugerir una palabra clave LIMPIA (4-oct-2026, pedido de Jose) ──────────────────────────────
# "Crear regla con esta fila" ponía la descripción COMPLETA del banco (fecha, ORIG ID, TRACE#…): la regla
# solo cazaba ESE movimiento. Aquí se saca el comercio de los formatos que traen los bancos de EE.UU.

_ACH_NAME_RE = re.compile(r"ORIG CO NAME:\s*(.+?)\s+ORIG ID:", re.IGNORECASE)
_P2P_RE = re.compile(r"^(?:ZELLE|VENMO|CASH ?APP)\b.*?\b(?:TO|FROM)\s+(.+)$", re.IGNORECASE)
_DOMAIN_RE = re.compile(r"\b([A-Z][A-Z0-9]{2,})\.(?:COM|NET|ORG|CO|IO|C)\b")
_PHONE_RE = re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b|\b\d{3}-\d{7}\b")
_DATE_RE = re.compile(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b")
_US_STATES = {"AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS",
              "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY",
              "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
              "WI", "WY", "DC"}


_CONNECTORS = {"OF", "THE", "AND", "&", "DE", "LA", "EL"}  # dentro de un nombre ("STATE OF CALIF")


def _is_code(token: str) -> bool:
    """¿Es un código y no una palabra? (C4860, MEA8E, 29038657532, WFCT0ZNDCQ96). `7-ELEVEN` no lo es."""
    if not any(c.isdigit() for c in token):
        return False
    return not any(part.isalpha() and len(part) >= 3 for part in token.split("-"))


# Primera palabra de ciudades compuestas (SAN JOSE, LOS ANGELES, NEW YORK…): no es el comercio.
_CITY_LEADS = {"SAN", "SANTA", "LOS", "LAS", "NEW", "EL", "FORT", "FT", "PORT", "SAINT", "ST",
               "WEST", "EAST", "NORTH", "SOUTH", "LA", "MOUNT", "MT", "PALO", "WALNUT", "DALY"}


def _name_words(text: str, max_words: int) -> str:
    """Las primeras palabras de un NOMBRE (sin códigos ni basura legal al final)."""
    out: list[str] = []
    for w in re.sub(r"[^A-Z0-9&'-]+", " ", text.upper()).split():
        if _is_code(w):
            break
        out.append(w)
        if len(out) >= max_words:
            break
    while out and out[-1] in {"INC", "LLC", "LTD", "CO", "CORP", "THE", "AND", "&"}:
        out.pop()
    return " ".join(out)


def suggest_keyword(description: Optional[str], extra: Optional[str] = None) -> str:
    """Una palabra clave CORTA y estable para la regla de este movimiento — lo que cambia en cada
    pago (fechas, números de rastreo, IDs, teléfonos, ciudad y estado) se queda afuera.

    Orden: lo que escribió el bookkeeper (`description_extra`, p. ej. a quién fue el cheque) → nombre
    de la empresa en un ACH (`ORIG CO NAME:PAYROLL SERVICE ORIG ID:…` → `PAYROLL SERVICE`) → la persona
    o empresa de un Zelle/Venmo → un dominio (`REMITLY.COM` → `REMITLY`) → las primeras palabras reales.
    Siempre devuelve algo que la regla puede cazar en ESTA descripción (si no, la frase específica)."""
    if extra and extra.strip():
        return _name_words(extra, 4) or extra.strip().upper()
    d = " ".join((description or "").upper().split())
    if not d or is_check(d):
        return ""  # un cheque SIN texto no tiene comercio: primero hay que escribir a quién fue
    cand = ""
    m = _ACH_NAME_RE.search(d)
    if m:
        cand = _name_words(m.group(1), 4)
    if not cand:
        m = _P2P_RE.search(d)
        if m:
            cand = _name_words(m.group(1), 3)
    if not cand:
        m = _DOMAIN_RE.search(d)
        if m:
            cand = m.group(1)
    if not cand:
        text = _PHONE_RE.sub(" ", _DATE_RE.sub(" ", d))
        text = re.sub(r"#\s*\w+", " ", text)
        words = [w for w in re.sub(r"[^A-Z0-9&'-]+", " ", text).split() if any(c.isalnum() for c in w)]
        # Prefijo del procesador (SQ *, TST*, POS DEBIT…) y ruido antes de la primera palabra real.
        while words and (words[0] in _PROCESSOR_PREFIXES or words[0] in _MERCHANT_NOISE or _is_code(words[0])):
            words = words[1:]
        out: list[str] = []
        real = 0
        for w in words:
            if _is_code(w) or w in _US_STATES or (real and w in _MERCHANT_NOISE and w not in _CONNECTORS):
                break
            out.append(w)
            if len(w) >= 2 and w not in _CONNECTORS:
                real += 1
            if real >= (3 if len(words) <= 3 else 2):
                break
        # La CIUDAD va justo antes del estado ("7-ELEVEN PITTSBURG CA"): si lo elegido llega hasta
        # ella, se corta ahí. También la primera mitad de una ciudad compuesta ("SAN", "LOS"…).
        # (Pegada al estado en el texto del banco: "MEMBERSHIP 800-922-8228 CA" no es una ciudad.)
        st = next((i for i, w in enumerate(words) if w in _US_STATES), None)
        if (st is not None and st >= 2 and len(out) >= st
                and re.search(rf"(^|\s){re.escape(words[st - 1])}\s+{words[st]}(\s|$)", d)):
            out = out[:st - 1]
        while st is not None and len(out) >= 2 and out[-1] in _CITY_LEADS:
            out.pop()
        cand = " ".join(out)
    # Tiene que cazar ESTA descripción (palabra completa, frase seguida). Si al limpiar quedaron
    # palabras que no eran vecinas ("ARCO#07144ARCO" → "ARCO PITTSBURG"), se acorta hasta que case.
    words = cand.split()
    while words:
        while words and words[-1] in _CONNECTORS:
            words.pop()
        c = " ".join(words)
        if c and f" {_phrase(c)} " in f" {_phrase(d)} ":
            return c
        words = words[:-1]
    return specific_phrase(d, None)


def validate_keyword(keyword: str, covers: list[str], items: list[dict]) -> dict:
    """EL GUARDIÁN del proponedor (FASE 9 Paso 3): valida un keyword PROPUESTO contra las
    transacciones reales de la empresa, ANTES de que el dueño lo apruebe. Responde las dos preguntas
    que evitan los dos errores opuestos:

    - **Cobertura** — ¿matchea TODAS las transacciones del comercio que dice cubrir?
      (`missed` > 0 → sobre-específico: nace medio muerto, se le escapan movimientos)
    - **Colisión** — ¿matchea transacciones de OTROS comercios?
      (`conflicts` > 0 → sub-específico: caza falsos; NO se debe aprobar sin editarlo)

    `covers` = firmas de comercio (`merchant_signature`) que la propuesta dice cubrir; la pertenencia
    de cada transacción se decide por su firma. `items` = dicts con `description`. Sin `covers`
    (p. ej. validar un keyword del import), solo `matches` es significativo (0 = keyword muerto).

    Determinista y barato (mismo match por palabra completa + `*` de `match_rule`), sin IA.
    Devuelve {matches, covered, missed, conflicts, conflict_examples}.
    """
    cov = {c for c in (covers or []) if c}
    matches = covered = missed = 0
    conflict_examples: list[str] = []
    for it in items:
        desc = it.get("description") or ""
        hit = _matches(keyword or "", f" {_phrase(desc)} ")
        if hit:
            matches += 1
        if not cov:
            continue  # sin firmas de pertenencia: solo se puede medir el total de matches
        own = merchant_signature(desc) in cov
        if hit and own:
            covered += 1
        elif hit and not own:
            if len(conflict_examples) < 3:
                conflict_examples.append(desc)
        elif not hit and own:
            missed += 1
    conflicts = matches - covered if cov else 0
    return {
        "matches": matches,
        "covered": covered,
        "missed": missed,
        "conflicts": conflicts,
        "conflict_examples": conflict_examples,
    }


# Sinónimos de sección del P&L → valor del enum (para importar reglas con vocabulario libre del usuario).
_SECTION_SYNONYMS = {
    "INCOME": "REVENUE", "SALES": "REVENUE",
    "COSTOFGOODSSOLD": "COGS", "COSTOFGOODS": "COGS", "DIRECTCOST": "COGS",
    "OPERATINGEXPENSE": "OPERATING_EXPENSE", "OPEX": "OPERATING_EXPENSE",
    "EXPENSE": "OPERATING_EXPENSE", "OVERHEAD": "OPERATING_EXPENSE",
    "OTHERINCOME": "OTHER_INCOME", "OTHEREXPENSE": "OTHER_EXPENSE",
    "NONPL": "NON_PL", "NONPANDL": "NON_PL", "TRANSFER": "NON_PL", "BALANCESHEET": "NON_PL",
}


def normalize_account_type(label: Optional[str]) -> str:
    """Texto libre de sección del usuario ("Income", "Operating Expense", "COGS") → valor del enum
    (`REVENUE`/`COGS`/…) o `""` si no se reconoce. Para importar reglas con vocabulario propio."""
    if not label:
        return ""
    key = re.sub(r"[^A-Z]", "", label.upper())
    for at in AccountType:
        if re.sub(r"[^A-Z]", "", at.value) == key:
            return at.value
    return _SECTION_SYNONYMS.get(key, "")


def build_import_rules(
    headers: list[str], rows: list[list[str]], keyword_col: str, category_col: str,
    account_type_col: str, sector: str, existing_keywords: list,
    subcategory_col: str = "", priority_col: str = "", from_col: str = "", to_col: str = "",
    min_col: str = "", max_col: str = "", chart: Optional[dict[str, list[str]]] = None,
) -> list[dict]:
    """Construye el PREVIEW del import: por cada fila del archivo del usuario (ya mapeado por la IA),
    extrae keyword + categoría, **resuelve la sección** (del archivo si la trae, si no por el plan de
    cuentas), y marca un `status`: `ok` · `needs_section` (no se pudo resolver) · `exists` (ya hay
    regla con ese keyword) · `duplicate` (repetido dentro del archivo).

    La identidad de una regla es keyword + condiciones (`From`/`To` y `Min amount`/`Max amount` de
    nuestra exportación): `existing_keywords` trae `(keyword, valid_from, valid_to, amount_min,
    amount_max)` o el keyword solo. Una fecha o un monto ilegible marca la fila `bad_date` /
    `bad_amount` (no se adivina una condición). `chart`: el plan del LIBRO (18-oct: el plan es por libro);
    sin él, el plan por defecto del sector."""
    from categories import categories_for  # local: evita ciclo de import
    idx = {h: i for i, h in enumerate(headers)}
    ki, ci = idx.get(keyword_col), idx.get(category_col)
    ti = idx.get(account_type_col) if account_type_col else None
    si = idx.get(subcategory_col) if subcategory_col else None
    pi = idx.get(priority_col) if priority_col else None
    fi = idx.get(from_col) if from_col else None
    ti2 = idx.get(to_col) if to_col else None
    mi = idx.get(min_col) if min_col else None
    xi = idx.get(max_col) if max_col else None
    if ki is None or ci is None:
        return []
    cat_to_type = {normalize(c): at for at, cats in (chart or categories_for(sector)).items() for c in cats}
    existing_norm = {(normalize(k[0]), *k[1:3], *(list(k[3:5]) + [None, None])[:2]) if isinstance(k, tuple)
                     else (normalize(k), None, None, None, None) for k in existing_keywords}
    out: list[dict] = []
    seen: set[tuple] = set()
    for r in rows:
        if ki >= len(r) or ci >= len(r):
            continue
        kw, cat = (r[ki] or "").strip(), (r[ci] or "").strip()
        nkw = normalize(kw)
        if not kw or not cat or not nkw:
            continue
        at = normalize_account_type(r[ti]) if (ti is not None and ti < len(r)) else ""
        if not at:
            at = cat_to_type.get(normalize(cat), "")
        vf, bad_f = _parse_day(r[fi]) if (fi is not None and fi < len(r)) else (None, False)
        vt, bad_t = _parse_day(r[ti2]) if (ti2 is not None and ti2 < len(r)) else (None, False)
        amin, bad_a = _parse_money(r[mi]) if (mi is not None and mi < len(r)) else (None, False)
        amax, bad_b = _parse_money(r[xi]) if (xi is not None and xi < len(r)) else (None, False)
        key = (nkw, vf, vt, amin, amax)
        if bad_f or bad_t or (vf and vt and vf > vt):
            status = "bad_date"
        elif bad_a or bad_b or (amin is not None and amax is not None and amin > amax):
            status = "bad_amount"
        elif key in seen:
            status = "duplicate"
        elif key in existing_norm:
            status = "exists"
        elif not at:
            status = "needs_section"
        else:
            status = "ok"
        seen.add(key)
        sub = (r[si] or "").strip() if (si is not None and si < len(r)) else ""
        out.append({"keyword": kw, "category": cat, "subcategory": sub,
                    "account_type": at, "status": status,
                    "priority": parse_priority(r[pi]) if (pi is not None and pi < len(r)) else None,
                    "valid_from": vf.isoformat() if vf else None, "valid_to": vt.isoformat() if vt else None,
                    "amount_min": float(amin) if amin is not None else None,
                    "amount_max": float(amax) if amax is not None else None})
    return out


def _parse_money(raw: Optional[str]) -> tuple[Optional[Decimal], bool]:
    """Monto de la condición en un archivo de reglas ("50", "$1,200.00"). Vacío → (None, False).
    Ilegible o negativo → (None, True): el import lo marca en vez de adivinar."""
    text = (raw or "").strip().replace("$", "").replace(",", "")
    if not text:
        return None, False
    try:
        v = Decimal(text).quantize(Decimal("0.01"))
    except Exception:
        return None, True
    return (v, False) if v >= 0 else (None, True)


def _parse_day(raw: Optional[str]) -> tuple[Optional[date], bool]:
    """Fecha de la ventana en un archivo de reglas: `YYYY-MM-DD` (lo que escribe nuestra exportación).
    Vacía → (None, False). Ilegible → (None, True): el import la marca en vez de adivinar."""
    text = (raw or "").strip()
    if not text:
        return None, False
    try:
        return date.fromisoformat(text), False
    except ValueError:
        return None, True


def create_company_rule(
    session: Session, company_id: int, sector: str,
    match_text: str, account_type: Optional[AccountType], category: Optional[str],
    subcategory: Optional[str] = None, priority: Optional[int] = None,
    valid_from: Optional[date] = None, valid_to: Optional[date] = None, origin: Optional[str] = None,
    amount_min: Optional[Decimal] = None, amount_max: Optional[Decimal] = None,
) -> Optional[CategoryRule]:
    """Crea una regla COMPANY con el keyword **TAL CUAL** (preserva el comodín `*`, a diferencia de
    `upsert_company_rule` que lo normaliza con `specific_phrase`). Para reglas ya listas del
    **proponedor** o del **import**. Idempotente por keyword normalizado. Devuelve la regla o None.
    `subcategory` solo se escribe si viene (el import la trae; el proponedor no)."""
    kw = (match_text or "").strip()
    if not kw or account_type is None or not category:
        return None
    norm = normalize(kw)
    if not norm:
        return None
    existing = session.exec(
        select(CategoryRule).where(
            (CategoryRule.scope == RuleScope.COMPANY) & (CategoryRule.company_id == company_id)
        )
    ).all()
    for r in existing:
        if normalize(r.match_text) == norm and (r.valid_from, r.valid_to) == (valid_from, valid_to) \
                and (r.amount_min, r.amount_max) == (amount_min, amount_max):
            # ya existe ese comercio con esa ventana → actualizar
            r.account_type = account_type
            r.category = category
            r.match_text = kw
            if subcategory:
                r.subcategory = subcategory
            session.add(r)
            if priority:
                place_priority(session, company_id, r, priority)  # sin repetir: empuja a las demás
            return r
    rule = CategoryRule(
        scope=RuleScope.COMPANY, company_id=company_id, sector=sector,
        match_text=kw, account_type=account_type, category=category,
        subcategory=subcategory or None,
        valid_from=valid_from, valid_to=valid_to, origin=origin,
        amount_min=amount_min, amount_max=amount_max,
    )
    session.add(rule)
    if priority:
        place_priority(session, company_id, rule, priority)
    return rule


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
        # Una corrección suelta aprende la regla GENERAL; nunca reescribe una con ventana de fechas.
        if _phrase(rule.match_text) == phrase and not is_exception(rule):
            rule.account_type = account_type
            rule.category = category
            rule.match_text = phrase
            session.add(rule)
            return rule

    # EL GUARDIÁN también en el camino AUTOMÁTICO (hallazgo 8.10): la frase derivada puede ser
    # genérica ("BAY POINT" es el pueblo, no el comercio). Si caza transacciones de OTROS
    # comercios, NO se crea la regla — la corrección queda como excepción puntual (un falso match
    # miscategoriza en silencio). Solo aplica a reglas NUEVAS: editar una existente ya pasó por acá.
    if description is not None:
        own_sig = merchant_signature(description)
        if own_sig:
            items = [
                {"description": t.description}
                for t in session.exec(
                    select(Transaction).where(Transaction.company_id == company_id)
                ).all()
            ]
            if validate_keyword(phrase, [own_sig], items)["conflicts"] > 0:
                return None

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
