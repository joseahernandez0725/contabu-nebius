"""
ContaBu — BANCOS VINCULADOS (FASE 10, área Movimientos · 3-oct-2026). Proveedor: PLAID.

Plaid Link (en el navegador) devuelve un `public_token`; el backend lo cambia por un `access_token`
(`/item/public_token/exchange`), lo guarda CIFRADO en `BankConnection` y trae los movimientos con
`/transactions/sync`. Todo pasa por EL MISMO motor que un CSV (`ingestion.ingest_parsed`:
huella → regla → transferencia → Pendiente).

(Primero se construyó sobre Teller, que cerró su API el mismo 3-oct: el cifrado, la conexión por libro
y el paso por el motor se reutilizaron tal cual; cambió solo el cliente del proveedor.)

Configuración (todo por variable de entorno, nada en el código):
  PLAID_CLIENT_ID   ┐ del Dashboard de Plaid. Sin ellos, la vinculación queda apagada.
  PLAID_SECRET      ┘ El secret es por ambiente (el de sandbox no sirve en production).
  PLAID_ENV         sandbox | production   (por defecto: sandbox)
  BANK_TOKEN_KEY    clave Fernet para cifrar los access_token guardados (obligatoria para vincular)

Verificado en la documentación de Plaid (3-oct-2026):
- Hosts: `https://sandbox.plaid.com` y `https://production.plaid.com` (solo dos ambientes).
  `client_id` y `secret` van en el cuerpo JSON de cada llamada.
- 🔴 SIGNO: en Plaid `amount` es POSITIVO cuando SALE dinero (compras, también en tarjetas) y negativo
  cuando entra. ContaBu usa lo contrario (positivo = entra) → se invierte SIEMPRE.
- `/transactions/sync`: `added` / `modified` / `removed`, `has_more`, `next_cursor` (vale ≥1 año). Si
  falla a media paginación (`TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION`) hay que reiniciar el bucle
  desde el cursor original. Justo después de vincular puede venir vacío (`NOT_READY`).
- `pending: true` = no asentado. Al asentarse, Plaid lo quita (`removed`) y manda el asentado como
  `added` → lo pendiente simplemente no entra, y su versión asentada entra en un sync posterior.
- Errores: `{error_type, error_code, error_message, display_message}`. `ITEM_LOGIN_REQUIRED` = el banco
  cortó la conexión (se repara con Link en modo actualización, pasando el `access_token`).
- `personal_finance_category` (la categoría de Plaid) **nunca** entra a `category`: la categoría la
  pone una regla o el bookkeeper (decisión de Jose, 3-oct).
"""

import os
import re
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlmodel import Session, select

from corrections import VIA_BANK_MODIFIED, record_change, snapshot
from ingestion import ingest_parsed, make_fingerprint, mask_account
from models import BankConnection, Company, Transaction
from rules import check_number, classify_transfer, load_rules, match_rule, matchable_text

ENVIRONMENTS = {"sandbox": "https://sandbox.plaid.com", "production": "https://production.plaid.com"}
# Errores que significan "el banco cortó / ya no hay acceso": la conexión pasa a `disconnected`.
DISCONNECTED_CODES = {"ITEM_LOGIN_REQUIRED", "ITEM_NOT_FOUND", "INVALID_ACCESS_TOKEN",
                      "ACCESS_NOT_GRANTED", "PENDING_EXPIRATION", "PENDING_DISCONNECT"}
# Historia que se pide al vincular (máximo de Plaid: 730 días). Se fija al crear el Item.
DAYS_REQUESTED = 730


class BankConfigError(Exception):
    """Falta configuración del servidor (llaves de Plaid, clave de cifrado…). Código en `code`."""
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class BankError(Exception):
    """Error devuelto por Plaid. `disconnected` = el banco cortó la conexión."""
    def __init__(self, status: int, code: str, message: str = ""):
        super().__init__(f"{status} {code}")
        self.status, self.code, self.message = status, code, message

    @property
    def disconnected(self) -> bool:
        return self.code in DISCONNECTED_CODES


# ── Configuración ───────────────────────────────────────────────────────────────────────────────

def environment() -> str:
    env = (os.getenv("PLAID_ENV") or "sandbox").strip().lower()
    return env if env in ENVIRONMENTS else "sandbox"


def _credentials() -> dict:
    client_id, secret = (os.getenv("PLAID_CLIENT_ID") or "").strip(), (os.getenv("PLAID_SECRET") or "").strip()
    if not client_id or not secret:
        raise BankConfigError("plaid_keys_missing")
    return {"client_id": client_id, "secret": secret}


def public_config() -> dict:
    """Lo que el navegador necesita saber. NUNCA incluye secretos."""
    enabled = bool((os.getenv("PLAID_CLIENT_ID") or "").strip() and (os.getenv("PLAID_SECRET") or "").strip())
    return {"enabled": enabled, "provider": "plaid", "environment": environment()}


def _fernet() -> Fernet:
    key = (os.getenv("BANK_TOKEN_KEY") or "").strip()
    if not key:
        raise BankConfigError("token_key_missing")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError):
        raise BankConfigError("token_key_invalid")


def encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()


def decrypt_token(enc: str) -> str:
    try:
        return _fernet().decrypt(enc.encode()).decode()
    except InvalidToken:
        raise BankConfigError("token_key_mismatch")  # la clave cambió: hay que volver a vincular


# ── Cliente HTTP ────────────────────────────────────────────────────────────────────────────────

def _post(path: str, body: dict, env: Optional[str] = None) -> dict:
    """POST a Plaid. Las credenciales viajan en el cuerpo; nada de esto se registra en logs."""
    url = ENVIRONMENTS[env or environment()] + path
    try:
        r = httpx.post(url, json={**_credentials(), **body}, timeout=httpx.Timeout(60.0))
    except httpx.HTTPError:
        raise BankError(0, "network_error")
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 400:
        raise BankError(r.status_code, data.get("error_code") or f"http_{r.status_code}",
                        data.get("display_message") or data.get("error_message") or "")
    return data


def create_link_token(user_id: int, language: str, access_token: Optional[str] = None) -> str:
    """Token de un solo uso para abrir Plaid Link. Con `access_token` abre el modo actualización
    (reparar una conexión que el banco cortó): ahí no se mandan `products`."""
    body: dict = {
        "client_name": "ContaBu",
        "user": {"client_user_id": f"contabu-user-{user_id}"},  # sin datos personales (doc de Plaid)
        "country_codes": ["US"],
        "language": "es" if (language or "").startswith("es") else "en",
    }
    if access_token:
        body["access_token"] = access_token
    else:
        body["products"] = ["transactions"]
        body["transactions"] = {"days_requested": DAYS_REQUESTED}
    return _post("/link/token/create", body)["link_token"]


def exchange_public_token(public_token: str) -> tuple[str, str]:
    data = _post("/item/public_token/exchange", {"public_token": public_token})
    return data["access_token"], data["item_id"]


def remove_item(access_token: str, env: str) -> None:
    """Desvincular: borra el Item en Plaid (deja de tener acceso y de facturar)."""
    _post("/item/remove", {"access_token": access_token}, env)


def account_label(a: dict) -> Optional[str]:
    """Cómo se ve la cuenta en la Hoja: nombre y últimos 4, enmascarados como en un CSV."""
    return mask_account(f"{a.get('name') or ''} {a.get('mask') or ''}".strip())


def signed_amount(t: dict) -> Decimal:
    """Plaid: positivo = sale dinero. ContaBu: positivo = entra. Se invierte siempre."""
    return -Decimal(str(t.get("amount")))


def _sync_pages(access_token: str, env: str, cursor: Optional[str]) -> tuple[list, list, list, str, str]:
    """Todas las páginas de `/transactions/sync` desde `cursor`: (added, modified, removed, cursor,
    estado). Si Plaid avisa que los datos cambiaron a media paginación, se reinicia desde el cursor
    ORIGINAL (lo pide su documentación)."""
    for _ in range(3):
        added: list = []
        modified: list = []
        removed: list = []
        page_cursor, status = cursor, ""
        try:
            while True:
                body = {"access_token": access_token, "count": 500,
                        "options": {"include_original_description": True}}
                if page_cursor:
                    body["cursor"] = page_cursor
                data = _post("/transactions/sync", body, env)
                added += data.get("added") or []
                modified += data.get("modified") or []
                removed += data.get("removed") or []
                page_cursor = data.get("next_cursor") or page_cursor
                status = data.get("transactions_update_status") or status
                if not data.get("has_more"):
                    return added, modified, removed, page_cursor or "", status
        except BankError as e:
            if e.code == "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION":
                continue
            raise
    raise BankError(0, "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION")


def _parse(t: dict, by_id: dict) -> Optional[tuple]:
    """Un movimiento de Plaid → la fila del motor `(fecha, descripción, monto con signo, cuenta,
    huella, id de Plaid)`. None si le falta algo."""
    try:
        d = datetime.strptime(t["date"], "%Y-%m-%d").date()
        description = (t.get("original_description") or t.get("name") or "").strip()
        signed = signed_amount(t)
    except (KeyError, ValueError, TypeError, InvalidOperation):
        return None
    if not description or not t.get("transaction_id"):
        return None
    label = account_label(by_id.get(t.get("account_id")) or {})
    return (d, description, signed, label, make_fingerprint(d, description, signed, label), t["transaction_id"])


def _last4(label: Optional[str]) -> Optional[str]:
    digits = re.sub(r"\D", "", label or "")
    return digits[-4:] if len(digits) >= 4 else None


# ── CSV + Plaid en el mismo libro ───────────────────────────────────────────────────────────────

def csv_cutoffs(session: Session, company_id: int) -> tuple[dict, Counter]:
    """DECISIÓN (Movimientos, 4-oct-2026): un libro con meses subidos por CSV que después se vincula
    a Plaid NO vuelve a recibir esos meses. Por cuenta (últimos 4 dígitos), el último día que ya
    cubren las filas que NO vinieron de Plaid es el CORTE:
    - antes del corte → no entra (esos meses ya están, aunque la descripción del CSV sea otra);
    - el MISMO día del corte → entra solo lo que no esté ya (mismo día + mismo monto con signo, por
      conteo): el CSV pudo bajarse a media jornada;
    - después del corte → entra normal.
    Una fila sin cuenta (CSV donde no se supo cuál era) cuenta para TODAS las cuentas. Devuelve
    ({last4 | None: fecha de corte}, Counter de (last4 | None, fecha, monto con signo) en ese día)."""
    rows = session.exec(
        select(Transaction.bank_account, Transaction.date, Transaction.amount, Transaction.is_inflow)
        .where(Transaction.company_id == company_id)
        .where(Transaction.plaid_transaction_id.is_(None))
    ).all()
    cut: dict = {}
    for acct, d, _, _ in rows:
        k = _last4(acct)
        if k not in cut or d > cut[k]:
            cut[k] = d
    edge: Counter = Counter()
    for acct, d, amount, inflow in rows:
        k = _last4(acct)
        if cut.get(k) == d:
            edge[(k, d, (amount if inflow else -amount).quantize(Decimal("0.01")))] += 1
    return cut, edge


def _drop_before_csv(parsed: list[tuple], cut: dict, edge: Counter) -> tuple[list[tuple], int]:
    if not cut:
        return parsed, 0
    kept: list[tuple] = []
    dropped = 0
    edge = Counter(edge)  # se consume
    for row in parsed:
        d, _, signed, label = row[:4]
        k = _last4(label)
        # El corte de su cuenta y el de las filas sin cuenta: manda el más tardío.
        limits = [(kk, cut[kk]) for kk in (k, None) if kk in cut]
        if not limits:
            kept.append(row)
            continue
        kk, limit = max(limits, key=lambda x: x[1])
        if d < limit:
            dropped += 1
            continue
        if d == limit:
            key = (kk, d, signed.quantize(Decimal("0.01")))
            if edge.get(key, 0) > 0:
                edge[key] -= 1
                dropped += 1
                continue
        kept.append(row)
    return kept, dropped


# ── Avisos del banco: modificado / retirado ─────────────────────────────────────────────────────

def _redecide(session: Session, company: Company, tx: Transaction, rules: list) -> None:
    """El motor vuelve a decidir la categoría de una fila que el banco cambió: regla → transferencia →
    Pendiente (el mismo orden que al entrar)."""
    rule = match_rule(rules, matchable_text(tx.description, tx.description_extra))
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


def apply_modified(session: Session, company: Company, tx: Transaction, row: tuple, rules: list) -> bool:
    """Aviso `modified` sobre una fila ya registrada: se actualizan los datos DEL BANCO (fecha, monto,
    descripción, cuenta). La categoría se vuelve a decidir SOLO si la decidió el motor (una regla o
    una transferencia) o está Pendiente; lo escrito a mano manda y no se toca. Si la categoría cambia,
    queda en el historial de correcciones. Devuelve True si la categoría cambió.

    (Mes cerrado: cuando exista "cerrar mes", aquí un aviso sobre esas filas solo se marcará para
    revisión — hoy no hay meses cerrados.)"""
    d, description, signed, label, fp = row[:5]
    tx.date, tx.description = d, description
    tx.amount, tx.is_inflow = abs(signed), signed > 0
    tx.bank_account, tx.fingerprint = label, fp
    tx.check_number = check_number(description)
    session.add(tx)
    if tx.source == "manual" or not (tx.source in ("rule", "transfer") or tx.category is None):
        return False
    before = snapshot(tx)
    _redecide(session, company, tx, rules)
    return record_change(session, tx, before, via=VIA_BANK_MODIFIED) is not None


# ── Sincronizar ─────────────────────────────────────────────────────────────────────────────────

def sync(session: Session, conn: BankConnection, company: Company) -> dict:
    """Trae lo nuevo desde `conn.next_cursor` y lo pasa por el motor común.

    No duplica: el cursor solo devuelve lo nuevo y cada fila trae su id de Plaid (deduplicación
    exacta). Lo que el CSV ya cubre no entra (ver `csv_cutoffs`). Los avisos del banco se APLICAN:
    `modified` actualiza la fila (ver `apply_modified`); `removed` sobre una fila registrada la MARCA
    como retirada por el banco (no se borra: decide el bookkeeper) y sobre algo que nunca entró (un
    pendiente retenido) se ignora. El cursor nuevo se guarda en la MISMA transacción de base de datos
    que todo lo demás: si algo falla, no avanza y el próximo sync lo reintenta.
    Lanza `BankError` / `BankConfigError`; el estado de error lo deja quien llama."""
    token = decrypt_token(conn.access_token_enc)
    accounts = _post("/accounts/get", {"access_token": token}, conn.environment).get("accounts") or []
    by_id = {a.get("account_id"): a for a in accounts}
    added, modified, removed, cursor, status = _sync_pages(token, conn.environment, conn.next_cursor)

    # Un mismo id puede venir en varias listas a lo largo de las páginas: vale el último estado.
    removed_ids = {r.get("transaction_id") for r in removed if r.get("transaction_id")}
    latest: dict[str, dict] = {}
    for t in [*added, *modified]:
        if t.get("transaction_id"):
            latest[t["transaction_id"]] = t

    held = errors = 0
    rows: dict[str, tuple] = {}
    for tid, t in latest.items():
        if tid in removed_ids:
            continue
        if t.get("pending"):
            held += 1  # al asentarse, Plaid lo manda de nuevo como `added`, ya asentado
            continue
        row = _parse(t, by_id)
        if row is None:
            errors += 1
            continue
        rows[tid] = row

    existing = {tx.plaid_transaction_id: tx for tx in session.exec(
        select(Transaction).where(Transaction.company_id == company.id)
        .where(Transaction.plaid_transaction_id.in_(set(rows) | removed_ids))
    ).all()} if (rows or removed_ids) else {}

    # MODIFICADOS: lo ya registrado se actualiza; lo que no estaba entra como nuevo.
    rules = load_rules(session, company.id, company.sector or "")
    modified_applied = recategorized = unchanged = 0
    new_rows: list[tuple] = []
    for tid, row in rows.items():
        tx = existing.get(tid)
        if tx is None:
            new_rows.append(row)
            continue
        if row[4] == tx.fingerprint:
            unchanged += 1             # ya está en el libro, igual
        else:                          # el banco cambió algo de verdad
            modified_applied += 1
            if apply_modified(session, company, tx, row, rules):
                recategorized += 1

    # RETIRADOS: se marcan (no se borran). Lo que nunca entró (pendiente retenido) se ignora.
    removed_marked = removed_ignored = 0
    for tid in removed_ids:
        tx = existing.get(tid)
        if tx is None:
            removed_ignored += 1
        elif tx.bank_removed_at is None:
            tx.bank_removed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            session.add(tx)
            removed_marked += 1

    # Lo que el CSV ya cubre no vuelve a entrar.
    cut, edge = csv_cutoffs(session, company.id)
    new_rows.sort(key=lambda r: r[0])
    new_rows, before_csv = _drop_before_csv(new_rows, cut, edge)

    conn.accounts = [{"id": a.get("account_id"), "name": a.get("name"), "mask": a.get("mask"),
                      "type": a.get("type"), "subtype": a.get("subtype")} for a in accounts]
    conn.next_cursor = cursor or conn.next_cursor
    session.add(conn)
    # Plaid nunca categoriza (ai_limit=0, y su categoría no se usa): regla → transferencia → Pendiente.
    # `ingest_parsed` hace el commit de TODO (filas nuevas, avisos aplicados y el cursor).
    core = ingest_parsed(session, company.id, company.sector or "", new_rows, ai_limit=0)
    return {
        "accounts": len(accounts),
        "fetched": len(added),
        "inserted": core["inserted"],
        "skipped_duplicates": core["skipped_duplicates"] + unchanged,
        "skipped_before_csv": before_csv,
        "held_pending": held,
        "modified_applied": modified_applied,
        "modified_recategorized": recategorized,
        "removed_marked": removed_marked,
        "removed_ignored": removed_ignored,
        "errors": errors,
        "by_rule": core["by_rule"],
        "by_transfer": core["by_transfer"],
        "pending": core["inserted"] - core["by_rule"] - core["by_transfer"] - core["by_ai"],
        "not_ready": status == "NOT_READY" and not added,
        "update_status": status,  # p. ej. HISTORICAL_UPDATE_COMPLETE: Plaid ya trajo todo el historial
    }
