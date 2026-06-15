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

from models import AccountType  # enum estándar de secciones del P&L (fuente única)

load_dotenv()

logger = logging.getLogger("contabu.ai")

MODEL = "gemini-2.5-flash"
_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# Códigos HTTP transitorios que vale la pena reintentar (saturación, límites temporales).
_RETRY_CODES = {429, 500, 502, 503, 504}


class Categorization(BaseModel):
    """Resultado de categorizar una transacción: los 3 niveles del plan de cuentas (en inglés)."""
    account_type: AccountType  # sección del P&L: REVENUE/COGS/OPERATING_EXPENSE/OTHER_INCOME/OTHER_EXPENSE
    category: str              # cuenta contable, p. ej. "Materials"
    subcategory: str           # nombre del comercio/empresa, p. ej. "Home Depot"
    confidence: float          # 0.0 - 1.0


class ColumnMapping(BaseModel):
    """Qué encabezado del archivo corresponde a cada campo que necesitamos."""
    date_column: str
    description_column: str
    amount_column: str


def _generate(prompt: str, schema: type[BaseModel], *, max_retries: int = 4):
    """Llama a Gemini con structured output. Reintenta ante errores transitorios (backoff)."""
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
- account_type: the income-statement section. Exactly one of:
  REVENUE, COGS, OPERATING_EXPENSE, OTHER_INCOME, OTHER_EXPENSE.
  (COGS = direct costs of producing the product/service; OPERATING_EXPENSE = overhead.)
- category: the specific accounting account, e.g. "Materials", "Labor",
  "Subcontractors", "Rent", "Utilities", "Marketing".
- subcategory: the merchant or company name in the transaction, e.g. "Home Depot", "Shell".
- confidence: 0.0 to 1.0.

Transaction description: "{description}"
Amount (USD, negative = expense): {amount}"""

    response = _generate(prompt, Categorization)
    result = response.parsed
    # Log de la decisión del agente (entrada -> salida): evidencia para el concurso.
    logger.info(
        "categorize | sector=%s desc=%r amount=%s -> type=%s category=%r subcategory=%r confidence=%s",
        sector, description, amount, result.account_type.value, result.category,
        result.subcategory, result.confidence,
    )
    return result


def map_columns(headers: list[str], sample_row: list[str]) -> ColumnMapping:
    """Mapea encabezados de un CSV bancario desordenado a date/description/amount."""
    prompt = f"""A user uploaded a messy bank CSV. Map its columns to the fields we need.

Column headers: {headers}
Example data row: {sample_row}

Identify which header holds the transaction date, which holds the description,
and which holds the transaction amount. Return the EXACT header names as given."""

    response = _generate(prompt, ColumnMapping)
    result = response.parsed
    logger.info(
        "map_columns | headers=%s -> date=%r description=%r amount=%r",
        headers, result.date_column, result.description_column, result.amount_column,
    )
    return result


# Prueba rápida al ejecutar el módulo directamente: python ai.py
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    cat = categorize_transaction("HOME DEPOT #2745 BUILDING MATERIALS", -342.18, "construction")
    print("Categorization:", cat)

    mapping = map_columns(
        ["TXN DT", "NARRATIVE", "DR/CR", "VALUE", "RUNNING BAL"],
        ["2026-03-14", "HOME DEPOT #2745 BUILDING MATERIALS", "DR", "-342.18", "1250.00"],
    )
    print("ColumnMapping:", mapping)
