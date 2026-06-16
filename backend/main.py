"""
ContaBu — backend (FastAPI).
Endpoints de la API: prueba (/api/hello) e ingestión de archivos bancarios.
"""

from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlmodel import Session, select

from ai import recategorize_with_answer
from db import get_session
from ingestion import ingest_csv
from models import AccountType, CategoryRule, Company, RuleScope, Transaction
from reports import income_statement


class TransactionUpdate(BaseModel):
    """Campos que se pueden actualizar al confirmar/corregir una revisión."""
    account_type: Optional[AccountType] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    needs_review: Optional[bool] = None


class ClarifyPayload(BaseModel):
    """Respuesta del dueño en lenguaje simple ('¿qué fue / en qué consistió?')."""
    answer: str

# `app` es nuestra aplicación. uvicorn la "sirve" y queda esperando peticiones.
app = FastAPI(title="ContaBu API")

# CORS: permite que el frontend (que corre en otro puerto, el 5173)
# pueda llamar a este backend (puerto 8000). Sin esto, el navegador lo bloquea.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/hello")
def hello():
    """Endpoint de prueba: responde un JSON. El frontend lo pedirá y lo mostrará."""
    return {"message": "¡Hola desde el backend de ContaBu! 🚀"}


@app.post("/api/companies/{company_id}/transactions/upload")
async def upload_transactions(
    company_id: int,
    file: UploadFile,
    session: Session = Depends(get_session),
):
    """Sube un CSV bancario y dispara el pipeline (mapeo IA → parse → categoriza → guarda).

    `limit` está fijo en dev para no agotar el rate limit del tier gratis; se quitará al
    resolver el bulk asíncrono (ver FASE 5.md).
    """
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="company not found")

    content = await file.read()
    result = ingest_csv(
        session, company_id, company.sector, content, limit=20
    )
    return result


@app.get("/api/companies/{company_id}")
def get_company(company_id: int, session: Session = Depends(get_session)):
    """Devuelve la empresa (para el nombre en el header, etc.)."""
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="company not found")
    return company


@app.get("/api/companies/{company_id}/transactions")
def list_transactions(company_id: int, session: Session = Depends(get_session)):
    """Lista las transacciones de una empresa (más recientes primero)."""
    return session.exec(
        select(Transaction)
        .where(Transaction.company_id == company_id)
        .order_by(Transaction.date.desc())
    ).all()


@app.get("/api/companies/{company_id}/income-statement")
def get_income_statement(company_id: int, session: Session = Depends(get_session)):
    """Estado de resultados (P&L) calculado en SQL. Ver reports.income_statement."""
    return income_statement(session, company_id)


@app.get("/api/companies/{company_id}/transactions/review")
def list_review(company_id: int, session: Session = Depends(get_session)):
    """Transacciones que la IA marcó como dudosas (necesitan confirmación humana)."""
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
    session: Session = Depends(get_session),
):
    """Actualiza una transacción (confirmar o corregir una revisión)."""
    tx = session.get(Transaction, transaction_id)
    if tx is None:
        raise HTTPException(status_code=404, detail="transaction not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(tx, field, value)
    session.add(tx)
    session.commit()
    session.refresh(tx)
    return tx


@app.post("/api/transactions/{transaction_id}/clarify")
def clarify_transaction(
    transaction_id: int,
    payload: ClarifyPayload,
    session: Session = Depends(get_session),
):
    """El dueño explica en lenguaje simple qué fue el movimiento → la IA re-categoriza
    con esa info y se guarda una REGLA de empresa (memoria) para la próxima vez."""
    tx = session.get(Transaction, transaction_id)
    if tx is None:
        raise HTTPException(status_code=404, detail="transaction not found")

    company = session.get(Company, tx.company_id)
    sector = company.sector if company else "general"

    # Reconstruir la dirección (entrada/salida) desde el account_type actual.
    income = tx.account_type in (AccountType.REVENUE, AccountType.OTHER_INCOME)
    signed = float(tx.amount) if income else -float(tx.amount)

    cat = recategorize_with_answer(tx.description, signed, sector, payload.answer)

    tx.account_type = cat.account_type
    tx.category = cat.category
    tx.subcategory = cat.subcategory
    tx.confidence = cat.confidence
    tx.needs_review = cat.needs_review
    tx.review_question = cat.review_question or None
    tx.ai_categorized = True
    session.add(tx)

    # Memoria del cliente: regla de empresa para que la próxima vez se aplique sola.
    if cat.subcategory:
        session.add(CategoryRule(
            scope=RuleScope.COMPANY,
            company_id=tx.company_id,
            sector=sector,
            match_text=cat.subcategory,
            account_type=cat.account_type,
            category=cat.category,
        ))

    session.commit()
    session.refresh(tx)
    return tx
