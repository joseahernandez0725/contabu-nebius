"""
ContaBu — pipeline de ingestión de archivos bancarios.

CSV bancario desordenado → (IA mapea columnas) → parsea cada fila → (reglas → IA categoriza
lo que no matchea) → crea Transaction. Pensado para datos reales (formatos variados).

Convención: `amount` se guarda como MAGNITUD positiva; el signo (dirección entrada/salida)
se le pasa a la IA para que clasifique bien.
"""

import csv
import io
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from dateutil import parser as dateparser
from sqlalchemy import func
from sqlmodel import Session, select

import os

from ai import categorize_transaction, map_columns

# ─────────────────────────────────────────────────────────────────────────────────────────────
# LA IA NO CATEGORIZA (idea #15 de Jose, decidida el 27-sep-2026).
#
# "Toda la categorización sale de la base de reglas; los agentes únicamente supervisan que todo
# esté coherente." Lo que ninguna regla/transferencia cubre **NO se rellena con una
# adivinanza**: queda como PENDIENTE (rojo en la hoja) y el bookkeeper decide — categoría a mano
# (queda `✎`) o una regla nueva (queda `ƒ`). La IA pasa a dos trabajos: **proponer reglas** (a
# pedido del usuario, `POST /rule-proposals`) y **auditar coherencia** (determinista, gratis).
#
# Se deja tras una bandera para poder volver atrás sin tocar código. Ver `IDEAS.md` #15.
# ─────────────────────────────────────────────────────────────────────────────────────────────
AI_CATEGORIZES = os.getenv("AI_CATEGORIZES", "").lower() in {"1", "true", "yes"}
from company_profile import format_profile_facts
from models import AccountType, CategoryRule, Company, Transaction

# Ventana de asentamiento por defecto (días): cubre tarjeta/ACH normales (1-3 días hábiles) + un
# fin de semana. Se retienen los movimientos recientes/pending hasta que liquidan (base caja).
DEFAULT_SETTLEMENT_BUFFER_DAYS = 5

# Categorización LÍNEA POR LÍNEA: una petición a Gemini por transacción (cada una aislada → es
# IMPOSIBLE que los resultados se desalineen, a diferencia del batch). Para que no sea lento, se
# lanzan en paralelo con un límite de concurrencia. Requiere tier de pago de Gemini (el free es
# insuficiente para archivos grandes). Ver docs/FASE 6.F / NOTAS IMPORTANTES.
AI_CONCURRENCY = 16

# Movimientos intrínsecamente AMBIGUOS: la IA no puede saber su propósito solo del texto
# (cheques, depósitos sin pagador claro, retiros, remesas, transferencias). Se fuerzan a revisión
# humana en vez de adivinar (adivinar corrompe el P&L). Los cheques además ni se agrupan ni crean
# regla (ver rules.is_check).
_AMBIGUOUS_RE = re.compile(
    r"\b(deposit|withdrawal|remitly|rmtly|wire|transfer)\b", re.IGNORECASE
)

# Palabras que indican que un movimiento aún NO liquidó (no debe entrar a los libros todavía).
_PENDING_WORDS = ("pending", "hold", "authoriz", "processing", "unposted", "in process")


def is_pending(status: str | None) -> bool:
    """True si el valor de la columna de estado indica que el movimiento aún no liquidó."""
    if not status:
        return False
    s = status.strip().lower()
    return any(w in s for w in _PENDING_WORDS)
from rules import check_number, classify_transfer, is_check, load_rules, match_rule


def needs_human_review(description: str) -> bool:
    """True si el movimiento es ambiguo por naturaleza (cheque/depósito/retiro/remesa/transferencia)
    → forzar revisión humana en vez de que la IA adivine."""
    return is_check(description) or bool(_AMBIGUOUS_RE.search(description or ""))


# "Comercios" genéricos que NO representan una contraparte real (depósitos, pagadores desconocidos):
# no se deben unificar entre sí (un "DEPOSIT" no es lo mismo que otro). Se comparan normalizados.
_GENERIC_MERCHANTS = {
    "UNKNOWN", "UNKNOWNPAYEE", "BANK", "BANKDEPOSIT", "DEPOSIT", "CASH", "CHECK", "OTHER", "NA",
}


def _merchant_key(subcategory: str | None) -> str | None:
    """Clave normalizada del comercio para agrupar (solo alfanuméricos, MAYÚSCULAS).
    Devuelve None si es vacío/corto o un 'comercio' genérico que no debe unificarse."""
    if not subcategory:
        return None
    key = re.sub(r"[^A-Z0-9]", "", subcategory.upper())
    if len(key) < 3 or key in _GENERIC_MERCHANTS:
        return None
    return key


def unify_merchant_categorization(parsed: list[tuple], ai_cats: dict[int, object]) -> int:
    """Consistencia INTRA-SUBIDA: el mismo comercio → la MISMA categorización en todo el archivo.

    La IA categoriza fila por fila (aislada), así que el mismo comercio puede salir distinto
    (ej. PAYROLL SERVICE como Direct Labor / Admin Wages / Legal & Professional). Aquí, para cada
    comercio que aparece ≥2 veces, se toma la categorización de **mayor confianza** (su mejor
    evidencia) y se aplica a TODAS sus apariciones (account_type, category, subcategory, confidence,
    needs_review). Determinista, sin IA. Devuelve cuántas filas se ajustaron.

    Se excluyen los movimientos ambiguos por naturaleza (cheques/depósitos/transferencias/remesas):
    no son una contraparte recurrente y se resuelven en su propia capa de revisión."""
    groups: dict[str, list[int]] = {}
    for idx, cat in ai_cats.items():
        if needs_human_review(parsed[idx][1]):
            continue  # ambiguo por naturaleza → no se unifica
        key = _merchant_key(getattr(cat, "subcategory", None))
        if key:
            groups.setdefault(key, []).append(idx)

    adjusted = 0
    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        canonical = max(idxs, key=lambda i: ai_cats[i].confidence or 0.0)
        c = ai_cats[canonical]
        for i in idxs:
            cat = ai_cats[i]
            if (cat.account_type, cat.category) != (c.account_type, c.category) \
                    or cat.confidence != c.confidence:
                adjusted += 1
            cat.account_type = c.account_type
            cat.category = c.category
            cat.subcategory = c.subcategory
            cat.confidence = c.confidence
            cat.needs_review = c.needs_review
            cat.review_question = c.review_question
    return adjusted


def parse_csv(content: bytes) -> tuple[list[str], list[list[str]]]:
    """Bytes de CSV → (headers, filas). Ignora líneas vacías. Maneja BOM de Excel."""
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any(cell.strip() for cell in r)]
    if not rows:
        return [], []
    return rows[0], rows[1:]


def parse_xlsx(content: bytes) -> tuple[list[str], list[list[str]]]:
    """Bytes de un .xlsx → (headers, filas). Lee la **primera hoja**; ignora filas vacías; la 1ª fila
    no vacía son los encabezados. Las celdas se normalizan a str (números/fechas → texto)."""
    import openpyxl  # import perezoso: solo si llega un Excel
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    headers: list[str] = []
    for row in rows_iter:
        cells = ["" if c is None else str(c).strip() for c in row]
        if any(cells):
            headers = cells
            break
    data: list[list[str]] = []
    for row in rows_iter:
        cells = ["" if c is None else str(c).strip() for c in row]
        if any(cells):
            data.append(cells)
    wb.close()
    return headers, data


def parse_spreadsheet(content: bytes, filename: str | None = None) -> tuple[list[str], list[list[str]]]:
    """Despacha a Excel o CSV según extensión/firma. Los `.xlsx` son ZIP → empiezan con `PK`."""
    name = (filename or "").lower()
    is_xlsx = name.endswith(".xlsx") or content[:2] == b"PK"
    return parse_xlsx(content) if is_xlsx else parse_csv(content)


def parse_amount(raw: str) -> Decimal:
    """Monto sucio → Decimal con signo. '$1,234.56' → 1234.56 ; '(342.18)' → -342.18."""
    s = raw.strip().replace("$", "").replace(" ", "")
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace(",", "")  # quita paréntesis y separadores de miles (formato US)
    if not s or s in {"-", "."}:
        return Decimal("0")
    try:
        value = Decimal(s)
    except InvalidOperation:
        return Decimal("0")
    return -abs(value) if negative else value


def signed_from_row(
    r: list[str], i_amount: int | None, i_debit: int | None, i_credit: int | None
) -> Decimal:
    """Monto CON SIGNO de una fila. Si hay columna única de monto, la usa. Si el banco trae columnas
    separadas **Debit** (salida) y **Credit** (entrada), las combina: `+credit − debit` (en magnitud,
    porque el signo lo da la columna). Cada fila suele traer solo una de las dos."""
    if i_amount is not None and i_amount < len(r) and r[i_amount].strip():
        return parse_amount(r[i_amount])
    credit = abs(parse_amount(r[i_credit])) if (i_credit is not None and i_credit < len(r)) else Decimal(0)
    debit = abs(parse_amount(r[i_debit])) if (i_debit is not None and i_debit < len(r)) else Decimal(0)
    return credit - debit


def mask_account(raw: str | None) -> str | None:
    """Normaliza una cuenta bancaria: conserva el NOMBRE del banco y enmascara el número a
    los ÚLTIMOS 4 DÍGITOS. 'Chase Checking 001234567890' → 'Chase Checking ****7890';
    '001234567890' → '****7890'; '****4471' → '****4471'.

    No guardamos el número completo (es sensible y no hace falta): solo el nombre + los 4
    últimos dígitos, lo justo para distinguir/filtrar cuentas. Si no hay al menos 4 dígitos,
    devuelve None (no es una cuenta utilizable → habrá que pedírsela al usuario)."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 4:
        return None
    # Nombre = el texto sin dígitos ni caracteres de máscara, con separadores colapsados.
    name = re.sub(r"[\d*•]", "", raw)
    name = re.sub(r"[#\-_\s]+", " ", name).strip()
    masked = f"****{digits[-4:]}"
    return f"{name} {masked}" if name else masked


def parse_date(raw: str) -> date:
    """Fecha en formato variado de banco → date (asume mes primero, formato US)."""
    return dateparser.parse(raw.strip(), dayfirst=False).date()


def make_fingerprint(d: date, description: str, signed: Decimal, account: str | None) -> str:
    """Huella de contenido de un movimiento, para deduplicar re-subidas y solapamientos.

    Identifica la transacción por (fecha | descripción normalizada | monto CON SIGNO | cuenta):
    - descripción: espacios colapsados + MAYÚSCULAS (los bancos varían el espaciado entre exports).
    - monto con signo: distingue una entrada de una salida del mismo importe el mismo día.
    - cuenta: el mismo cargo en DOS cuentas distintas son movimientos distintos.
    NO es única a propósito: dos movimientos idénticos legítimos (p. ej. dos cafés iguales el
    mismo día) comparten huella; la deduplicación se hace por CONTEO. Ver docs/NOTAS IMPORTANTES.md.
    """
    desc = re.sub(r"\s+", " ", description).strip().upper()
    acct = (account or "").strip().upper()
    return f"{d.isoformat()}|{desc}|{signed:.2f}|{acct}"


def _apply_categorization(tx: Transaction, idx: int, description: str,
                          rule_hits: dict, transfer_hits: dict,
                          ai_cats: dict) -> "str | None":
    """Aplica a `tx` lo que decidió el motor (regla → transferencia → IA) + los dos guardias
    (revisión humana en ambiguos resueltos por IA, detector de signo). Compartido por la SUBIDA y
    por `categorize_pending` (mismo cerebro en ambos caminos). Devuelve la fuente
    ("rule"|"transfer"|"ai") o None si nada decidió → Pendiente.

    Ya no hay banda de monto ni criterios que categoricen (decisión de Jose, 1-oct-2026): las filas
    viejas con `source="band"` se quedan como historia."""
    source = None
    if idx in rule_hits:
        rule = rule_hits[idx]
        tx.account_type = rule.account_type
        tx.category = rule.category
        # El comercio presentable de la regla; sin él, el patrón que la disparó (histórico).
        tx.subcategory = (rule.subcategory or rule.match_text).upper()
        tx.confidence = 1.0
        tx.needs_review = False
        tx.review_question = None
        tx.ai_categorized = False  # vino de una regla, no de la IA
        tx.source = "rule"
        tx.matched_rule_id = rule.id  # procedencia exacta: QUÉ regla decidió (FASE 9)
        source = "rule"
    elif idx in transfer_hits:
        at, cat = transfer_hits[idx]
        tx.account_type = at  # NON_PL (transferencia entre cuentas propias → fuera del P&L)
        tx.category = cat
        tx.confidence = 1.0
        tx.needs_review = False
        tx.review_question = None
        tx.ai_categorized = False  # determinista, no IA
        tx.source = "transfer"
        source = "transfer"
    elif idx in ai_cats:
        cat = ai_cats[idx]
        tx.account_type = cat.account_type
        tx.category = cat.category
        tx.subcategory = (cat.subcategory or "").upper() or None
        tx.confidence = cat.confidence
        tx.needs_review = cat.needs_review
        tx.review_question = cat.review_question or None
        tx.ai_categorized = True
        tx.source = "ai"
        # Predicción ORIGINAL de la IA (FASE 9 Paso 6): se congela aquí y NUNCA se toca al
        # corregir → precisión online (comparar contra la categoría final, la verdad del dueño).
        tx.ai_original_account_type = cat.account_type.value if cat.account_type else None
        tx.ai_original_category = cat.category
        source = "ai"

    # Forzar revisión humana en movimientos AMBIGUOS por naturaleza (cheques, depósitos,
    # retiros, remesas, transferencias) que resolvió la IA: no puede saber su propósito → que
    # el dueño decida. Si ya había REGLA (source="rule"), el usuario ya decidió → NO re-preguntar.
    if source == "ai" and needs_human_review(description):
        tx.needs_review = True
        if tx.confidence is None or tx.confidence > 0.5:
            tx.confidence = 0.5

    # Detector de SIGNO (vale para regla e IA): si la dirección del banco contradice la
    # sección del P&L, casi seguro es un return/reembolso o un cobro mal clasificado → revisar.
    # Cubre el caso "return de Home Depot": entró plata pero la regla lo etiquetó como gasto
    # (COGS/Materials) → sin esto sumaría como gasto en vez de restarlo. NON_PL se exime
    # (transferencias/retiros/aportes van en cualquier dirección legítimamente).
    if tx.account_type is not None and tx.account_type != AccountType.NON_PL:
        is_income = tx.account_type in (AccountType.REVENUE, AccountType.OTHER_INCOME)
        if is_income != tx.is_inflow:  # plata que entró etiquetada como gasto, o al revés
            tx.needs_review = True
            if tx.review_question is None:
                tx.review_question = (
                    "Money came IN but this looks like an expense — is it a refund/return?"
                    if tx.is_inflow else
                    "Money went OUT but this looks like income — is it categorized right?"
                )
    return source


def ingest_csv(
    session: Session,
    company_id: int,
    sector: str,
    content: bytes,
    *,
    categorize: bool = True,
    limit: int | None = None,
    offset: int = 0,
    dry_run: bool = False,
    filename: str | None = None,
    ai_limit: int | None = None,
    profile: dict | None = None,
) -> dict:
    """Pipeline completo. Acepta CSV o Excel (.xlsx). Devuelve un resumen (filas, mapeo, errores).
    `profile`: la entrevista del dueño (`Company.profile`) — sus hechos viajan al prompt de la IA."""
    headers, rows = parse_spreadsheet(content, filename)
    if not rows:
        return {"inserted": 0, "errors": 0, "mapping": None, "message": "archivo vacío"}

    # 1) La IA descubre qué columna es cuál (con varias filas de muestra) y, si puede,
    #    detecta la cuenta bancaria de origen (columna por fila o etiqueta de todo el archivo).
    mapping = map_columns(headers, rows[:3])
    try:
        i_date = headers.index(mapping.date_column)
        i_desc = headers.index(mapping.description_column)
    except ValueError:
        return {"inserted": 0, "errors": 0, "mapping": mapping.model_dump(),
                "message": "la IA devolvió una columna que no existe en el archivo"}
    # Monto: una columna con signo, O dos columnas separadas Debit (salida) / Credit (entrada).
    i_amount = headers.index(mapping.amount_column) if mapping.amount_column in headers else None
    i_debit = headers.index(mapping.debit_column) if mapping.debit_column in headers else None
    i_credit = headers.index(mapping.credit_column) if mapping.credit_column in headers else None
    if i_amount is None and i_debit is None and i_credit is None:
        return {"inserted": 0, "errors": 0, "mapping": mapping.model_dump(),
                "message": "no se encontró columna de monto (ni Debit/Credit) en el archivo"}

    # Cuenta bancaria: si hay columna por fila, la leemos de cada fila; si no, usamos la
    # etiqueta detectada para todo el archivo. En ambos casos se guarda enmascarada a los
    # últimos 4 dígitos ('****4471'); si no hay 4 dígitos, queda None y se la pediremos al usuario.
    i_account = headers.index(mapping.account_column) if mapping.account_column in headers else None
    file_account_raw = mapping.account_label or None
    # Columna de estado (pending/posted), si el archivo la trae.
    i_status = headers.index(mapping.status_column) if mapping.status_column in headers else None

    # Ventana de asentamiento de la empresa (o el default del sistema). Se retienen los
    # movimientos recientes/pending hasta que liquidan; entran solos en una subida posterior.
    company = session.get(Company, company_id)
    # Los días de espera son un ajuste simple del libro (`Company.settlement_buffer_days`, Ajustes del
    # libro). Volvió aquí el 2-oct: `criteria.py`, que lo leía, se borró con los criterios por monto.
    buffer_days = settlement_buffer_days(company)
    today = datetime.now(timezone.utc).date()

    # Trozo a procesar: rows[offset : offset+limit]. Permite subir en LOTES (cada lote bajo el
    # límite de ~300s del navegador) → el frontend itera el offset mostrando progreso. `total` es
    # el nº de filas de datos del archivo (para que el frontend sepa cuándo terminó).
    total = len(rows)
    to_process = rows[offset:offset + limit] if limit else rows[offset:]

    # 1) Parsear las filas y RETENER las que aún no liquidaron (pending o demasiado recientes).
    #    Si hay columna de estado, mandan ella; si no, se usa la fecha (edad < buffer_days).
    parsed: list[tuple] = []  # (date, description, signed_amount, bank_account, fingerprint)
    errors = 0
    held_pending = 0
    for r in to_process:
        try:
            d = parse_date(r[i_date])
            description = r[i_desc].strip()
            signed = signed_from_row(r, i_amount, i_debit, i_credit)  # con signo (una col o Debit/Credit)
            raw_account = r[i_account] if i_account is not None else file_account_raw
            account = mask_account(raw_account)
            status = r[i_status] if i_status is not None else None
        except (IndexError, ValueError, TypeError):
            errors += 1
            continue
        held = is_pending(status) if i_status is not None else (today - d).days < buffer_days
        if held:
            held_pending += 1
            continue  # no entra a los libros todavía (base caja)
        fp = make_fingerprint(d, description, signed, account)
        parsed.append((d, description, signed, account, fp))

    core = ingest_parsed(session, company_id, sector, parsed, categorize=categorize,
                         dry_run=dry_run, ai_limit=ai_limit, profile=profile)
    return {
        "inserted": core["inserted"],
        "errors": errors,
        # Para la subida por lotes: total de filas del archivo y cuántas cubrió este lote.
        "total": total,
        "offset": offset,
        "processed": len(to_process),
        "by_rule": core["by_rule"],
        "by_band": 0,  # ya no hay banda de monto; la clave sigue para el frontend (Audit.tsx, api.ts)
        "by_transfer": core["by_transfer"],
        # Transacciones que NO se categorizaron por exceder el cupo de IA (hay que subir de plan).
        "quota_blocked": core["quota_blocked"],
        "by_ai": core["by_ai"],
        # Cuántas filas ajustó el pase de consistencia (mismo comercio → misma categoría).
        "consistency_adjusted": core["consistency_adjusted"],
        "dry_run": dry_run,
        "mapping": mapping.model_dump(),
        "preview": core["preview"],
        # Cuántas filas se omitieron por ya existir (re-subida / rango solapado).
        "skipped_duplicates": core["skipped_duplicates"],
        # Cuántas se retuvieron por estar pending / aún no asentadas (entran en otra subida).
        "held_pending": held_pending,
        # Si la IA no pudo sacar la cuenta del archivo, el frontend la pide al usuario
        # y la aplica con set-account sobre estos IDs.
        "account_detected": core["account_detected"],
        "transaction_ids": core["transaction_ids"],
    }


def settlement_buffer_days(company: "Company | None") -> int:
    """Días de espera del libro: lo más reciente se retiene hasta que el banco lo asienta."""
    if company is not None and company.settlement_buffer_days is not None:
        return company.settlement_buffer_days
    return DEFAULT_SETTLEMENT_BUFFER_DAYS


def ingest_parsed(
    session: Session,
    company_id: int,
    sector: str,
    parsed: list[tuple],
    *,
    categorize: bool = True,
    dry_run: bool = False,
    ai_limit: int | None = None,
    profile: dict | None = None,
) -> dict:
    """EL MOTOR DE ENTRADA, común a toda fuente de movimientos (CSV/Excel y Teller, 3-oct-2026).

    Recibe filas ya parseadas y YA asentadas — `(fecha, descripción, monto CON signo, cuenta
    enmascarada, huella)` y, si vienen de un banco vinculado, un 6º elemento: el `transaction_id` de
    Plaid —; quien llama retiene lo pendiente y lo demasiado reciente — y hace lo mismo
    para todas: deduplicación por huella → regla → transferencia → (IA apagada) → Pendiente, y crea
    las transacciones. Así un movimiento de Teller y uno de un CSV pasan exactamente por el mismo
    camino."""
    # 1b) DEDUPLICACIÓN por huella (multiconjunto): omite las filas que YA existen en la BD de
    #     esta empresa, para no duplicar al re-subir el mismo archivo o un rango solapado.
    #     Se hace por CONTEO: si el archivo trae 2 movimientos idénticos y la BD ya tiene 2, se
    #     omiten los 2; si tiene 0, se insertan los 2 → conserva repeticiones legítimas.
    #     Va ANTES de reglas/IA para no gastar cuota de Gemini en lo que no se va a insertar.
    skipped_duplicates = 0
    # 1a) Filas de un BANCO VINCULADO (traen el id de Plaid): se deduplican EXACTO por ese id, nunca
    #     por huella — dos cafés iguales el mismo día son dos ids distintos (4-oct-2026).
    if not dry_run and any(len(row) > 5 and row[5] for row in parsed):
        ids = {row[5] for row in parsed if len(row) > 5 and row[5]}
        known = set(session.exec(
            select(Transaction.plaid_transaction_id)
            .where(Transaction.company_id == company_id)
            .where(Transaction.plaid_transaction_id.in_(ids))
        ).all())
        kept_ids: list[tuple] = []
        for row in parsed:
            pid = row[5] if len(row) > 5 else None
            if pid and pid in known:
                skipped_duplicates += 1
                continue
            if pid:
                known.add(pid)  # también un id repetido dentro del mismo lote
            kept_ids.append(row)
        parsed = kept_ids
    # 1b) Las demás (CSV): por huella.
    if not dry_run and parsed:
        fps = [row[4] for row in parsed if not (len(row) > 5 and row[5])]
        existing: dict[str, int] = dict(
            session.exec(
                select(Transaction.fingerprint, func.count())
                .where(Transaction.company_id == company_id)
                .where(Transaction.fingerprint.in_(set(fps)))
                .group_by(Transaction.fingerprint)
            ).all()
        )
        remaining_skip = Counter(existing)  # ocurrencias de cada huella ya guardadas (a omitir)
        kept: list[tuple] = []
        for row in parsed:
            fp = row[4]
            if len(row) > 5 and row[5]:
                kept.append(row)  # ya deduplicada por su id
                continue
            if remaining_skip.get(fp, 0) > 0:
                remaining_skip[fp] -= 1
                skipped_duplicates += 1
            else:
                kept.append(row)
        parsed = kept

    # 2) MOTOR DE REGLAS: lo que tiene regla (comercio conocido) se resuelve SIN IA.
    #    Precedencia: regla explícita de empresa → transferencia entre cuentas → Pendiente.
    #    (Sin categorizar por monto: decisión de Jose, 1-oct-2026. El criterio del bookkeeper sobre
    #    montos pasa a las notas de la pestaña Auditoría, que reportan y nunca categorizan.)
    rules: list[CategoryRule] = load_rules(session, company_id, sector) if categorize else []
    rule_hits: dict[int, CategoryRule] = {}
    transfer_hits: dict[int, tuple] = {}  # idx -> (account_type, category) — transferencia entre cuentas
    ai_indices: list[int] = []
    if categorize:
        for idx, row in enumerate(parsed):
            desc = row[1]
            rule = match_rule(rules, desc)
            if rule is not None:
                rule_hits[idx] = rule
                continue
            transfer = classify_transfer(desc)  # transferencia entre cuentas propias → NON_PL
            if transfer is not None:
                transfer_hits[idx] = transfer
                continue
            ai_indices.append(idx)

    # La IA no categoriza (ver AI_CATEGORIZES arriba): lo que ninguna regla cubrió queda PENDIENTE.
    if not AI_CATEGORIZES:
        ai_indices = []

    # Cupo de IA: lo que excede el `ai_limit` NO se categoriza con IA (se inserta sin categorizar).
    # El usuario debe suscribirse/subir de tier para cubrirlas. `quota_blocked` = cuántas se bloquearon.
    quota_blocked = 0
    if ai_limit is not None and len(ai_indices) > max(0, ai_limit):
        keep = max(0, ai_limit)
        quota_blocked = len(ai_indices) - keep
        ai_indices = ai_indices[:keep]

    # 3) Categorizar con la IA SOLO lo que no matcheó regla, LÍNEA POR LÍNEA (una petición por
    #    transacción), en PARALELO con un límite de concurrencia. Cada llamada es independiente →
    #    no hay forma de desalinear resultados. Si una falla tras los reintentos, queda sin
    #    categorizar (no rompe la subida; aparecerá para revisión).
    ai_cats: dict[int, object] = {}
    profile_facts = format_profile_facts(profile)  # hechos de la entrevista → prompt

    def _categorize_one(idx: int):
        try:
            cat = categorize_transaction(parsed[idx][1], float(parsed[idx][2]), sector, profile_facts)
            return idx, cat
        except Exception:
            return idx, None

    if ai_indices:
        with ThreadPoolExecutor(max_workers=AI_CONCURRENCY) as pool:
            for idx, cat in pool.map(_categorize_one, ai_indices):
                if cat is not None:
                    ai_cats[idx] = cat

    # 3b) CONSISTENCIA intra-subida: el mismo comercio → la misma categorización (la de mayor
    #     confianza) en todo el archivo. Corrige la incoherencia de categorizar fila por fila.
    consistency_adjusted = unify_merchant_categorization(parsed, ai_cats) if ai_cats else 0

    # 4) Crear las transacciones (monto = magnitud positiva).
    inserted = 0
    by_rule = 0
    by_transfer = 0
    by_ai = 0
    preview: list[dict] = []
    created: list[Transaction] = []
    any_account = False
    for idx, row in enumerate(parsed):
        d, description, signed, account, fp = row[:5]
        tx = Transaction(company_id=company_id, date=d, description=description,
                         amount=abs(signed), bank_account=account, fingerprint=fp,
                         is_inflow=signed > 0,  # dirección real del banco (entró/salió)
                         check_number=check_number(description),  # número de cheque (si lo es)
                         plaid_transaction_id=(row[5] if len(row) > 5 else None) or None)
        if account:
            any_account = True
        source = _apply_categorization(tx, idx, description, rule_hits, transfer_hits, ai_cats)
        if source == "rule":
            by_rule += 1
        elif source == "transfer":
            by_transfer += 1
        elif source == "ai":
            by_ai += 1

        if source is not None:
            preview.append({
                "date": str(d), "description": description, "amount": str(abs(signed)),
                "bank_account": account,
                "source": source,
                "account_type": tx.account_type.value if tx.account_type else None,
                "category": tx.category, "subcategory": tx.subcategory,
                "confidence": tx.confidence, "needs_review": tx.needs_review,
                "review_question": tx.review_question,
            })
        session.add(tx)
        created.append(tx)
        inserted += 1

    if dry_run:
        session.rollback()  # no persiste: solo prueba el pipeline
        transaction_ids: list[int] = []
    else:
        session.flush()  # asigna los IDs antes de cerrar la transacción
        transaction_ids = [tx.id for tx in created]
        session.commit()

    return {
        "inserted": inserted,
        "by_rule": by_rule,
        "by_transfer": by_transfer,
        "by_ai": by_ai,
        "quota_blocked": quota_blocked,
        "consistency_adjusted": consistency_adjusted,
        "preview": preview,
        "skipped_duplicates": skipped_duplicates,
        "account_detected": any_account,
        "transaction_ids": transaction_ids,
    }


def categorize_pending(session: Session, company: Company, ai_limit: "int | None" = None) -> dict:
    """Re-corre el MOTOR sobre las transacciones SIN categorizar de la empresa — las que quedaron
    fuera por el cupo de IA (`quota_blocked`) o porque la IA falló. Es la otra mitad de la promesa
    "sube de plan para categorizarlas": tras pagar (o al liberarse cupo el mes siguiente), esto las
    resuelve SIN re-subir el archivo. Mismo orden y semántica que la subida: regla →
    transferencia → IA (hasta `ai_limit`, hoy apagada); congela `ai_original_*`."""
    sector = company.sector
    txs = list(session.exec(
        select(Transaction)
        .where(Transaction.company_id == company.id)
        .where(Transaction.account_type == None)  # noqa: E711 — SQL "IS NULL"
        .order_by(Transaction.date, Transaction.id)
    ).all())
    result = {"pending": len(txs), "by_rule": 0, "by_band": 0, "by_transfer": 0, "by_ai": 0,
              "quota_blocked": 0, "consistency_adjusted": 0, "still_uncategorized": 0}
    if not txs:
        return result

    # La misma forma (fecha, descripción, monto CON signo, cuenta, huella) que usa el pipeline.
    parsed = [
        (tx.date, tx.description,
         Decimal(tx.amount) if tx.is_inflow else -Decimal(tx.amount),
         tx.bank_account, tx.fingerprint)
        for tx in txs
    ]

    rules = load_rules(session, company.id, sector)
    rule_hits: dict[int, CategoryRule] = {}
    transfer_hits: dict[int, tuple] = {}
    ai_indices: list[int] = []
    for idx, (_, desc, signed, _account, _fp) in enumerate(parsed):
        rule = match_rule(rules, desc)
        if rule is not None:
            rule_hits[idx] = rule
            continue
        transfer = classify_transfer(desc)
        if transfer is not None:
            transfer_hits[idx] = transfer
            continue
        ai_indices.append(idx)

    # Cupo de IA: igual que en la subida — lo que excede queda sin categorizar (otro intento
    # cuando haya cupo; el saldo lo reporta `quota_blocked`).
    if ai_limit is not None and len(ai_indices) > max(0, ai_limit):
        keep = max(0, ai_limit)
        result["quota_blocked"] = len(ai_indices) - keep
        ai_indices = ai_indices[:keep]

    profile_facts = format_profile_facts(company.profile)

    def _categorize_one(idx: int):
        try:
            return idx, categorize_transaction(parsed[idx][1], float(parsed[idx][2]),
                                               sector, profile_facts)
        except Exception:
            return idx, None

    ai_cats: dict[int, object] = {}
    if ai_indices and AI_CATEGORIZES:   # la IA no categoriza: quedan pendientes
        with ThreadPoolExecutor(max_workers=AI_CONCURRENCY) as pool:
            for idx, cat in pool.map(_categorize_one, ai_indices):
                if cat is not None:
                    ai_cats[idx] = cat

    result["consistency_adjusted"] = unify_merchant_categorization(parsed, ai_cats) if ai_cats else 0

    for idx, tx in enumerate(txs):
        source = _apply_categorization(tx, idx, parsed[idx][1], rule_hits, transfer_hits, ai_cats)
        if source is not None:
            result[f"by_{source}"] += 1
            session.add(tx)
    session.commit()
    done = result["by_rule"] + result["by_band"] + result["by_transfer"] + result["by_ai"]
    result["still_uncategorized"] = len(txs) - done
    return result


if __name__ == "__main__":
    # Prueba rápida (dry-run, no persiste):  python ingestion.py sample_bank.csv
    import json
    import sys

    from db import engine

    # Uso:  python ingestion.py <archivo> [limite] [sector]
    path = sys.argv[1] if len(sys.argv) > 1 else "sample_bank.csv"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    sector = sys.argv[3] if len(sys.argv) > 3 else "construction"

    with open(path, "rb") as fh:
        content = fh.read()

    with Session(engine) as session:
        result = ingest_csv(
            session, company_id=1, sector=sector, content=content,
            limit=limit, dry_run=True,
        )
    print(json.dumps(result, indent=2, ensure_ascii=False))
