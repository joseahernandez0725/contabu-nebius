"""
ContaBu — capa de IA (Gemini).

Tres capacidades, todas con STRUCTURED OUTPUT (JSON estricto vía Pydantic):
- map_columns(): mapea encabezados desordenados de un extracto a date / description / amount.
- map_rule_columns(): lo mismo para un archivo de reglas que se importa.
- propose_rules(): PROPONE reglas que el bookkeeper aprueba (nunca escribe una categoría).

Reglas de oro:
- La IA NO categoriza, por ningún camino (Jose, 1-oct): `categorize_transaction`, `categorize_batch`
  y `recategorize_with_answer` se borraron el 4-oct (deuda #16). Solo mapea y propone; NUNCA calcula.
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
from google.genai import types
from pydantic import BaseModel

from categories import format_for_prompt  # plan de cuentas cerrado por sector
from models import AccountType  # enum estándar de secciones del P&L (fuente única)

load_dotenv()

logger = logging.getLogger("contabu.ai")

# Modelo de Gemini. Configurable por entorno (GEMINI_MODEL) para A/B sin tocar código.
# flash-lite = más barato/rápido; flash = razona mejor en casos difíciles. Ver docs/FASE.
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Backend de Gemini, configurable por entorno (sin tocar código):
# - VERTEX AI (GEMINI_USE_VERTEX=true): factura como servicio de GCP → lo cubre el crédito de $300.
#   Autentica con Application Default Credentials (NO API key): en local `gcloud auth
#   application-default login`; en Cloud Run, la service account con rol `roles/aiplatform.user`.
# - AI STUDIO (por defecto): usa GEMINI_API_KEY; se cobra de los créditos PREPAGADOS de Gemini.
if os.getenv("GEMINI_USE_VERTEX", "").lower() in ("1", "true", "yes"):
    _client = genai.Client(
        vertexai=True,
        project=os.getenv("GOOGLE_CLOUD_PROJECT", "contabu-prod"),
        location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
    )
else:
    _client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# Códigos HTTP transitorios que vale la pena reintentar (saturación, límites temporales).
_RETRY_CODES = {429, 500, 502, 503, 504}


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
    # Monto con signo en UNA columna. "" si el banco usa Debit/Credit separadas (ver abajo).
    amount_column: str = ""
    # Algunos bancos NO traen monto con signo, sino DOS columnas: Debit (salida) y Credit (entrada).
    # Si las hay, devolver sus nombres EXACTOS; si el banco usa una sola columna de monto, dejar "".
    debit_column: str = ""
    credit_column: str = ""
    account_column: str = ""
    account_label: str = ""
    # Columna de estado del movimiento (pending/posted), si el archivo la trae. "" si no existe.
    status_column: str = ""


class RuleColumnMapping(BaseModel):
    """Mapea las columnas de un archivo de REGLAS del usuario (keyword→categoría) a nuestros campos.
    El bookkeeper sube su Excel/CSV **como sea**; la IA identifica qué columna es cada cosa."""
    keyword_column: str            # comercio/keyword/patrón a matchear (requerida)
    category_column: str           # categoría / cuenta contable (requerida)
    account_type_column: str = ""  # SECCIÓN del P&L (REVENUE/COGS/…), "" si no existe
    subcategory_column: str = ""   # beneficiario/subcategoría, "" si no existe


class ProposedRule(BaseModel):
    """Una regla COMPANY que el AGENTE PROPONEDOR sugiere (el usuario la aprueba). El proponedor toma
    los comercios recurrentes sin regla y propone reglas: mergeando variantes de marca, evitando
    duplicar reglas existentes, marcando gas/super como `skip` (los manejan las bandas de monto)."""
    reasoning: str            # 1 frase: por qué / qué candidatos junta
    keyword: str              # match_text específico (puede usar `*` como comodín)
    account_type: AccountType
    category: str
    covers: list[str]         # firmas de candidato que esta regla cubre (auditar el merge)
    kind: str                 # "rule" (proponer regla) | "skip" (gas/super→banda, o ya cubierto)


def _generate(contents, schema, *, model: str = MODEL, max_retries: int = 4):
    """Llama a Gemini con structured output (schema puede ser un modelo o una lista de modelos).
    `contents` puede ser un prompt de texto o una lista multimodal (p. ej. [imagen, texto]).
    `model` permite sobre-escribir el modelo por llamada.
    Reintenta ante errores transitorios (backoff)."""
    delay = 2.0
    for attempt in range(1, max_retries + 1):
        try:
            return _client.models.generate_content(
                model=model,
                contents=contents,
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


def _facts_block(profile_facts: str) -> str:
    """Bloque de HECHOS del dueño (entrevista del bookkeeper, FASE 9 Paso 5) para los prompts.
    Hechos, no heurísticas (idea #5): la IA sigue razonando desde la descripción/monto."""
    if not profile_facts:
        return ""
    return f"""
FACTS from the owner (company profile — treat as ground truth about THIS business):
{profile_facts}
"""


def propose_rules(
    candidates: list[dict], existing_keywords: list[str], sector: str, profile_facts: str = "",
) -> list[ProposedRule]:
    """AGENTE PROPONEDOR (1 llamada): toma los candidatos deterministas (`rules.find_rule_candidates`)
    y devuelve reglas COMPANY propuestas para que el usuario apruebe. Mergea variantes de marca,
    no duplica las reglas existentes, usa `*` para transferencias/ACH, y marca gas/super como `skip`."""
    if not candidates:
        return []
    cand_lines = "\n".join(
        f"- signature={c['signature']} count={c['count']} sign={c['sign']} "
        f"suggested={c['suggested_account_type']}/{c['suggested_category']} examples={c['examples']}"
        for c in candidates
    )
    existing = ", ".join(existing_keywords) or "(none yet)"
    prompt = f"""You are a bookkeeping rule-proposer for a "{sector}" small business. From the recurring
merchants below (each seen >=2 times and NOT yet covered by a rule), propose COMPANY categorization
rules for the owner to approve.
{_facts_block(profile_facts)}

Rules of the game:
- MERGE brand variants into ONE rule. "CHEVRONSAN", "CHEVRONBAY", "CHEVRONCONCORD" are all Chevron ->
  one rule. Drop store numbers and city/state from the keyword.
- keyword construction (how matching works: the keyword must appear as WHOLE words in the bank
  description; `*` means "anything in between, in order"):
  * Anchor on the STABLE words of the description and use `*` to skip the dynamic junk (ref numbers,
    dates, IDs, store numbers). Prefer MORE stable anchor words joined with `*` over one short word:
    "PAYCHEX TPS*TAXES" beats "PAYCHEX"; "ONLINE TRANSFER*TO*XXXX2961" beats "TRANSFER".
  * The keyword MUST match EVERY example description of the candidates it covers — check them one by
    one; if a word is not in all examples, drop it or wildcard it.
  * Use the truncated form exactly as the bank prints it (POS cuts names: "HOBBYLO", not "HOBBYLOBBY").
  * Keep processor prefixes when they identify the merchant ("SQ *JOES" -> "SQ*JOES"); masked accounts
    need the EXACT digits as printed ("XXXX2961" — a wrong X-count makes the keyword dead).
  * Never anchor only on numbers, dates or generic words (PAYMENT, ONLINE, DEBIT): too generic
    keywords catch OTHER merchants (false matches destroy trust).
- account_type: exactly one of REVENUE, COGS, OPERATING_EXPENSE, OTHER_INCOME, OTHER_EXPENSE, NON_PL.
- category: from the closed chart of accounts below; it MUST belong to the account_type. Use the
  suggested category unless it is clearly wrong.
- Do NOT duplicate an existing rule keyword (listed below); if a merchant is already covered or
  conflicts, set kind="skip".
- GAS STATIONS and SUPERMARKETS are handled by amount bands -> set kind="skip" for them.
- NEVER propose a blanket rule for ambiguous PERSON-TO-PERSON payments (Zelle/Venmo/Quickpay/Cash App
  to or from a PERSON): the same channel can be a customer payment, a subcontractor, an owner draw or a
  contribution -> set kind="skip" (those go to human review). Only propose a rule when the counterparty
  is a clear BUSINESS/vendor name (e.g. "Zelle to ABC Plumbing" is fine; "Zelle to/from <person>" is not)
  — EXCEPTION: if the owner's profile facts above CONFIRM that person's role (e.g. a confirmed
  subcontractor or customer), DO propose the rule for that person with the matching category.
- covers: list the candidate signatures this entry merges.

Existing rule keywords (do not duplicate): {existing}

Chart of accounts for "{sector}":
{format_for_prompt(sector)}

Recurring merchants (candidates):
{cand_lines}

Return one object per proposed rule (kind="rule") or per merchant to skip (kind="skip")."""

    response = _generate(prompt, list[ProposedRule])
    result = response.parsed
    logger.info(
        "propose_rules | sector=%s candidates=%d -> proposed=%d",
        sector, len(candidates), len([r for r in result if r.kind == "rule"]),
    )
    return result


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
- amount_column: header holding the SIGNED transaction amount (one column). If the bank instead uses
  TWO separate columns (one for money out, one for money in), leave amount_column "" and fill the next two:
- debit_column: header for money OUT (withdrawals/charges/debits). "" if there is a single amount column.
- credit_column: header for money IN (deposits/credits). "" if there is a single amount column.

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
        "map_columns | headers=%s -> date=%r description=%r amount=%r debit=%r credit=%r "
        "account_column=%r account_label=%r status_column=%r",
        headers, result.date_column, result.description_column, result.amount_column,
        result.debit_column, result.credit_column,
        result.account_column, result.account_label, result.status_column,
    )
    return result


def map_rule_columns(headers: list[str], sample_rows: list[list[str]]) -> RuleColumnMapping:
    """Mapea las columnas de un archivo de REGLAS del usuario (lo sube como sea) a
    keyword / category / (opcional) account_type / subcategory. La IA lee headers + filas de muestra."""
    sample = "\n".join(str(r) for r in sample_rows)
    prompt = f"""A bookkeeper uploaded their existing RULES file: a list mapping a merchant/keyword to a
bookkeeping category. Map its columns to the fields we need.

Column headers: {headers}
Example data rows:
{sample}

Return the EXACT header names as given:
- keyword_column: the column holding the MERCHANT or keyword/pattern to match in bank descriptions
  (e.g. "Home Depot", "SHELL", "ONLINE TRANSFER*TO*XXXX2961"). Required.
- category_column: the column holding the CATEGORY / account name (e.g. "Materials", "Rent"). Required.
- account_type_column: if a column holds the P&L SECTION (REVENUE, COGS, OPERATING_EXPENSE,
  OTHER_INCOME, OTHER_EXPENSE, NON_PL, or words like "income"/"expense"/"COGS"), return that EXACT
  header. Otherwise "".
- subcategory_column: if a column holds a sub-detail / payee, return that EXACT header. Otherwise "".

Do NOT guess; return "" for optional columns that are not clearly present."""

    response = _generate(prompt, RuleColumnMapping)
    result = response.parsed
    logger.info(
        "map_rule_columns | headers=%s -> keyword=%r category=%r account_type=%r subcategory=%r",
        headers, result.keyword_column, result.category_column,
        result.account_type_column, result.subcategory_column,
    )
    return result
