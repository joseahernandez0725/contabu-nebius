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
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from dateutil import parser as dateparser
from sqlalchemy import func
from sqlmodel import Session, select

from ai import categorize_batch, map_columns
from models import CategoryRule, Company, Transaction

# Ventana de asentamiento por defecto (días): cubre tarjeta/ACH normales (1-3 días hábiles) + un
# fin de semana. Se retienen los movimientos recientes/pending hasta que liquidan (base caja).
DEFAULT_SETTLEMENT_BUFFER_DAYS = 5

# Tamaño de lote para categorizar con la IA: cada lote es UNA petición a Gemini. Se trocea para
# que el JSON de salida no se trunque ni degrade en archivos grandes. Ver docs/FASE 6.E.md.
AI_BATCH_SIZE = 50

# Palabras que indican que un movimiento aún NO liquidó (no debe entrar a los libros todavía).
_PENDING_WORDS = ("pending", "hold", "authoriz", "processing", "unposted", "in process")


def is_pending(status: str | None) -> bool:
    """True si el valor de la columna de estado indica que el movimiento aún no liquidó."""
    if not status:
        return False
    s = status.strip().lower()
    return any(w in s for w in _PENDING_WORDS)
from rules import amount_band_category, load_rules, match_rule


def parse_csv(content: bytes) -> tuple[list[str], list[list[str]]]:
    """Bytes de CSV → (headers, filas). Ignora líneas vacías. Maneja BOM de Excel."""
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any(cell.strip() for cell in r)]
    if not rows:
        return [], []
    return rows[0], rows[1:]


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


def ingest_csv(
    session: Session,
    company_id: int,
    sector: str,
    content: bytes,
    *,
    categorize: bool = True,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict:
    """Pipeline completo. Devuelve un resumen (cuántas filas, el mapeo, errores)."""
    headers, rows = parse_csv(content)
    if not rows:
        return {"inserted": 0, "errors": 0, "mapping": None, "message": "archivo vacío"}

    # 1) La IA descubre qué columna es cuál (con varias filas de muestra) y, si puede,
    #    detecta la cuenta bancaria de origen (columna por fila o etiqueta de todo el archivo).
    mapping = map_columns(headers, rows[:3])
    try:
        i_date = headers.index(mapping.date_column)
        i_desc = headers.index(mapping.description_column)
        i_amount = headers.index(mapping.amount_column)
    except ValueError:
        return {"inserted": 0, "errors": 0, "mapping": mapping.model_dump(),
                "message": "la IA devolvió una columna que no existe en el archivo"}

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
    buffer_days = company.settlement_buffer_days if company and company.settlement_buffer_days is not None \
        else DEFAULT_SETTLEMENT_BUFFER_DAYS
    today = datetime.now(timezone.utc).date()

    to_process = rows[:limit] if limit else rows

    # 1) Parsear las filas y RETENER las que aún no liquidaron (pending o demasiado recientes).
    #    Si hay columna de estado, mandan ella; si no, se usa la fecha (edad < buffer_days).
    parsed: list[tuple] = []  # (date, description, signed_amount, bank_account, fingerprint)
    errors = 0
    held_pending = 0
    for r in to_process:
        try:
            d = parse_date(r[i_date])
            description = r[i_desc].strip()
            signed = parse_amount(r[i_amount])  # con signo (dirección)
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

    # 1b) DEDUPLICACIÓN por huella (multiconjunto): omite las filas que YA existen en la BD de
    #     esta empresa, para no duplicar al re-subir el mismo archivo o un rango solapado.
    #     Se hace por CONTEO: si el archivo trae 2 movimientos idénticos y la BD ya tiene 2, se
    #     omiten los 2; si tiene 0, se insertan los 2 → conserva repeticiones legítimas.
    #     Va ANTES de reglas/IA para no gastar cuota de Gemini en lo que no se va a insertar.
    skipped_duplicates = 0
    if not dry_run and parsed:
        fps = [row[4] for row in parsed]
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
            if remaining_skip.get(fp, 0) > 0:
                remaining_skip[fp] -= 1
                skipped_duplicates += 1
            else:
                kept.append(row)
        parsed = kept

    # 2) MOTOR DE REGLAS: lo que ya tiene regla (comercio conocido) o cae en una banda de monto
    #    (super/gasolinera) se resuelve SIN IA. Solo lo que no matchea nada va al batch de la IA.
    #    Precedencia: regla explícita de empresa → banda de monto → IA.
    rules: list[CategoryRule] = load_rules(session, company_id, sector) if categorize else []
    super_thr = company.supermarket_threshold if company else None
    gas_thr = company.gas_station_threshold if company else None
    rule_hits: dict[int, CategoryRule] = {}
    band_hits: dict[int, tuple] = {}  # idx -> (account_type, category)
    ai_indices: list[int] = []
    if categorize:
        for idx, (_, desc, signed, _account, _fp) in enumerate(parsed):
            rule = match_rule(rules, desc)
            if rule is not None:
                rule_hits[idx] = rule
                continue
            band = amount_band_category(desc, float(signed), super_thr, gas_thr)
            if band is not None:
                band_hits[idx] = band
                continue
            ai_indices.append(idx)

    # 3) Categorizar con la IA SOLO lo que no matcheó regla, en LOTES (chunks) de AI_BATCH_SIZE:
    #    cada lote es una sola petición a Gemini → procesa archivos grandes sin truncar el JSON.
    ai_cats: dict[int, object] = {}
    for start in range(0, len(ai_indices), AI_BATCH_SIZE):
        chunk = ai_indices[start:start + AI_BATCH_SIZE]
        items = [{"description": parsed[i][1], "amount": float(parsed[i][2])} for i in chunk]
        results = categorize_batch(items, sector)
        for i, cat in zip(chunk, results):
            ai_cats[i] = cat

    # 4) Crear las transacciones (monto = magnitud positiva).
    inserted = 0
    by_rule = 0
    by_band = 0
    by_ai = 0
    preview: list[dict] = []
    created: list[Transaction] = []
    any_account = False
    for idx, (d, description, signed, account, fp) in enumerate(parsed):
        tx = Transaction(company_id=company_id, date=d, description=description,
                         amount=abs(signed), bank_account=account, fingerprint=fp)
        if account:
            any_account = True
        source = None
        if idx in rule_hits:
            rule = rule_hits[idx]
            tx.account_type = rule.account_type
            tx.category = rule.category
            tx.subcategory = rule.match_text.upper()  # el comercio/patrón que disparó la regla
            tx.confidence = 1.0
            tx.needs_review = False
            tx.review_question = None
            tx.ai_categorized = False  # vino de una regla, no de la IA
            tx.source = "rule"
            source = "rule"
            by_rule += 1
        elif idx in band_hits:
            at, cat = band_hits[idx]
            tx.account_type = at
            tx.category = cat
            tx.confidence = 1.0
            tx.needs_review = False
            tx.review_question = None
            tx.ai_categorized = False  # banda de monto determinista, no IA
            tx.source = "rule"
            source = "rule"
            by_band += 1
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
            source = "ai"
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
        "errors": errors,
        "by_rule": by_rule,
        "by_band": by_band,
        "by_ai": by_ai,
        "dry_run": dry_run,
        "mapping": mapping.model_dump(),
        "preview": preview,
        # Cuántas filas se omitieron por ya existir (re-subida / rango solapado).
        "skipped_duplicates": skipped_duplicates,
        # Cuántas se retuvieron por estar pending / aún no asentadas (entran en otra subida).
        "held_pending": held_pending,
        # Si la IA no pudo sacar la cuenta del archivo, el frontend la pide al usuario
        # y la aplica con set-account sobre estos IDs.
        "account_detected": any_account,
        "transaction_ids": transaction_ids,
    }


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
