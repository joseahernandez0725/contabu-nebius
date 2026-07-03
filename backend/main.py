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

from dateutil import parser as dateparser
from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, delete, select

from ai import extract_receipt, map_rule_columns, propose_rules, recategorize_with_answer
from audit import audit_categorization
from billing import ai_quota_status, apply_session, create_checkout_session, handle_event
from auth import current_user, member_role, owned_company, owner_company, owned_transaction
from categories import categories_for, categories_for_company
from db import get_session
from ingestion import ingest_csv, make_fingerprint, mask_account, parse_spreadsheet
from models import (
    AccountType, CategoryRule, Company, CompanyCategory, CompanyMember, PnlReport, Role, RuleScope,
    Transaction, User,
)
from reports import income_statement
from rules import (
    apply_rule_to_transactions, build_import_rules, create_company_rule, find_rule_candidates,
    is_check, load_rules, match_rule, upsert_company_rule, validate_keyword,
)


class TransactionUpdate(BaseModel):
    """Campos que se pueden actualizar al confirmar/corregir una revisión.

    `rule_action` (FASE 9 Paso 2) — qué hacer con el REGLAMENTO al corregir la categoría:
    - None (default): comportamiento clásico — aprende/actualiza una regla por frase específica.
    - "only_this": excepción puntual — NO toca ninguna regla (una excepción no reescribe el reglamento).
    - "update_rule": edita LA regla que cubría esta transacción (`matched_rule_id`) y re-alinea
      todas las que matchea (corregir el reglamento, no la transacción).
    """
    account_type: Optional[AccountType] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    needs_review: Optional[bool] = None
    rule_action: Optional[str] = None  # None | "only_this" | "update_rule"


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


class CompanyProfile(BaseModel):
    """Perfil de la empresa que se edita en el onboarding / ajustes (nombre y sector)."""
    name: Optional[str] = None
    sector: Optional[str] = None


class CompanyCreate(BaseModel):
    """Crea una empresa NUEVA bajo la misma cuenta (gestionar varias empresas)."""
    name: Optional[str] = None


class MemberInvite(BaseModel):
    """Invitar a un usuario (por email) a una empresa con un rol (MEMBER o VIEWER)."""
    email: str
    role: Role = Role.MEMBER


class MemberRoleUpdate(BaseModel):
    """Cambiar el rol de un miembro."""
    role: Role


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
# - *.web.app / *.firebaseapp.com → el frontend en Firebase Hosting (dominios por defecto).
# - contabu.com / www → el DOMINIO PROPIO (comprado 26-jun). Sin esto, el navegador bloquea por CORS
#   las llamadas desde https://contabu.com → la app no carga al usuario (parece "no se pudo entrar").
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "https://contabu-prod.web.app",
        "https://contabu-prod.firebaseapp.com",
        "https://contabu.com",
        "https://www.contabu.com",
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
    owned = session.exec(select(Company).where(Company.user_id == user.id)).all()
    roles: dict[int, str] = {c.id: Role.OWNER.value for c in owned}

    # Empresas donde el usuario es MIEMBRO invitado (roles por empresa).
    member_rows = session.exec(
        select(CompanyMember).where(CompanyMember.user_id == user.id)
    ).all()
    member_companies: list[Company] = []
    if member_rows:
        ids = [m.company_id for m in member_rows]
        member_companies = session.exec(select(Company).where(Company.id.in_(ids))).all()
        for m in member_rows:
            roles.setdefault(m.company_id, m.role.value)

    owned_ids = {c.id for c in owned}
    companies = owned + [c for c in member_companies if c.id not in owned_ids]

    if not companies:
        # sector vacío = AÚN NO ELEGIDO → el frontend muestra el onboarding (selector de sector)
        # antes de dejar usar la app. El sector decide reglas de sector + plan de cuentas.
        company = Company(name="My Business", sector="", user_id=user.id)
        session.add(company)
        session.commit()
        session.refresh(company)
        companies = [company]
        roles[company.id] = Role.OWNER.value
    return {"user": user, "companies": companies, "roles": roles}


@app.post("/api/companies")
def create_company(
    payload: CompanyCreate,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Crea una empresa NUEVA bajo la cuenta del usuario (gestionar varias desde una sola cuenta).
    Se crea con `sector=""` → el frontend la lleva al onboarding para elegir su sector."""
    name = (payload.name or "").strip() or "New Business"
    company = Company(name=name, sector="", user_id=user.id)
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


@app.post("/api/companies/{company_id}/transactions/upload")
async def upload_transactions(
    company_id: int,
    file: UploadFile,
    offset: int = Form(0),
    chunk: int = Form(0),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Sube un CSV bancario y dispara el pipeline (mapeo IA → parse → categoriza → guarda).

    La IA categoriza en LOTES de 50 (cada lote = 1 petición a Gemini), así que el archivo se
    importa completo. `limit=1000` es solo un **tope de seguridad** por subida en dev (evita
    disparar la cuota gratis por accidente); el bulk asíncrono real queda para post-deploy
    (deuda técnica #8).
    """
    company = owned_company(company_id, user, session, write=True)
    content = await file.read()
    # chunk>0 → subida por LOTES (cada lote bajo el límite del navegador); si no, tope de seguridad.
    limit = chunk if chunk > 0 else 1000
    # Cupo de IA: lo que exceda el restante NO se categoriza (hay que subir de plan). Se recalcula por
    # lote (las ya insertadas este mes cuentan), así el tope vale en toda la subida troceada.
    quota = ai_quota_status(session, company)
    result = ingest_csv(
        session, company_id, company.sector, content,
        limit=limit, offset=offset, filename=file.filename, ai_limit=quota["remaining"],
    )
    result["quota"] = quota  # used / limit / remaining / monthly → el frontend lo muestra
    return result


@app.post("/api/companies/{company_id}/transactions/receipt")
async def upload_receipt(
    company_id: int,
    file: UploadFile,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Sube la FOTO de un recibo (compra en efectivo/tarjeta) → la IA lee comercio/fecha/total y
    lo categoriza → crea UNA transacción de caja. Pensado sobre todo para compras en EFECTIVO, que
    el extracto bancario no ve. Si el recibo se pagó con TARJETA, se marca para revisión (probable
    duplicado con el extracto). Cada recibo gasta 1 llamada de IA → cuenta contra el cupo."""
    company = owned_company(company_id, user, session, write=True)

    content = await file.read()
    mime = file.content_type or "image/jpeg"
    if not mime.startswith("image/"):
        raise HTTPException(status_code=422, detail="el archivo debe ser una imagen (foto del recibo)")

    # Cupo de IA: un recibo gasta una llamada → si no queda cupo, no se procesa (subir de plan).
    quota = ai_quota_status(session, company)
    if quota["remaining"] <= 0:
        return {"created": 0, "quota_blocked": 1, "quota": quota}

    extracted = extract_receipt(content, mime, company.sector)
    if not extracted.is_receipt:
        # La imagen no parece un recibo → no se crea nada (no se cobra cupo).
        return {"created": 0, "not_a_receipt": True, "quota": quota}

    # Fecha del recibo (si no se leyó, hoy). Total = magnitud; una compra es dinero que SALE.
    try:
        d = dateparser.parse(extracted.purchase_date).date() if extracted.purchase_date else date.today()
    except (ValueError, OverflowError):
        d = date.today()
    amount = abs(Decimal(str(extracted.total or 0)))
    description = f"{extracted.merchant} (receipt)".strip()
    signed = -amount  # compra → salida (el signo lo refleja is_inflow=False)
    fp = make_fingerprint(d, description, signed, None)

    # Dedup: si ya se subió esta misma foto/recibo (misma huella), no duplicar.
    exists = session.exec(
        select(func.count()).select_from(Transaction)
        .where(Transaction.company_id == company_id, Transaction.fingerprint == fp)
    ).one()
    if exists:
        return {"created": 0, "duplicate": True, "quota": quota}

    tx = Transaction(
        company_id=company_id, date=d, description=description, amount=amount,
        is_inflow=False, bank_account=None, fingerprint=fp,
        account_type=extracted.account_type, subcategory=(extracted.merchant or "").upper() or None,
        confidence=extracted.confidence, ai_categorized=True, source="ai",
    )

    # Consistencia: si el dueño YA tiene una regla para este comercio, mandar su categoría (la regla
    # ganó antes para el extracto bancario → mismo comercio, misma categoría). Si no, la de la IA.
    rule = match_rule(load_rules(session, company_id, company.sector), extracted.merchant)
    if rule is not None:
        tx.account_type = rule.account_type
        tx.category = rule.category
        tx.subcategory = rule.match_text.upper()
        tx.confidence = 1.0
        tx.needs_review = False
        tx.source = "rule"
        tx.matched_rule_id = rule.id  # procedencia: qué regla decidió
    else:
        tx.category = extracted.category
        tx.needs_review = extracted.needs_review
        tx.review_question = extracted.review_question or None

    # No se pudo leer el total → no sirve para el P&L hasta que el dueño lo confirme.
    if amount <= 0:
        tx.needs_review = True
        tx.review_question = "We couldn't read the total on this receipt — what was the amount?"

    # Doble conteo: si el recibo se pagó con TARJETA, también caerá en el extracto bancario → avisar.
    if extracted.payment_method == "card":
        tx.needs_review = True
        tx.review_question = (
            "This receipt was paid by card, so it may also appear in your bank statement — "
            "keep it only if it isn't already there (to avoid double-counting)."
        )

    session.add(tx)
    session.commit()
    session.refresh(tx)

    quota = ai_quota_status(session, company)  # recalcular tras crear la tx
    return {
        "created": 1,
        "transaction_id": tx.id,
        "payment_method": extracted.payment_method,
        "transaction": {
            "date": str(d), "description": description, "amount": str(amount),
            "account_type": tx.account_type.value if tx.account_type else None,
            "category": tx.category, "subcategory": tx.subcategory,
            "confidence": tx.confidence, "needs_review": tx.needs_review,
            "review_question": tx.review_question, "source": tx.source,
        },
        "quota": quota,
    }


@app.post("/api/companies/{company_id}/transactions/set-account")
def set_transactions_account(
    company_id: int,
    payload: SetAccountPayload,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Asigna la cuenta bancaria (que indicó el usuario) a varias transacciones de la empresa.
    Se guarda enmascarada a los últimos 4 dígitos. Útil cuando la IA no la detectó del archivo."""
    owned_company(company_id, user, session, write=True)
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


@app.patch("/api/companies/{company_id}")
def update_company(
    company_id: int,
    payload: CompanyProfile,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Edita el perfil de la empresa (nombre/sector). Lo usa el onboarding y el renombrar en Ajustes.
    Solo el OWNER (es un ajuste de la empresa)."""
    company = owner_company(company_id, user, session)
    data = payload.model_dump(exclude_unset=True)
    if data.get("name"):
        company.name = data["name"].strip()
    if data.get("sector"):
        company.sector = data["sector"].strip().lower()
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


@app.delete("/api/companies/{company_id}")
def delete_company(
    company_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Elimina una empresa y TODOS sus datos (transacciones, reglas de empresa, categorías
    personalizadas, reportes P&L, miembros). Solo el OWNER. No borra su ÚNICA empresa."""
    owner_company(company_id, user, session)
    mine = session.exec(select(Company).where(Company.user_id == user.id)).all()
    if len(mine) <= 1:
        raise HTTPException(status_code=400, detail="No puedes eliminar tu única empresa.")
    # Borrar los datos hijos (reglas de SECTOR no se tocan: tienen company_id NULL).
    for model in (Transaction, CategoryRule, CompanyCategory, PnlReport, CompanyMember):
        session.exec(delete(model).where(model.company_id == company_id))
    company = session.get(Company, company_id)
    session.delete(company)
    session.commit()
    return {"deleted": company_id}


# --- Equipo: roles y permisos por empresa (gestión por el OWNER) ---

@app.get("/api/companies/{company_id}/members")
def list_members(
    company_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Equipo de la empresa: el dueño (OWNER) + los miembros invitados. Cualquier miembro lo ve."""
    company = owned_company(company_id, user, session)
    owner = session.get(User, company.user_id) if company.user_id else None
    out = [{"id": None, "email": owner.email if owner else None,
            "role": Role.OWNER.value, "is_owner": True, "pending": False}]
    members = session.exec(
        select(CompanyMember).where(CompanyMember.company_id == company_id)
    ).all()
    for m in members:
        out.append({"id": m.id, "email": m.email, "role": m.role.value,
                    "is_owner": False, "pending": m.user_id is None})
    return out


@app.post("/api/companies/{company_id}/members")
def invite_member(
    company_id: int,
    payload: MemberInvite,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Invita (por email) a un usuario con rol MEMBER o VIEWER. Solo el OWNER. Si el email ya tiene
    cuenta, queda enlazado al instante; si no, queda pendiente y se enlaza cuando inicie sesión."""
    owner_company(company_id, user, session)
    email = payload.email.strip().lower()
    if not email:
        raise HTTPException(status_code=422, detail="Email requerido.")
    if payload.role == Role.OWNER:
        raise HTTPException(status_code=422, detail="El dueño no se invita; transferir dueño no está soportado.")
    existing = session.exec(
        select(CompanyMember)
        .where(CompanyMember.company_id == company_id)
        .where(CompanyMember.email == email)
    ).first()
    if existing:
        existing.role = payload.role
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing
    invited = session.exec(select(User).where(User.email == email)).first()
    m = CompanyMember(company_id=company_id, email=email, role=payload.role,
                      user_id=invited.id if invited else None)
    session.add(m)
    session.commit()
    session.refresh(m)
    return m


@app.patch("/api/companies/{company_id}/members/{member_id}")
def update_member_role(
    company_id: int,
    member_id: int,
    payload: MemberRoleUpdate,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Cambia el rol (MEMBER/VIEWER) de un miembro. Solo el OWNER."""
    owner_company(company_id, user, session)
    if payload.role == Role.OWNER:
        raise HTTPException(status_code=422, detail="No se puede asignar OWNER.")
    m = session.get(CompanyMember, member_id)
    if m is None or m.company_id != company_id:
        raise HTTPException(status_code=404, detail="Miembro no encontrado.")
    m.role = payload.role
    session.add(m)
    session.commit()
    session.refresh(m)
    return m


@app.delete("/api/companies/{company_id}/members/{member_id}")
def remove_member(
    company_id: int,
    member_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Quita a un miembro de la empresa (pierde el acceso). Solo el OWNER."""
    owner_company(company_id, user, session)
    m = session.get(CompanyMember, member_id)
    if m is None or m.company_id != company_id:
        raise HTTPException(status_code=404, detail="Miembro no encontrado.")
    session.delete(m)
    session.commit()
    return {"deleted": member_id}


@app.patch("/api/companies/{company_id}/thresholds")
def update_thresholds(
    company_id: int,
    payload: CompanyThresholds,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Edita los umbrales de monto (super/gasolinera) que usa la desambiguación por banda."""
    company = owned_company(company_id, user, session, write=True)
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
    company = owned_company(company_id, user, session, write=True)
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
    owned_company(company_id, user, session, write=True)
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
    owned_company(company_id, user, session, write=True)
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
    owned_company(company_id, user, session, write=True)
    rule = _owned_company_rule(company_id, rule_id, session)
    session.delete(rule)
    session.commit()
    return {"deleted": rule_id}


class CheckoutPayload(BaseModel):
    quota: int  # tier M5: 100 | 200 | 400 | 1000 (transacciones/mes)


class ConfirmPayload(BaseModel):
    session_id: str


@app.post("/api/companies/{company_id}/checkout")
def create_checkout(
    company_id: int,
    payload: CheckoutPayload,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Crea la sesión de Stripe Checkout para el tier elegido y devuelve la URL de pago (el frontend
    redirige allá). No toca tarjetas — Stripe aloja la página."""
    company = owned_company(company_id, user, session, write=True)
    try:
        url = create_checkout_session(session, company, payload.quota, user.email)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"url": url}


@app.post("/api/companies/{company_id}/checkout/confirm")
def confirm_checkout(
    company_id: int,
    payload: ConfirmPayload,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Tras el redirect de éxito de Stripe: confirma el pago y deja la empresa suscrita (status + cupo).
    Permite probar e2e sin webhooks; el webhook cubre renovaciones/cancelaciones."""
    owned_company(company_id, user, session, write=True)
    return apply_session(session, payload.session_id)


@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request, session: Session = Depends(get_session)):
    """Webhook de Stripe (PÚBLICO, sin auth — lo llama Stripe; se verifica por firma). Refleja en la
    empresa los cambios de suscripción (renovación/cancelación)."""
    payload = await request.body()
    sig = request.headers.get("stripe-signature")
    try:
        return handle_event(session, payload, sig)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"webhook error: {e}")


@app.get("/api/companies/{company_id}/audit")
def audit_company(
    company_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """AGENTE AUDITOR (1 click): escanea las transacciones categorizadas y devuelve los **sospechosos**
    (inconsistencia por comercio, contradicción de signo, sin categorizar). Determinista, sin IA."""
    owned_company(company_id, user, session)
    txs = session.exec(
        select(Transaction).where(Transaction.company_id == company_id)
    ).all()
    items = [{
        "id": t.id, "description": t.description,
        "account_type": t.account_type.value if t.account_type else None,
        "category": t.category, "is_inflow": t.is_inflow,
    } for t in txs]
    return audit_categorization(items)


@app.post("/api/companies/{company_id}/rule-proposals")
def generate_rule_proposals(
    company_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """AGENTE PROPONEDOR: mira las transacciones ya categorizadas, encuentra los comercios recurrentes
    SIN regla (determinista) y pide a la IA reglas COMPANY propuestas (mergea marcas, no duplica las
    existentes, salta gas/super y persona-a-persona). Devuelve propuestas para que el dueño APRUEBE
    (no crea nada todavía)."""
    company = owned_company(company_id, user, session)
    sector = company.sector if company else "general"
    rules = load_rules(session, company_id, sector)
    txs = session.exec(
        select(Transaction).where(Transaction.company_id == company_id)
    ).all()
    items = [{
        "description": t.description, "account_type": t.account_type.value if t.account_type else None,
        "category": t.category, "is_inflow": t.is_inflow,
    } for t in txs]
    candidates = find_rule_candidates(rules, items)
    proposals = propose_rules(candidates, [r.match_text for r in rules], sector)
    # EL GUARDIÁN (FASE 9 Paso 3): validar cada keyword propuesto contra los datos reales ANTES de
    # mostrarlo — cobertura (¿matchea todo su comercio?) y colisión (¿caza otros comercios?). El
    # dueño aprueba con evidencia ("cubre N · M conflictos"), no con fe. Determinista, sin IA.
    return [{
        "keyword": p.keyword, "account_type": p.account_type.value, "category": p.category,
        "covers": p.covers, "reasoning": p.reasoning,
        **validate_keyword(p.keyword, p.covers, items),
    } for p in proposals if p.kind == "rule"]


class RuleProposalValidate(BaseModel):
    """Un keyword (posiblemente EDITADO por el dueño) a re-validar contra los datos."""
    keyword: str
    covers: list[str] = []  # firmas de comercio que la propuesta cubre (para medir colisión)


@app.post("/api/companies/{company_id}/rule-proposals/validate")
def validate_rule_proposal(
    company_id: int,
    payload: RuleProposalValidate,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Re-valida un keyword contra las transacciones reales (cobertura/colisión) — se usa cuando el
    dueño EDITA el keyword de una propuesta antes de aprobarla. Determinista, sin IA."""
    owned_company(company_id, user, session)
    txs = session.exec(
        select(Transaction).where(Transaction.company_id == company_id)
    ).all()
    items = [{"description": t.description} for t in txs]
    return validate_keyword(payload.keyword, payload.covers, items)


class RuleProposalApply(BaseModel):
    """Una regla aprobada del proponedor (lista para crear)."""
    keyword: str
    account_type: AccountType
    category: str


class RuleProposalsApply(BaseModel):
    rules: list[RuleProposalApply]


@app.post("/api/companies/{company_id}/rule-proposals/apply")
def apply_rule_proposals(
    company_id: int,
    payload: RuleProposalsApply,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Crea las reglas que el dueño APROBÓ del proponedor (keyword tal cual, preserva `*`) y las
    re-aplica a las transacciones existentes (los libros se ajustan). Devuelve cuántas reglas creó
    y cuántas transacciones alineó."""
    company = owned_company(company_id, user, session, write=True)
    sector = company.sector if company else "general"
    created = 0
    applied = 0
    for p in payload.rules:
        rule = create_company_rule(session, company_id, sector, p.keyword, p.account_type, p.category)
        if rule is not None:
            created += 1
            session.flush()  # asigna id para apply_rule_to_transactions
            applied += apply_rule_to_transactions(session, company_id, rule)
    session.commit()
    return {"created": created, "applied": applied}


@app.post("/api/companies/{company_id}/rules/import")
async def import_rules_preview(
    company_id: int,
    file: UploadFile,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Sube el archivo de REGLAS del usuario (CSV, columnas COMO SEA). La IA mapea las columnas y
    devuelve un PREVIEW (keyword, category, account_type, status) para que el usuario confirme. NO
    crea nada — las aprobadas se crean con `/rule-proposals/apply` (misma forma)."""
    company = owned_company(company_id, user, session)
    sector = company.sector if company else "general"
    content = await file.read()
    headers, rows = parse_spreadsheet(content, file.filename)
    if not headers or not rows:
        raise HTTPException(status_code=422, detail="Archivo vacío o ilegible.")
    mapping = map_rule_columns(headers, rows[:5])
    existing = [r.match_text for r in load_rules(session, company_id, sector)]
    preview = build_import_rules(
        headers, rows, mapping.keyword_column, mapping.category_column,
        mapping.account_type_column, sector, existing,
    )
    return {
        "mapping": {
            "keyword": mapping.keyword_column, "category": mapping.category_column,
            "account_type": mapping.account_type_column, "subcategory": mapping.subcategory_column,
        },
        "rules": preview,
    }


@app.post("/api/companies/{company_id}/rules/cleanup")
def cleanup_rules(
    company_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Higiene: borra las reglas de empresa que **no matchean ninguna transacción** (afecta 0) —
    reglas 'muertas' (ver IDEAS #4: las reglas se pudren). Devuelve cuántas eliminó."""
    owned_company(company_id, user, session, write=True)
    rules = session.exec(
        select(CategoryRule)
        .where(CategoryRule.scope == RuleScope.COMPANY)
        .where(CategoryRule.company_id == company_id)
    ).all()
    txs = session.exec(
        select(Transaction).where(Transaction.company_id == company_id)
    ).all()
    removed = 0
    for r in rules:
        if not any(match_rule([r], t.description or "") is not None for t in txs):
            session.delete(r)
            removed += 1
    session.commit()
    return {"removed": removed}


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
    owned_company(company_id, user, session, write=True)
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
    owned_company(company_id, user, session, write=True)
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
    owned_company(company_id, user, session, write=True)
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
    owned_company(company_id, user, session, write=True)
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
    tx = owned_transaction(transaction_id, user, session, write=True)
    data = payload.model_dump(exclude_unset=True)
    rule_action = data.pop("rule_action", None)  # intención sobre el reglamento (no es campo de tx)
    if data.get("subcategory"):
        data["subcategory"] = data["subcategory"].upper()  # subcategorías siempre en MAYÚSCULA
    for field, value in data.items():
        setattr(tx, field, value)
    # Si el usuario tocó la categorización, la intención sobre el REGLAMENTO decide (FASE 9 Paso 2):
    # "only_this" = excepción (no toca reglas) · "update_rule" = edita la regla que la cubría ·
    # default = aprende/actualiza regla por frase específica (comportamiento clásico).
    applied = 0  # cuántas OTRAS transacciones alineó la regla (para feedback "en vivo")
    rule_updated = False
    if any(k in data for k in ("account_type", "category", "subcategory")):
        prev_rule_id = tx.matched_rule_id  # QUÉ regla la cubría (antes de limpiar la procedencia)
        tx.source = "manual"
        tx.matched_rule_id = None  # la decidió el dueño, ya no una regla (procedencia)
        if rule_action == "only_this":
            pass  # excepción puntual: una excepción no reescribe el reglamento
        elif rule_action == "update_rule" and prev_rule_id is not None:
            rule = session.get(CategoryRule, prev_rule_id)
            if (rule is not None and rule.company_id == tx.company_id
                    and tx.account_type is not None and tx.category):
                rule.account_type = tx.account_type
                rule.category = tx.category
                session.add(rule)
                rule_updated = True
                # Corregir el reglamento, no la transacción: la tx queda cubierta por SU regla
                # actualizada (procedencia exacta, verde), y las demás se re-alinean.
                tx.source = "rule"
                tx.matched_rule_id = rule.id
                tx.confidence = 1.0
                applied = apply_rule_to_transactions(session, tx.company_id, rule, exclude_id=tx.id)
        # Un cheque no genera regla (cada cheque es distinto; ver rules.is_check).
        elif not is_check(tx.description):
            company = session.get(Company, tx.company_id)
            rule = upsert_company_rule(
                session, tx.company_id, company.sector if company else "general",
                tx.subcategory, tx.account_type, tx.category,
                description=tx.description,
            )
            # Corregir una → corregir todas: alinea las demás transacciones del mismo comercio.
            if rule is not None:
                applied = apply_rule_to_transactions(session, tx.company_id, rule, exclude_id=tx.id)
    session.add(tx)
    session.commit()
    session.refresh(tx)
    # Devuelve la tx + cuántas OTRAS alineó la regla → el frontend lo muestra y re-pinta de verde.
    return {"id": tx.id, "applied": applied, "rule_updated": rule_updated}


@app.post("/api/transactions/{transaction_id}/clarify")
def clarify_transaction(
    transaction_id: int,
    payload: ClarifyPayload,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """El dueño explica en lenguaje simple qué fue el movimiento → la IA re-categoriza
    con esa info y se guarda una REGLA de empresa (memoria) para la próxima vez."""
    tx = owned_transaction(transaction_id, user, session, write=True)

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
    tx.matched_rule_id = None  # ya no la decide una regla (procedencia)
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
    txs = [owned_transaction(tid, user, session, write=True) for tid in payload.transaction_ids]
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
        tx.matched_rule_id = None  # ya no la decide una regla (procedencia)
        session.add(tx)

    # Una sola regla de empresa para la contraparte (memoria). Cheques no generan regla.
    if not is_check(rep.description):
        upsert_company_rule(
            session, rep.company_id, sector, cat.subcategory, cat.account_type, cat.category,
            description=rep.description,
        )

    session.commit()
    return {"updated": len(txs)}
