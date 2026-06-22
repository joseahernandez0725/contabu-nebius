"""
ContaBu — backend (FastAPI).
Endpoints de la API: prueba (/api/hello), identidad (/api/me) e ingestión de archivos bancarios.

Multi-tenant (deuda #2 🔴): la identidad sale SIEMPRE del usuario autenticado (token Firebase,
ver `auth.py`); los endpoints validan que la empresa/transacción pertenezca al usuario antes de
tocarla. El `company_id` de la URL ya NO se confía a ciegas.
"""

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlmodel import Session, select

from ai import recategorize_with_answer
from auth import current_user, owned_company, owned_transaction
from categories import categories_for, categories_for_company
from db import get_session
from ingestion import ingest_csv, mask_account
from models import (
    AccountType, CategoryRule, Company, CompanyCategory, PnlReport, RuleScope, Transaction, User,
)
from reports import income_statement
from rules import apply_rule_to_transactions, is_check, match_rule, upsert_company_rule


class TransactionUpdate(BaseModel):
    """Campos que se pueden actualizar al confirmar/corregir una revisión."""
    account_type: Optional[AccountType] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    needs_review: Optional[bool] = None


class ClarifyPayload(BaseModel):
    """Respuesta del dueño en lenguaje simple ('¿qué fue / en qué consistió?')."""
    answer: str


class ClarifyGroupPayload(BaseModel):
    """Una sola explicación del dueño que aplica a VARIAS transacciones de la misma contraparte
    (p. ej. la renta mensual a WATSON YU): una pregunta → categoriza todas de una vez."""
    transaction_ids: list[int]
    answer: str


class SetAccountPayload(BaseModel):
    """Cuenta bancaria que el usuario indica cuando la IA no pudo detectarla del archivo.
    Se aplica a las transacciones recién subidas (transaction_ids) y se guarda enmascarada."""
    transaction_ids: list[int]
    account: str


class CompanyThresholds(BaseModel):
    """Ajustes editables por empresa: umbrales de monto (super/gasolinera) y ventana de
    asentamiento (días que se retienen los movimientos recientes/pending)."""
    supermarket_threshold: Optional[Decimal] = None
    gas_station_threshold: Optional[Decimal] = None


class CustomCategoryCreate(BaseModel):
    """Nueva categoría personalizada que la empresa agrega a su plan de cuentas (P&L personalizable)."""
    account_type: AccountType  # sección del P&L
    name: str                  # nombre de la cuenta (en inglés)


class RuleUpdate(BaseModel):
    """Edición de una regla de empresa desde la tabla de reglas (comercio → categoría)."""
    account_type: Optional[AccountType] = None
    category: Optional[str] = None


class PnlReportCreate(BaseModel):
    """Nuevo proyecto de P&L (se crea como borrador; recalcula en vivo hasta que se cierra)."""
    name: str
    start: Optional[date] = None
    end: Optional[date] = None
    settlement_buffer_days: Optional[int] = None

# `app` es nuestra aplicación. uvicorn la "sirve" y queda esperando peticiones.
app = FastAPI(title="ContaBu API")

# CORS: orígenes desde los que el navegador puede llamar a este backend.
# - localhost:5173 → desarrollo (Vite).
# - *.web.app / *.firebaseapp.com → el frontend desplegado en Firebase Hosting.
# (Cuando exista el dominio propio app.contabu.com, se agrega aquí.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "https://contabu-prod.web.app",
        "https://contabu-prod.firebaseapp.com",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/hello")
def hello():
    """Endpoint de prueba: responde un JSON. El frontend lo pedirá y lo mostrará."""
    return {"message": "¡Hola desde el backend de ContaBu! 🚀"}


@app.get("/api/me")
def get_me(
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Devuelve el usuario autenticado y SUS empresas. En el primer login (sin empresa) crea una
    empresa por defecto para que el flujo funcione de inmediato. El frontend usa esto en vez de
    un `company_id` fijo."""
    companies = session.exec(select(Company).where(Company.user_id == user.id)).all()
    if not companies:
        company = Company(name="My Business", sector="construction", user_id=user.id)
        session.add(company)
        session.commit()
        session.refresh(company)
        companies = [company]
    return {"user": user, "companies": companies}


@app.post("/api/companies/{company_id}/transactions/upload")
async def upload_transactions(
    company_id: int,
    file: UploadFile,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Sube un CSV bancario y dispara el pipeline (mapeo IA → parse → categoriza → guarda).

    La IA categoriza en LOTES de 50 (cada lote = 1 petición a Gemini), así que el archivo se
    importa completo. `limit=1000` es solo un **tope de seguridad** por subida en dev (evita
    disparar la cuota gratis por accidente); el bulk asíncrono real queda para post-deploy
    (deuda técnica #8).
    """
    company = owned_company(company_id, user, session)
    content = await file.read()
    result = ingest_csv(
        session, company_id, company.sector, content, limit=1000
    )
    return result


@app.post("/api/companies/{company_id}/transactions/set-account")
def set_transactions_account(
    company_id: int,
    payload: SetAccountPayload,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Asigna la cuenta bancaria (que indicó el usuario) a varias transacciones de la empresa.
    Se guarda enmascarada a los últimos 4 dígitos. Útil cuando la IA no la detectó del archivo."""
    owned_company(company_id, user, session)
    masked = mask_account(payload.account)
    if masked is None:
        raise HTTPException(status_code=422, detail="account must contain at least 4 digits")
    if not payload.transaction_ids:
        return {"updated": 0, "bank_account": masked}
    rows = session.exec(
        select(Transaction)
        .where(Transaction.company_id == company_id)
        .where(Transaction.id.in_(payload.transaction_ids))
    ).all()
    for tx in rows:
        tx.bank_account = masked
        session.add(tx)
    session.commit()
    return {"updated": len(rows), "bank_account": masked}


@app.get("/api/companies/{company_id}")
def get_company(
    company_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Devuelve la empresa (para el nombre en el header, etc.)."""
    return owned_company(company_id, user, session)


@app.patch("/api/companies/{company_id}/thresholds")
def update_thresholds(
    company_id: int,
    payload: CompanyThresholds,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Edita los umbrales de monto (super/gasolinera) que usa la desambiguación por banda."""
    company = owned_company(company_id, user, session)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(company, field, value)
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


@app.get("/api/companies/{company_id}/categories")
def get_categories(
    company_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Plan de cuentas EFECTIVO de la empresa: base del sector + categorías personalizadas.
    Lo usa el frontend para el selector de categoría (y derivar el account_type)."""
    company = owned_company(company_id, user, session)
    return categories_for_company(session, company_id, company.sector)


@app.get("/api/companies/{company_id}/custom-categories")
def list_custom_categories(
    company_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Categorías personalizadas que la empresa agregó a su plan de cuentas (con id, para borrarlas)."""
    owned_company(company_id, user, session)
    return session.exec(
        select(CompanyCategory).where(CompanyCategory.company_id == company_id)
    ).all()


@app.post("/api/companies/{company_id}/custom-categories")
def add_custom_category(
    company_id: int,
    payload: CustomCategoryCreate,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Agrega una categoría personalizada (P&L personalizable v1). Idempotente: si ya existe esa
    categoría (mismo nombre y sección, o ya está en el plan base), no la duplica."""
    company = owned_company(company_id, user, session)
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="El nombre de la categoría no puede estar vacío.")

    # No duplicar si ya existe en el plan base del sector o en las personalizadas.
    base = categories_for(company.sector).get(payload.account_type.value, [])
    if any(name.lower() == c.lower() for c in base):
        raise HTTPException(status_code=409, detail="Esa categoría ya existe en el plan base.")
    existing = session.exec(
        select(CompanyCategory)
        .where(CompanyCategory.company_id == company_id)
        .where(CompanyCategory.account_type == payload.account_type)
    ).all()
    for c in existing:
        if c.name.lower() == name.lower():
            return c  # ya existe → idempotente

    cat = CompanyCategory(company_id=company_id, account_type=payload.account_type, name=name)
    session.add(cat)
    session.commit()
    session.refresh(cat)
    return cat


@app.delete("/api/companies/{company_id}/custom-categories/{category_id}")
def delete_custom_category(
    company_id: int,
    category_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Elimina una categoría personalizada. No afecta transacciones ya categorizadas con ese nombre."""
    owned_company(company_id, user, session)
    cat = session.get(CompanyCategory, category_id)
    if cat is None or cat.company_id != company_id:
        raise HTTPException(status_code=404, detail="Categoría no encontrada.")
    session.delete(cat)
    session.commit()
    return {"deleted": category_id}


# --- Tabla de reglas editable (comercio → categoría) — el "reglamento" que la firma cura ---

def _owned_company_rule(company_id: int, rule_id: int, session: Session) -> CategoryRule:
    """Carga una regla de EMPRESA validando que sea de esta empresa (404 si no)."""
    rule = session.get(CategoryRule, rule_id)
    if rule is None or rule.scope != RuleScope.COMPANY or rule.company_id != company_id:
        raise HTTPException(status_code=404, detail="Regla no encontrada.")
    return rule


@app.get("/api/companies/{company_id}/rules")
def list_rules(
    company_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Reglas de EMPRESA (comercio → categoría) con cuántas transacciones afecta cada una.
    Es el 'reglamento' editable que el dueño/firma cura."""
    owned_company(company_id, user, session)
    rules = session.exec(
        select(CategoryRule)
        .where(CategoryRule.scope == RuleScope.COMPANY)
        .where(CategoryRule.company_id == company_id)
    ).all()
    txs = session.exec(
        select(Transaction).where(Transaction.company_id == company_id)
    ).all()
    out = []
    for r in rules:
        matches = sum(1 for t in txs if match_rule([r], t.description or "") is not None)
        out.append({
            "id": r.id, "match_text": r.match_text,
            "account_type": r.account_type.value if r.account_type else None,
            "category": r.category, "matches": matches,
        })
    out.sort(key=lambda x: x["matches"], reverse=True)
    return out


@app.patch("/api/companies/{company_id}/rules/{rule_id}")
def update_rule(
    company_id: int,
    rule_id: int,
    payload: RuleUpdate,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Edita una regla y la re-aplica a las transacciones existentes del comercio (cambia el
    reglamento → los libros se ajustan al instante). Devuelve la regla y cuántas alineó."""
    owned_company(company_id, user, session)
    rule = _owned_company_rule(company_id, rule_id, session)
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(rule, field, value)
    session.add(rule)
    applied = apply_rule_to_transactions(session, company_id, rule)
    session.commit()
    session.refresh(rule)
    return {"id": rule.id, "match_text": rule.match_text,
            "account_type": rule.account_type.value if rule.account_type else None,
            "category": rule.category, "applied": applied}


@app.delete("/api/companies/{company_id}/rules/{rule_id}")
def delete_rule(
    company_id: int,
    rule_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Elimina una regla del reglamento. No re-categoriza lo ya hecho (solo deja de aplicar a futuro)."""
    owned_company(company_id, user, session)
    rule = _owned_company_rule(company_id, rule_id, session)
    session.delete(rule)
    session.commit()
    return {"deleted": rule_id}


# --- P&L como PROYECTOS guardados (draft = recalcula en vivo · closed = congelado) ---

def _pnl_jsonable(pnl: dict) -> dict:
    """Convierte el P&L (con Decimals + breakdown) a JSON-serializable (Decimal → float)."""
    out: dict = {}
    for k, v in pnl.items():
        if k == "breakdown":
            out[k] = {
                sec: [{"category": r["category"], "amount": float(r["amount"])} for r in rows]
                for sec, rows in v.items()
            }
        else:
            out[k] = float(v)
    return out


def _report_meta(r: PnlReport) -> dict:
    return {"id": r.id, "name": r.name, "start": r.start_date, "end": r.end_date,
            "status": r.status, "closed_at": r.closed_at}


def _owned_report(company_id: int, report_id: int, session: Session) -> PnlReport:
    r = session.get(PnlReport, report_id)
    if r is None or r.company_id != company_id:
        raise HTTPException(status_code=404, detail="Reporte no encontrado.")
    return r


def _report_pnl(session: Session, company_id: int, r: PnlReport) -> dict:
    """El P&L del reporte: si está CERRADO devuelve el snapshot congelado; si es BORRADOR lo
    recalcula en vivo para su rango de fechas."""
    if r.status == "closed" and r.snapshot:
        return json.loads(r.snapshot)
    return _pnl_jsonable(income_statement(session, company_id, r.start_date, r.end_date))


@app.get("/api/companies/{company_id}/pnl-reports")
def list_pnl_reports(
    company_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Lista los proyectos de P&L guardados (con su net income para mostrar en la lista)."""
    owned_company(company_id, user, session)
    reports = session.exec(
        select(PnlReport).where(PnlReport.company_id == company_id)
        .order_by(PnlReport.created_at.desc())
    ).all()
    return [{**_report_meta(r), "net_income": _report_pnl(session, company_id, r)["net_income"]}
            for r in reports]


@app.post("/api/companies/{company_id}/pnl-reports")
def create_pnl_report(
    company_id: int,
    payload: PnlReportCreate,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Crea un proyecto de P&L como BORRADOR (recalcula en vivo hasta que se cierra)."""
    owned_company(company_id, user, session)
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail="El nombre no puede estar vacío.")
    r = PnlReport(company_id=company_id, name=payload.name.strip(),
                  start_date=payload.start, end_date=payload.end, status="draft")
    session.add(r)
    session.commit()
    session.refresh(r)
    return _report_meta(r)


@app.get("/api/companies/{company_id}/pnl-reports/{report_id}")
def get_pnl_report(
    company_id: int,
    report_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Abre un proyecto: su metadata + el P&L (recalculado si es borrador; congelado si está cerrado)."""
    owned_company(company_id, user, session)
    r = _owned_report(company_id, report_id, session)
    return {**_report_meta(r), "pnl": _report_pnl(session, company_id, r)}


@app.post("/api/companies/{company_id}/pnl-reports/{report_id}/close")
def close_pnl_report(
    company_id: int,
    report_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """CIERRA el período: calcula el P&L ahora y lo CONGELA (snapshot). Ya no cambia."""
    owned_company(company_id, user, session)
    r = _owned_report(company_id, report_id, session)
    r.snapshot = json.dumps(_pnl_jsonable(income_statement(session, company_id, r.start_date, r.end_date)))
    r.status = "closed"
    r.closed_at = datetime.now(timezone.utc)
    session.add(r)
    session.commit()
    session.refresh(r)
    return _report_meta(r)


@app.post("/api/companies/{company_id}/pnl-reports/{report_id}/reopen")
def reopen_pnl_report(
    company_id: int,
    report_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Reabre un proyecto cerrado → vuelve a BORRADOR (recalcula en vivo); descarta el snapshot."""
    owned_company(company_id, user, session)
    r = _owned_report(company_id, report_id, session)
    r.status = "draft"
    r.snapshot = None
    r.closed_at = None
    session.add(r)
    session.commit()
    session.refresh(r)
    return _report_meta(r)


@app.delete("/api/companies/{company_id}/pnl-reports/{report_id}")
def delete_pnl_report(
    company_id: int,
    report_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Elimina un proyecto de P&L."""
    owned_company(company_id, user, session)
    r = _owned_report(company_id, report_id, session)
    session.delete(r)
    session.commit()
    return {"deleted": report_id}


@app.get("/api/companies/{company_id}/transactions")
def list_transactions(
    company_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Lista las transacciones de una empresa (más recientes primero)."""
    owned_company(company_id, user, session)
    return session.exec(
        select(Transaction)
        .where(Transaction.company_id == company_id)
        .order_by(Transaction.date.desc())
    ).all()


@app.get("/api/companies/{company_id}/income-statement")
def get_income_statement(
    company_id: int,
    start: Optional[date] = None,
    end: Optional[date] = None,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Estado de resultados (P&L) calculado en SQL, opcional por rango `start`/`end` (YYYY-MM-DD)."""
    owned_company(company_id, user, session)
    return income_statement(session, company_id, start, end)


@app.get("/api/companies/{company_id}/transactions/review")
def list_review(
    company_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Transacciones que la IA marcó como dudosas (necesitan confirmación humana)."""
    owned_company(company_id, user, session)
    return session.exec(
        select(Transaction)
        .where(Transaction.company_id == company_id)
        .where(Transaction.needs_review == True)  # noqa: E712
        .order_by(Transaction.date.desc())
    ).all()


@app.patch("/api/transactions/{transaction_id}")
def update_transaction(
    transaction_id: int,
    payload: TransactionUpdate,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Actualiza una transacción (confirmar o corregir una revisión)."""
    tx = owned_transaction(transaction_id, user, session)
    data = payload.model_dump(exclude_unset=True)
    if data.get("subcategory"):
        data["subcategory"] = data["subcategory"].upper()  # subcategorías siempre en MAYÚSCULA
    for field, value in data.items():
        setattr(tx, field, value)
    # Si el usuario tocó la categorización: fuente = manual y se guarda como REGLA (memoria),
    # igual que el flujo de "explicar". Así la próxima vez ese comercio se categoriza solo.
    if any(k in data for k in ("account_type", "category", "subcategory")):
        tx.source = "manual"
        # Un cheque no genera regla (cada cheque es distinto; ver rules.is_check).
        if not is_check(tx.description):
            company = session.get(Company, tx.company_id)
            rule = upsert_company_rule(
                session, tx.company_id, company.sector if company else "general",
                tx.subcategory, tx.account_type, tx.category,
                description=tx.description,
            )
            # Corregir una → corregir todas: alinea las demás transacciones del mismo comercio.
            if rule is not None:
                apply_rule_to_transactions(session, tx.company_id, rule, exclude_id=tx.id)
    session.add(tx)
    session.commit()
    session.refresh(tx)
    return tx


@app.post("/api/transactions/{transaction_id}/clarify")
def clarify_transaction(
    transaction_id: int,
    payload: ClarifyPayload,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """El dueño explica en lenguaje simple qué fue el movimiento → la IA re-categoriza
    con esa info y se guarda una REGLA de empresa (memoria) para la próxima vez."""
    tx = owned_transaction(transaction_id, user, session)

    company = session.get(Company, tx.company_id)
    sector = company.sector if company else "general"

    # Reconstruir la dirección (entrada/salida) desde el account_type actual.
    income = tx.account_type in (AccountType.REVENUE, AccountType.OTHER_INCOME)
    signed = float(tx.amount) if income else -float(tx.amount)

    cat = recategorize_with_answer(tx.description, signed, sector, payload.answer)

    tx.account_type = cat.account_type
    tx.category = cat.category
    tx.subcategory = (cat.subcategory or "").upper() or None
    tx.confidence = cat.confidence
    tx.needs_review = cat.needs_review
    tx.review_question = cat.review_question or None
    tx.ai_categorized = True
    tx.source = "ai"  # re-categorizada por la IA a partir de la explicación del dueño
    session.add(tx)

    # Memoria del cliente: crea/actualiza la regla de empresa (idempotente, frase específica).
    # Excepto cheques: cada uno es distinto, una regla sería errónea.
    if not is_check(tx.description):
        rule = upsert_company_rule(
            session, tx.company_id, sector, cat.subcategory, cat.account_type, cat.category,
            description=tx.description,
        )
        # Corregir una → corregir todas: alinea las demás del mismo comercio.
        if rule is not None:
            apply_rule_to_transactions(session, tx.company_id, rule, exclude_id=tx.id)

    session.commit()
    session.refresh(tx)
    return tx


@app.post("/api/transactions/clarify-group")
def clarify_group(
    payload: ClarifyGroupPayload,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """El dueño explica UNA vez y se categorizan TODAS las transacciones de la misma contraparte
    (una sola llamada a la IA con un representante; el resultado se aplica al grupo). Evita repetir
    la misma pregunta por cada movimiento (p. ej. la renta mensual)."""
    if not payload.transaction_ids:
        raise HTTPException(status_code=422, detail="no transactions")
    txs = [owned_transaction(tid, user, session) for tid in payload.transaction_ids]
    rep = txs[0]  # representante: misma contraparte → misma categoría

    company = session.get(Company, rep.company_id)
    sector = company.sector if company else "general"
    income = rep.account_type in (AccountType.REVENUE, AccountType.OTHER_INCOME)
    signed = float(rep.amount) if income else -float(rep.amount)

    cat = recategorize_with_answer(rep.description, signed, sector, payload.answer)
    sub = (cat.subcategory or "").upper() or None

    for tx in txs:
        tx.account_type = cat.account_type
        tx.category = cat.category
        tx.subcategory = sub
        tx.confidence = cat.confidence
        tx.needs_review = cat.needs_review
        tx.review_question = cat.review_question or None
        tx.ai_categorized = True
        tx.source = "ai"
        session.add(tx)

    # Una sola regla de empresa para la contraparte (memoria). Cheques no generan regla.
    if not is_check(rep.description):
        upsert_company_rule(
            session, rep.company_id, sector, cat.subcategory, cat.account_type, cat.category,
            description=rep.description,
        )

    session.commit()
    return {"updated": len(txs)}
