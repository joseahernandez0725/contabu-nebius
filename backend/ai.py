"""
ContaBu — capa de IA (Gemini).

Dos capacidades, ambas con STRUCTURED OUTPUT (JSON estricto vía Pydantic):
- categorize_transaction(): clasifica una transacción (category, subcategory, confidence).
- map_columns(): mapea encabezados desordenados de un CSV a date / description / amount.

Reglas de oro:
- La IA SOLO clasifica/mapea, NUNCA calcula.
- Datos y categorías en INGLÉS.
- Cada llamada se REGISTRA (log) → evidencia de "AI-native operations" para el concurso.
- Reintenta ante errores transitorios (503/429/5xx) → robustez en producción.
"""

import logging
import os
import time

from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from pydantic import BaseModel

from categories import format_for_prompt  # plan de cuentas cerrado por sector
from models import AccountType  # enum estándar de secciones del P&L (fuente única)

load_dotenv()

logger = logging.getLogger("contabu.ai")

MODEL = "gemini-2.5-flash-lite"  # cuota propia + más barato/rápido; suficiente para clasificar
_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# Códigos HTTP transitorios que vale la pena reintentar (saturación, límites temporales).
_RETRY_CODES = {429, 500, 502, 503, 504}


class Categorization(BaseModel):
    """Resultado de categorizar una transacción: los 3 niveles del plan de cuentas (en inglés)."""
    account_type: AccountType  # sección del P&L
    category: str              # cuenta contable, p. ej. "Materials"
    subcategory: str           # nombre del comercio/empresa, p. ej. "Home Depot"
    confidence: float          # 0.0 - 1.0
    needs_review: bool         # true si es ambiguo y necesita confirmación humana
    review_question: str       # pregunta para el usuario si needs_review (si no, "")


class ColumnMapping(BaseModel):
    """Qué encabezado del archivo corresponde a cada campo que necesitamos.

    `account_column` / `account_label` permiten detectar la CUENTA BANCARIA de origen:
    - account_column: encabezado con la cuenta por fila (export de agregadores tipo Plaid/Mint).
      "" si el archivo no trae una columna de cuenta.
    - account_label: nombre/identificador de la cuenta para TODO el archivo, inferido de los
      encabezados o filas de muestra (ej. "Checking #4471"). "" si no se puede determinar.
    """
    date_column: str
    description_column: str
    amount_column: str
    account_column: str = ""
    account_label: str = ""
    # Columna de estado del movimiento (pending/posted), si el archivo la trae. "" si no existe.
    status_column: str = ""


def _generate(prompt: str, schema, *, max_retries: int = 4):
    """Llama a Gemini con structured output (schema puede ser un modelo o una lista de modelos).
    Reintenta ante errores transitorios (backoff)."""
    delay = 2.0
    for attempt in range(1, max_retries + 1):
        try:
            return _client.models.generate_content(
                model=MODEL,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": schema,
                },
            )
        except genai_errors.APIError as e:
            code = getattr(e, "code", None)
            if code not in _RETRY_CODES or attempt == max_retries:
                raise  # error no transitorio, o se agotaron los reintentos
            logger.warning(
                "Gemini %s (intento %d/%d); reintentando en %.0fs", code, attempt, max_retries, delay
            )
            time.sleep(delay)
            delay *= 2  # espera progresiva: 2s, 4s, 8s…


def categorize_transaction(description: str, amount: float, sector: str) -> Categorization:
    """Clasifica una transacción bancaria. Devuelve category/subcategory/confidence."""
    prompt = f"""You categorize bank transactions for small-business bookkeeping using a
3-level chart of accounts. The business operates in the "{sector}" sector.

For the transaction below, return (all in English):
- account_type: exactly one of:
  REVENUE, COGS, OPERATING_EXPENSE, OTHER_INCOME, OTHER_EXPENSE, NON_PL.
  (COGS = direct costs of producing the product/service; OPERATING_EXPENSE = overhead.)
  Use NON_PL when the movement is NOT business income or expense and must be excluded
  from the profit & loss: transfers between the owner's own accounts, owner draws or
  capital contributions, loan principal (received or repaid), and credit-card payments.
- category: choose the BEST-FITTING category from the closed chart of accounts below; it MUST
  belong to the account_type you assigned. If nothing truly fits, use "Other". Do NOT invent names.
- subcategory: the SPECIFIC merchant or company name, e.g. "Home Depot", "Shell".
- confidence: 0.0 to 1.0.

Chart of accounts for the "{sector}" sector (pick category from the matching account_type):
{format_for_prompt(sector)}

Transaction description: "{description}"
Amount (USD, negative = money out / expense): {amount}"""

    response = _generate(prompt, Categorization)
    result = response.parsed
    # Log de la decisión del agente (entrada -> salida): evidencia para el concurso.
    logger.info(
        "categorize | sector=%s desc=%r amount=%s -> type=%s category=%r subcategory=%r confidence=%s",
        sector, description, amount, result.account_type.value, result.category,
        result.subcategory, result.confidence,
    )
    return result


def recategorize_with_answer(description: str, amount: float, sector: str, answer: str) -> Categorization:
    """Re-categoriza usando la EXPLICACIÓN del dueño (lenguaje simple) como señal principal."""
    prompt = f"""Re-categorize a bank transaction for a "{sector}" small business AFTER the owner
explained what it was. Use the owner's explanation as the PRIMARY signal.

Transaction: "{description}" | amount: {amount}
Owner's explanation (plain language): "{answer}"

Choose `category` from the closed chart of accounts below; it MUST belong to the chosen account_type
(if nothing fits, use "Other"):
{format_for_prompt(sector)}

Return account_type, category, subcategory, confidence, needs_review, review_question.
(COGS = direct cost of the product/service sold; OPERATING_EXPENSE = overhead; NON_PL =
transfers between the owner's own accounts, owner draws, loan principal, credit-card payments.)
With the owner's explanation you should normally be confident: set needs_review=false and
review_question="" unless the explanation is still genuinely unclear."""

    response = _generate(prompt, Categorization)
    result = response.parsed
    logger.info(
        "recategorize | desc=%r answer=%r -> type=%s category=%r confidence=%s",
        description, answer, result.account_type.value, result.category, result.confidence,
    )
    return result


def categorize_batch(items: list[dict], sector: str) -> list[Categorization]:
    """Categoriza MUCHAS transacciones en UNA sola llamada (eficiente con el rate limit).

    `items`: lista de {"description": str, "amount": float (con signo: negativo = salida)}.
    Devuelve una lista de Categorization en el MISMO orden.
    """
    lines = "\n".join(
        f'{i + 1}. "{it["description"]}" | amount: {it["amount"]}'
        for i, it in enumerate(items)
    )
    prompt = f"""You categorize bank transactions for small-business bookkeeping using a
3-level chart of accounts. The business operates in the "{sector}" sector.

Categorize EACH transaction below. Return a JSON array with ONE object per transaction,
in the SAME ORDER, each with: account_type, category, subcategory, confidence,
needs_review, review_question.

- account_type: one of REVENUE, COGS, OPERATING_EXPENSE, OTHER_INCOME, OTHER_EXPENSE, NON_PL.
  COGS = direct cost of producing the product/service sold. OPERATING_EXPENSE = overhead.
  NON_PL = transfers between the owner's own accounts, owner draws/contributions, loan
  principal, credit-card payments. NON_PL is NOT a payment to a subcontractor or supplier.
- category: choose the BEST-FITTING category from the closed chart of accounts below; it MUST
  belong to the account_type you assigned. If nothing truly fits, use "Other". Do NOT invent names.
- subcategory: the SPECIFIC merchant or person name in the transaction.
- confidence: 0.0 to 1.0.  (amount: negative = money out / expense)
- needs_review: true when the transaction is genuinely ambiguous and a human should confirm —
  e.g. Zelle / person-to-person transfers (could be a subcontractor, an owner draw, or a
  customer payment), unclear merchants, or whenever your confidence is below 0.8.
- review_question: if needs_review is true, a short, friendly question for the business owner
  that would resolve it (e.g. "Is the $2,080 payment to watson yu a subcontractor or your own
  draw?"). If needs_review is false, return an empty string "".

Chart of accounts for the "{sector}" sector (pick category from the matching account_type):
{format_for_prompt(sector)}

Transactions:
{lines}"""

    response = _generate(prompt, list[Categorization])
    results: list[Categorization] = response.parsed or []
    logger.info("categorize_batch | sector=%s n=%d -> %d resultados", sector, len(items), len(results))
    return results


def map_columns(headers: list[str], sample_rows: list[list[str]]) -> ColumnMapping:
    """Mapea encabezados de un CSV bancario desordenado a date/description/amount y, si es
    posible, detecta la cuenta bancaria de origen (columna por fila o etiqueta de todo el archivo)."""
    sample = "\n".join(str(r) for r in sample_rows)
    prompt = f"""A user uploaded a messy bank CSV. Map its columns to the fields we need.

Column headers: {headers}
Example data rows:
{sample}

Return the EXACT header names as given:
- date_column: header holding the transaction date.
- description_column: header holding the description.
- amount_column: header holding the transaction amount.

Also detect the originating BANK ACCOUNT (a business may have several: checking, savings, cards):
- account_column: if a column identifies the account PER ROW (common in aggregator exports
  like Plaid/Mint), return that EXACT header name. Otherwise return "".
- account_label: if the whole file belongs to ONE account and you can read a name or number
  for it from the headers or sample rows (e.g. "Checking ****4471"), return a short label.
  Otherwise return "". Do NOT guess; return "" when there is no clear account information.
- status_column: if a column holds the transaction STATUS (e.g. "pending" vs "posted/cleared/
  settled"), return that EXACT header name. Otherwise return ""."""

    response = _generate(prompt, ColumnMapping)
    result = response.parsed
    logger.info(
        "map_columns | headers=%s -> date=%r description=%r amount=%r account_column=%r "
        "account_label=%r status_column=%r",
        headers, result.date_column, result.description_column, result.amount_column,
        result.account_column, result.account_label, result.status_column,
    )
    return result


# Prueba rápida al ejecutar el módulo directamente: python ai.py
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    cat = categorize_transaction("HOME DEPOT #2745 BUILDING MATERIALS", -342.18, "construction")
    print("Categorization (expense):", cat)

    transfer = categorize_transaction("ONLINE TRANSFER TO SAVINGS #4471", -500.00, "construction")
    print("Categorization (transfer):", transfer)

    mapping = map_columns(
        ["TXN DT", "NARRATIVE", "DR/CR", "VALUE", "RUNNING BAL"],
        [["2026-03-14", "HOME DEPOT #2745 BUILDING MATERIALS", "DR", "-342.18", "1250.00"]],
    )
    print("ColumnMapping:", mapping)
