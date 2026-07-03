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
from google.genai import types
from pydantic import BaseModel

from categories import format_for_prompt  # plan de cuentas cerrado por sector
from models import AccountType  # enum estándar de secciones del P&L (fuente única)

load_dotenv()

logger = logging.getLogger("contabu.ai")

# Modelo de Gemini. Configurable por entorno (GEMINI_MODEL) para A/B sin tocar código.
# flash-lite = más barato/rápido; flash = razona mejor en casos difíciles. Ver docs/FASE.
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Modelo para leer RECIBOS por foto (OCR multimodal). Se usa `pro` por defecto: los recibos son de
# bajo volumen (1 llamada por foto) y la lectura de imágenes sucias (papel térmico borroso, ángulos,
# manuscrito) + la categorización se benefician del modelo grande. La categorización MASIVA de
# extractos bancarios sigue con `MODEL` (flash) para no encarecer el volumen. Ver docs/FASE 6.K.
RECEIPT_MODEL = os.getenv("GEMINI_RECEIPT_MODEL", "gemini-2.5-pro")

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


class Categorization(BaseModel):
    """Resultado de categorizar una transacción: los 3 niveles del plan de cuentas (en inglés).

    `reasoning` va PRIMERO a propósito: con structured output el orden de los campos = orden de
    generación, así la IA expone la evidencia (comercio, dirección, monto) ANTES de decidir
    account_type/category (chain-of-thought) → mejor precisión + transparencia para el dueño/firma.
    """
    reasoning: str             # evidencia breve antes de decidir (1 frase, en inglés)
    account_type: AccountType  # sección del P&L
    category: str              # cuenta contable, p. ej. "Materials"
    subcategory: str           # nombre del comercio/empresa, p. ej. "Home Depot"
    confidence: float          # 0.0 - 1.0
    needs_review: bool         # true si es ambiguo y necesita confirmación humana
    review_question: str       # pregunta para el usuario si needs_review (si no, "")


class ReceiptExtraction(BaseModel):
    """Lo que la IA lee de la FOTO de un recibo + su categorización, en UNA sola llamada multimodal.

    Un recibo = UNA transacción de caja (el total). Pensado sobre todo para compras en EFECTIVO
    (lo que el extracto bancario NO ve). Si la IA detecta que se pagó con TARJETA, el endpoint marca
    la transacción para revisión (probable duplicado con el extracto). `reasoning` va primero
    (chain-of-thought, igual que Categorization)."""
    reasoning: str             # evidencia breve antes de decidir (1 frase, en inglés)
    is_receipt: bool           # ¿la imagen es realmente un recibo/factura? (guarda contra fotos random)
    merchant: str              # comercio del recibo, p. ej. "Home Depot"
    purchase_date: str         # fecha del recibo en ISO (YYYY-MM-DD); "" si no se lee
    total: float               # TOTAL pagado (magnitud positiva); 0 si no se lee
    payment_method: str        # "cash" | "card" | "unknown" (leído del recibo)
    account_type: AccountType  # sección del P&L
    category: str              # cuenta contable, p. ej. "Materials"
    subcategory: str           # = merchant (comercio específico)
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
    `model` permite sobre-escribir el modelo por llamada (p. ej. `pro` para recibos).
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


def categorize_transaction(
    description: str, amount: float, sector: str, profile_facts: str = "",
) -> Categorization:
    """Clasifica una transacción bancaria. Devuelve category/subcategory/confidence.
    `profile_facts`: hechos de la entrevista del dueño (en inglés) — "" si no hay perfil."""
    prompt = f"""You categorize bank transactions for small-business bookkeeping using a
3-level chart of accounts. The business operates in the "{sector}" sector.
{_facts_block(profile_facts)}

For the transaction below, return (all in English):
- reasoning: FIRST, in ONE short sentence, state the evidence — who the merchant/counterparty is,
  whether money came IN or OUT, and what the amount suggests — and which P&L section it implies.
  Decide every field below FROM this reasoning (do not contradict it).
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
- needs_review: true when the transaction is genuinely ambiguous and a human should confirm —
  e.g. person-to-person transfers (Zelle/Venmo/Quickpay to a person could be a subcontractor, an
  owner draw, or a customer payment), generic deposits with no identifiable payer, remittances,
  unclear merchants, or whenever your confidence is below 0.8. When in doubt, prefer needs_review
  over guessing — a wrong confident guess corrupts the books.
- review_question: if needs_review is true, a short, friendly question for the business owner that
  would resolve it (e.g. "Was the $1,920 Zelle to watson yu a subcontractor or your own draw?").
  If needs_review is false, return an empty string "".

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


def recategorize_with_answer(
    description: str, amount: float, sector: str, answer: str, profile_facts: str = "",
) -> Categorization:
    """Re-categoriza usando la EXPLICACIÓN del dueño (lenguaje simple) como señal principal."""
    prompt = f"""Re-categorize a bank transaction for a "{sector}" small business AFTER the owner
explained what it was. Use the owner's explanation as the PRIMARY signal.
{_facts_block(profile_facts)}

Transaction: "{description}" | amount: {amount}
Owner's explanation (plain language): "{answer}"

Choose `category` from the closed chart of accounts below; it MUST belong to the chosen account_type
(if nothing fits, use "Other"):
{format_for_prompt(sector)}

Return reasoning (one short sentence with the evidence, FIRST), then account_type, category,
subcategory, confidence, needs_review, review_question.
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


def extract_receipt(image: bytes, mime_type: str, sector: str) -> ReceiptExtraction:
    """Lee la FOTO de un recibo (efectivo/tarjeta) y, en UNA llamada multimodal, extrae el comercio,
    la fecha, el total y el método de pago, y lo categoriza con el plan de cuentas del sector.

    Un recibo es casi siempre una COMPRA (dinero que sale → gasto), por eso el monto se trata como
    salida. La capa de ingestión decide el signo real con `is_inflow`; aquí solo se clasifica."""
    prompt = f"""You read a photo of a PURCHASE RECEIPT for a "{sector}" small business and turn it into
ONE cash-basis bookkeeping transaction (the receipt TOTAL). Return everything in English.

- reasoning: FIRST, in ONE short sentence, state what you see — the merchant, the total, the payment
  method — and which P&L section it implies. Decide every field below FROM this reasoning.
- is_receipt: true only if the image really is a purchase receipt or invoice. If it is a random photo
  (not a receipt), set is_receipt=false and leave the other fields empty/zero.
- merchant: the store/vendor name on the receipt (e.g. "Home Depot").
- purchase_date: the date printed on the receipt in ISO format YYYY-MM-DD. "" if you cannot read it.
- total: the FINAL TOTAL actually paid (a positive number, taxes included). 0 if you cannot read it.
- payment_method: "card" if the receipt shows a card was used (VISA/MASTERCARD/DEBIT/CREDIT/a masked
  card number/"CHANGE DUE 0.00" with a card line), "cash" if it shows CASH/CASH TENDERED/change given,
  otherwise "unknown".
- account_type: one of REVENUE, COGS, OPERATING_EXPENSE, OTHER_INCOME, OTHER_EXPENSE, NON_PL.
  A purchase receipt is normally an expense (COGS = direct cost of the product/service sold;
  OPERATING_EXPENSE = overhead). Treat the amount as money OUT.
- category: the BEST-FITTING category from the closed chart of accounts below; it MUST belong to the
  account_type. If nothing truly fits, use "Other". Do NOT invent names.
- subcategory: the same specific merchant name.
- confidence: 0.0 to 1.0.
- needs_review: true if the receipt is blurry/unclear, the total is uncertain, or the category is
  genuinely ambiguous. Prefer needs_review over a wrong confident guess.
- review_question: if needs_review is true, a short friendly question for the owner; else "".

Chart of accounts for the "{sector}" sector:
{format_for_prompt(sector)}"""

    contents = [
        types.Part.from_bytes(data=image, mime_type=mime_type),
        prompt,
    ]
    response = _generate(contents, ReceiptExtraction, model=RECEIPT_MODEL)  # pro: OCR + categoría
    result = response.parsed
    logger.info(
        "extract_receipt | model=%s sector=%s mime=%s -> is_receipt=%s merchant=%r total=%s "
        "payment=%s type=%s category=%r confidence=%s",
        RECEIPT_MODEL, sector, mime_type, result.is_receipt, result.merchant, result.total,
        result.payment_method, result.account_type.value, result.category, result.confidence,
    )
    return result


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
in the SAME ORDER, each with: reasoning, account_type, category, subcategory, confidence,
needs_review, review_question.

- reasoning: FIRST, one short sentence with the evidence (merchant/counterparty, money in/out,
  amount) and the P&L section it implies. Decide the other fields FROM it.
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
