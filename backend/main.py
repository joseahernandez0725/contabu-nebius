"""
ContaBu — backend (FastAPI).
Endpoints de la API: prueba (/api/hello), identidad (/api/me) e ingestión de archivos bancarios.

Multi-tenant (deuda #2 🔴): la identidad sale SIEMPRE del usuario autenticado (token Firebase,
ver `auth.py`); los endpoints validan que la empresa/transacción pertenezca al usuario antes de
tocarla. El `company_id` de la URL ya NO se confía a ciegas.
"""

from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlmodel import Session, select

from ai import recategorize_with_answer
from auth import current_user, owned_company, owned_transaction
from categories import categories_for
from db import get_session
from ingestion import ingest_csv, mask_account
from models import AccountType, Company, Transaction, User
from reports import income_statement
from rules import upsert_company_rule


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
    """Plan de cuentas cerrado del sector de la empresa: {account_type: [categorías]}.
    Lo usa el frontend para el selector de categoría (y derivar el account_type)."""
    company = owned_company(company_id, user, session)
    return categories_for(company.sector)


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
        company = session.get(Company, tx.company_id)
        upsert_company_rule(
            session, tx.company_id, company.sector if company else "general",
            tx.subcategory, tx.account_type, tx.category,
            description=tx.description,
        )
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
    upsert_company_rule(
        session, tx.company_id, sector, cat.subcategory, cat.account_type, cat.category,
        description=tx.description,
    )

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

    # Una sola regla de empresa para la contraparte (memoria).
    upsert_company_rule(
        session, rep.company_id, sector, cat.subcategory, cat.account_type, cat.category,
        description=rep.description,
    )

    session.commit()
    return {"updated": len(txs)}
