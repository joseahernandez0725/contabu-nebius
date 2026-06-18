"""
ContaBu — pipeline de ingestión de archivos bancarios.

CSV bancario desordenado → (IA mapea columnas) → parsea cada fila → (reglas → IA categoriza
lo que no matchea) → crea Transaction. Pensado para datos reales (formatos variados).

Convención: `amount` se guarda como MAGNITUD positiva; el signo (dirección entrada/salida)
se le pasa a la IA para que clasifique bien.
"""

import csv
import io
from datetime import date
from decimal import Decimal, InvalidOperation

from dateutil import parser as dateparser
from sqlmodel import Session

from ai import categorize_batch, map_columns
from models import CategoryRule, Company, Transaction
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


def parse_date(raw: str) -> date:
    """Fecha en formato variado de banco → date (asume mes primero, formato US)."""
    return dateparser.parse(raw.strip(), dayfirst=False).date()


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

    # 1) La IA descubre qué columna es cuál (con una fila de muestra).
    mapping = map_columns(headers, rows[0])
    try:
        i_date = headers.index(mapping.date_column)
        i_desc = headers.index(mapping.description_column)
        i_amount = headers.index(mapping.amount_column)
    except ValueError:
        return {"inserted": 0, "errors": 0, "mapping": mapping.model_dump(),
                "message": "la IA devolvió una columna que no existe en el archivo"}

    to_process = rows[:limit] if limit else rows

    # 1) Parsear todas las filas (fecha, descripción, monto con signo).
    parsed: list[tuple] = []  # (date, description, signed_amount)
    errors = 0
    for r in to_process:
        try:
            d = parse_date(r[i_date])
            description = r[i_desc].strip()
            signed = parse_amount(r[i_amount])  # con signo (dirección)
        except (IndexError, ValueError, TypeError):
            errors += 1
            continue
        parsed.append((d, description, signed))

    # 2) MOTOR DE REGLAS: lo que ya tiene regla (comercio conocido) o cae en una banda de monto
    #    (super/gasolinera) se resuelve SIN IA. Solo lo que no matchea nada va al batch de la IA.
    #    Precedencia: regla explícita de empresa → banda de monto → IA.
    rules: list[CategoryRule] = load_rules(session, company_id, sector) if categorize else []
    company = session.get(Company, company_id) if categorize else None
    super_thr = company.supermarket_threshold if company else None
    gas_thr = company.gas_station_threshold if company else None
    rule_hits: dict[int, CategoryRule] = {}
    band_hits: dict[int, tuple] = {}  # idx -> (account_type, category)
    ai_indices: list[int] = []
    if categorize:
        for idx, (_, desc, signed) in enumerate(parsed):
            rule = match_rule(rules, desc)
            if rule is not None:
                rule_hits[idx] = rule
                continue
            band = amount_band_category(desc, float(signed), super_thr, gas_thr)
            if band is not None:
                band_hits[idx] = band
                continue
            ai_indices.append(idx)

    # 3) Categorizar con la IA SOLO lo que no matcheó regla (batch, eficiente).
    ai_cats: dict[int, object] = {}
    if ai_indices:
        items = [{"description": parsed[i][1], "amount": float(parsed[i][2])} for i in ai_indices]
        results = categorize_batch(items, sector)
        ai_cats = {i: cat for i, cat in zip(ai_indices, results)}

    # 4) Crear las transacciones (monto = magnitud positiva).
    inserted = 0
    by_rule = 0
    by_band = 0
    by_ai = 0
    preview: list[dict] = []
    for idx, (d, description, signed) in enumerate(parsed):
        tx = Transaction(company_id=company_id, date=d, description=description, amount=abs(signed))
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
                "source": source,
                "account_type": tx.account_type.value if tx.account_type else None,
                "category": tx.category, "subcategory": tx.subcategory,
                "confidence": tx.confidence, "needs_review": tx.needs_review,
                "review_question": tx.review_question,
            })
        session.add(tx)
        inserted += 1

    if dry_run:
        session.rollback()  # no persiste: solo prueba el pipeline
    else:
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
