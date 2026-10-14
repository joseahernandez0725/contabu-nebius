"""
ContaBu — la IA para IMPORTAR archivos (FASE 11, Auditoría / IA). Desde el 13-oct-2026 corre sobre
**Nemotron en Nebius Token Factory** (`nemotron.py`), como el resto de la IA de ContaBu. Antes, Gemini.

Dos capacidades, con JSON estricto:
- `map_columns()`: qué columna de un extracto bancario es la fecha, la descripción, el monto (o
  Debit/Credit), la cuenta y el estado.
- `map_rule_columns()`: lo mismo para un archivo de REGLAS que el bookkeeper importa.

`propose_rules()` (el viejo proponedor de reglas) se BORRÓ el 13-oct: su pantalla ya no la montaba
nadie y el agente ④ redactor (`agents.draft`) hace ese trabajo, con validador y prueba contra la historia.

🔒 Privacidad (la regla del 8-oct para todo lo que va a Nebius: nunca descripciones completas, montos
ni nombres de personas). Hasta el 13-oct se mandaban las primeras filas del archivo TAL CUAL. Ahora:
1. **Primero sin IA:** si los encabezados son los de siempre (Date, Description, Amount, Debit/Credit…),
   el mapeo lo hace el código, gratis.
2. **Si no**, al modelo van los ENCABEZADOS y un PERFIL de cada columna que calcula el código ("fecha en
   3 de 3", "número con signo", "texto de ~40 caracteres", "parece categoría contable"), nunca los
   valores. Los encabezados son nombres de columna, no datos del cliente.
3. El mapeo se recuerda por libro y encabezados: la subida por lotes llama una vez por lote y no paga
   cada vez.

La IA no categoriza ni calcula: solo dice qué columna es cuál. Cada llamada queda en `agentcall`.
"""

import logging
import re
from typing import Optional

from pydantic import BaseModel
from sqlmodel import Session

import nemotron
from models import Company

logger = logging.getLogger("contabu.ai")


class ColumnMapping(BaseModel):
    """Qué encabezado del archivo corresponde a cada campo que necesitamos ("" = no existe).

    `account_column`: encabezado con la cuenta por fila (exports de agregadores). `account_label`: el
    nombre de la cuenta de todo el archivo, SOLO si sale de los encabezados (ya no se leen filas).
    """
    date_column: str
    description_column: str
    amount_column: str = ""      # monto con signo en UNA columna; "" si el banco usa Debit/Credit
    debit_column: str = ""
    credit_column: str = ""
    account_column: str = ""
    account_label: str = ""
    status_column: str = ""      # pending/posted, si el archivo la trae


class RuleColumnMapping(BaseModel):
    """Mapea las columnas de un archivo de REGLAS (keyword → categoría) a nuestros campos."""
    keyword_column: str
    category_column: str
    account_type_column: str = ""
    subcategory_column: str = ""


# ── El perfil de una columna (lo ÚNICO que ve el modelo además del encabezado) ─────────────────

_DATE_RE = re.compile(r"^\s*(\d{1,4}[/-]\d{1,2}[/-]\d{1,4}|\d{4}-\d{2}-\d{2}|[A-Za-z]{3,9}\.? \d{1,2},? \d{2,4})")
_NUM_RE = re.compile(r"^\s*\(?-?\$?\s*-?[\d,]+(\.\d+)?\)?\s*$")
_ACCOUNT_RE = re.compile(r"(\*{2,}|X{2,}|x{2,})\s*\d{3,4}|\b(checking|savings|credit card)\b", re.IGNORECASE)
_STATUS_WORDS = {"pending", "posted", "cleared", "settled", "completed", "processing"}
_SECTION_WORDS = {"revenue", "income", "cogs", "expense", "operating_expense", "operating expense", "opex",
                  "other_income", "other_expense", "non_pl", "transfer", "cost of goods sold"}


def column_profile(headers: list[str], rows: list[list[str]],
                   known_categories: Optional[set[str]] = None) -> list[dict]:
    """Para cada columna, conteos y formas — calculados aquí, sin copiar ningún valor."""
    out = []
    for i, h in enumerate(headers):
        vals = [(r[i] if i < len(r) else "").strip() for r in rows]
        filled = [v for v in vals if v]
        nums = [v for v in filled if _NUM_RE.match(v)]
        neg = sum(1 for v in nums if v.startswith("-") or v.startswith("(") or "-" in v[:3])
        prof = {
            "header": h, "rows": len(vals), "filled": len(filled),
            "date_like": sum(1 for v in filled if _DATE_RE.match(v)),
            "number_like": len(nums), "negative_numbers": neg, "positive_numbers": len(nums) - neg,
            "account_like": sum(1 for v in filled if _ACCOUNT_RE.search(v)),
            "status_like": sum(1 for v in filled if v.lower() in _STATUS_WORDS),
            "avg_text_length": round(sum(len(v) for v in filled) / len(filled)) if filled else 0,
            "distinct_values": len(set(filled)),
        }
        if known_categories is not None:
            prof["matches_known_category"] = sum(1 for v in filled if v.lower() in known_categories)
            prof["matches_pl_section"] = sum(1 for v in filled if v.lower() in _SECTION_WORDS)
        out.append(prof)
    return out


# ── 1. Sin IA, cuando los encabezados son los de siempre ───────────────────────────────────────

_H = {
    "date": ("date", "posting date", "transaction date", "trans date", "post date", "posted date", "fecha"),
    "description": ("description", "memo", "payee", "transaction description", "descripción", "descripcion",
                    "concepto"),
    "amount": ("amount", "transaction amount", "monto", "importe"),
    "debit": ("debit", "debits", "withdrawal", "withdrawals", "cargo", "cargos"),
    "credit": ("credit", "credits", "deposit", "deposits", "abono", "abonos"),
    "account": ("account", "account name", "account number", "cuenta"),
    "status": ("status", "estado"),
}


def _by_name(headers: list[str], profile: list[dict]) -> Optional[ColumnMapping]:
    """El mapeo determinista: exige UNA fecha (que de verdad tenga fechas), UNA descripción y un monto
    (o Debit/Credit) por nombre. Si algo falta o es ambiguo, devuelve None y decide el modelo."""
    low = {h: h.strip().lower() for h in headers}
    prof = {p["header"]: p for p in profile}

    def pick(kind: str, need=None) -> Optional[str]:
        hits = [h for h in headers if low[h] in _H[kind] and (need is None or need(prof[h]))]
        return hits[0] if len(hits) == 1 else None

    date = pick("date", lambda p: p["date_like"] >= max(1, p["filled"] // 2))
    desc = pick("description", lambda p: p["filled"] > 0)
    amount = pick("amount", lambda p: p["number_like"] >= max(1, p["filled"] // 2))
    debit, credit = pick("debit"), pick("credit")
    if not date or not desc or not (amount or (debit and credit)):
        return None
    return ColumnMapping(date_column=date, description_column=desc, amount_column=amount or "",
                         debit_column="" if amount else debit or "", credit_column="" if amount else credit or "",
                         account_column=pick("account") or "", status_column=pick("status") or "")


# ── 2. Con Nemotron, cuando no ──────────────────────────────────────────────────────────────────

_CACHE: dict[tuple, BaseModel] = {}   # (libro, tipo, encabezados) → mapeo: un lote no vuelve a pagar


def _ask(kind: str, system: str, user: str, schema: type[BaseModel], headers: list[str],
         session: Optional[Session], company: Optional[Company]) -> dict:
    """Una llamada a Nemotron, medida en `agentcall` cuando hay libro. Devuelve el JSON (sin validar)."""
    import agent_calls
    try:
        reply = nemotron.chat_json(system, user, schema, max_tokens=600)
    except nemotron.NemotronError as e:
        if session is not None and company is not None:
            agent_calls.record(session, company, kind, e.usage, outcome=e.code, model=nemotron.model_name())
            session.commit()
        raise
    if session is not None and company is not None:
        agent_calls.record(session, company, kind, reply.usage, items_out=len(headers))
        session.commit()
    logger.info("%s | %d encabezados · %d/%d tokens · $%s", kind, len(headers), reply.usage.prompt_tokens,
                reply.usage.completion_tokens, f"{reply.usage.cost_usd:.6f}" if reply.usage.cost_usd else "?")
    return reply.data if isinstance(reply.data, dict) else {}


def _only_headers(data: dict, headers: list[str], fields: list[str]) -> dict:
    """El modelo solo puede devolver encabezados que EXISTEN; cualquier otra cosa queda en "" (y quien
    llama ya maneja "no se encontró la columna")."""
    return {f: (data.get(f) if data.get(f) in headers else "") for f in fields}


_COLUMNS_SYSTEM = """You map the columns of a bank statement export. You never see the data: only each
column's header and a profile computed from the first rows (how many look like dates, numbers, negative
numbers, account masks, statuses; average text length; distinct values).

Return ONLY JSON with exactly these fields, each the EXACT header name or "":
- date_column: the transaction date.
- description_column: the merchant/description text (usually the longest text column).
- amount_column: ONE signed amount column (has both negative and positive numbers). If the bank uses two
  columns instead (money out / money in), leave it "" and fill debit_column and credit_column.
- debit_column / credit_column: money out / money in, only when there is no single signed amount column.
- account_column: a column that identifies the bank account per row (account masks), else "".
- account_label: a short account name ONLY if a header itself names the account (e.g. "Checking 4471"),
  else "". Never guess.
- status_column: a column with statuses like pending/posted, else "".
A running balance column is NOT the amount."""

_RULES_SYSTEM = """You map the columns of a bookkeeper's RULES file (a list of keyword -> category). You never
see the data: only each column's header and a profile computed from the first rows (average text length,
distinct values, how many values match a known chart-of-accounts category or a P&L section name).

Return ONLY JSON with exactly these fields, each the EXACT header name or "":
- keyword_column: the merchant / keyword / pattern to match in bank descriptions (required).
- category_column: the bookkeeping category (usually the column with most matches_known_category) (required).
- account_type_column: the P&L section (matches_pl_section), else "".
- subcategory_column: a payee / sub-detail column, else "". Never guess."""


def map_columns(headers: list[str], sample_rows: list[list[str]], *, session: Optional[Session] = None,
                company: Optional[Company] = None) -> ColumnMapping:
    """Qué columna es cuál en un extracto bancario. Sin IA si los encabezados son los de siempre."""
    profile = column_profile(headers, sample_rows)
    by_name = _by_name(headers, profile)
    if by_name is not None:
        logger.info("map_columns | por nombre, sin IA: %s", by_name.model_dump())
        return by_name
    key = (company.id if company else None, "columns", tuple(headers))
    if key in _CACHE:
        return _CACHE[key]
    import json
    data = _ask("column_mapper", _COLUMNS_SYSTEM,
                f"Columns (header + profile of the first {len(sample_rows)} rows):\n"
                f"{json.dumps(profile, ensure_ascii=False)}", ColumnMapping, headers, session, company)
    picked = _only_headers(data, headers, ["date_column", "description_column", "amount_column", "debit_column",
                                           "credit_column", "account_column", "status_column"])
    label = data.get("account_label") if isinstance(data.get("account_label"), str) else ""
    # La etiqueta solo vale si sale de un ENCABEZADO (no hay filas en el prompt): si no, se descarta.
    label = label if label and any(label.lower() in h.lower() or h.lower() in label.lower() for h in headers) else ""
    mapping = ColumnMapping(**picked, account_label=label[:60])
    _CACHE[key] = mapping
    return mapping


def map_rule_columns(headers: list[str], sample_rows: list[list[str]], *, session: Optional[Session] = None,
                     company: Optional[Company] = None,
                     known_categories: Optional[set[str]] = None) -> RuleColumnMapping:
    """Qué columna es cuál en un archivo de REGLAS. `known_categories`: el plan de cuentas del libro, en
    minúsculas, para que el perfil diga qué columna parece de categorías."""
    profile = column_profile(headers, sample_rows, known_categories or set())
    key = (company.id if company else None, "rules", tuple(headers))
    if key in _CACHE:
        return _CACHE[key]
    import json
    data = _ask("rule_column_mapper", _RULES_SYSTEM,
                f"Columns (header + profile of the first {len(sample_rows)} rows):\n"
                f"{json.dumps(profile, ensure_ascii=False)}", RuleColumnMapping, headers, session, company)
    mapping = RuleColumnMapping(**_only_headers(data, headers, ["keyword_column", "category_column",
                                                                "account_type_column", "subcategory_column"]))
    _CACHE[key] = mapping
    return mapping
