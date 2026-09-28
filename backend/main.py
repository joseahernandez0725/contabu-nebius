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
from company_profile import (
    apply_person_answer, format_profile_facts, interview_questions, sanitize_round1,
)
from db import get_session
from ingestion import categorize_pending, ingest_csv, make_fingerprint, mask_account, parse_spreadsheet
from models import (
    AccountType, CategoryRule, Company, CompanyCategory, CompanyMember, PnlReport, Role, RuleScope,
    Transaction, User,
    SheetLabel,
    TransactionLabel,
    CellFormat,)
from reports import pivot, pivot_leaves, income_statement
from rules import (
    apply_rule_to_transactions, build_import_rules, create_company_rule, find_rule_candidates,
    is_check, load_rules, match_rule, matchable_text, upsert_company_rule, validate_keyword,
)


class TransactionUpdate(BaseModel):
    """Campos que se pueden actualizar al confirmar/corregir una revisión.

    `rule_action` (FASE 9 Paso 2) — qué hacer con el REGLAMENTO al corregir la categoría:
    - None (default): comportamiento clásico — aprende/actualiza una regla por frase específica.
    - "only_this": excepción puntual — NO toca ninguna regla (una excepción no reescribe el reglamento).
    - "update_rule": edita LA regla que cubría esta transacción (`matched_rule_id`) y re-alinea
      todas las que matchea (corregir el reglamento, no la transacción).
    - "follow_rule": lo contrario de escribir a mano — la celda vuelve a ser "fórmula". Busca la
      regla que matchearía esta descripción y la aplica, restaurando `matched_rule_id`.
    """
    account_type: Optional[AccountType] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    needs_review: Optional[bool] = None
    note: Optional[str] = None   # nota libre del bookkeeper sobre ESTA línea (FASE 10)
    # Texto que el bookkeeper AGREGA a la descripción del banco (no la reemplaza). Entra al matcheo.
    description_extra: Optional[str] = None
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
    subcategory: Optional[str] = None  # comercio presentable que asigna la regla ("" → limpiar)


class RuleCreate(BaseModel):
    """Regla creada A MANO por el dueño desde la tabla de reglas (hallazgo 8.10: el reglamento
    también se escribe directo, no solo vía correcciones/propuestas)."""
    match_text: str
    account_type: AccountType
    category: str
    subcategory: Optional[str] = None


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
        profile=company.profile,  # los hechos de la entrevista viajan al prompt (FASE 9 Paso 5)
    )
    result["quota"] = quota  # used / limit / remaining / monthly → el frontend lo muestra
    # AUDITOR AUTOMÁTICO (FASE 9 Paso 4): al terminar la subida (último lote), el agente revisa los
    # libros solo — sin que el dueño lo pida — y el frontend muestra el badge con los hallazgos.
    # Solo en el último lote (correr por lote sería redundante); determinista y gratis (sin IA).
    is_last_chunk = result.get("offset", 0) + result.get("processed", 0) >= result.get("total", 0)
    if is_last_chunk:
        result["audit_findings"] = len(_company_audit(session, company_id))
    return result


@app.get("/api/companies/{company_id}/quota")
def get_quota(
    company_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Cupo de IA vigente (used/limit/remaining/monthly). El frontend decide con esto qué ofrecer:
    con cupo → "categorizar ahora"; sin cupo → mandar a suscribirse (Billing)."""
    company = owned_company(company_id, user, session)
    return ai_quota_status(session, company)


@app.post("/api/companies/{company_id}/transactions/categorize-pending")
def categorize_pending_endpoint(
    company_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Categoriza las transacciones que quedaron SIN categorizar (bloqueadas por el cupo de IA en
    una subida, o fallo puntual de la IA). Es el cobro de la promesa "sube de plan para
    categorizarlas": tras suscribirse (o al renovarse el cupo mensual), un toque las resuelve sin
    re-subir el archivo. Respeta el cupo vigente y re-corre el auditor al final."""
    company = owned_company(company_id, user, session, write=True)
    quota = ai_quota_status(session, company)
    result = categorize_pending(session, company, ai_limit=quota["remaining"])
    result["quota"] = ai_quota_status(session, company)  # estado DESPUÉS (lo que quedó de cupo)
    # Igual que la subida: el auditor revisa solo y el frontend refresca el badge.
    result["audit_findings"] = len(_company_audit(session, company_id))
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
        # Predicción ORIGINAL de la IA (FASE 9 Paso 6): congelada para la precisión online.
        tx.ai_original_account_type = extracted.account_type.value if extracted.account_type else None
        tx.ai_original_category = extracted.category

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


@app.patch("/api/companies/{company_id}/profile")
def update_profile(
    company_id: int,
    payload: dict,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """ENTREVISTA del bookkeeper — RONDA 1 (FASE 9 Paso 5): guarda las respuestas estructuradas
    del dueño (valores SIEMPRE en inglés — la UI traduce, el dato no). Merge: se puede responder
    por partes o volver a editar; las claves no reconocidas se descartan."""
    company = owned_company(company_id, user, session, write=True)
    answers = sanitize_round1(payload)
    profile = dict(company.profile or {})
    profile.update(answers)
    company.profile = profile
    session.add(company)
    session.commit()
    return {"profile": profile}


@app.get("/api/companies/{company_id}/interview")
def get_interview(
    company_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """ENTREVISTA — RONDA 2 (FASE 9 Paso 5): preguntas ANCLADAS EN LOS DATOS. Determinista y sin
    IA: agrupa los pagos persona-a-persona (Zelle/Venmo/…) por contraparte — exactamente los que
    el proponedor tiene prohibido convertir en regla sin contexto — y pregunta el rol de cada
    persona aún sin confirmar. Los totales los suma Python (la IA no calcula)."""
    company = owned_company(company_id, user, session)
    txs = session.exec(
        select(Transaction).where(Transaction.company_id == company_id)
    ).all()
    return {"profile": company.profile or {},
            "questions": interview_questions(txs, company.profile)}


class InterviewAnswer(BaseModel):
    """El rol de UNA persona, confirmado por el dueño (ronda 2)."""
    name: str
    role: str  # "subcontractor" | "customer" | "owner" | "skip" (no volver a preguntar)


class InterviewAnswers(BaseModel):
    answers: list[InterviewAnswer]


@app.post("/api/companies/{company_id}/interview/answers")
def answer_interview(
    company_id: int,
    payload: InterviewAnswers,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Aplica las respuestas de la RONDA 2: (a) el hecho queda en el perfil (`people`) — el
    proponedor ya puede proponer la regla de esa persona y la IA lo recibe como hecho—, y (b) las
    transacciones persona-a-persona de esa contraparte se corrigen al instante (decisión del
    dueño → source="manual"). "skip" solo silencia la pregunta."""
    company = owned_company(company_id, user, session, write=True)
    profile = dict(company.profile or {})
    people = dict(profile.get("people") or {})
    updated = 0
    for a in payload.answers:
        key = a.name.strip().upper()
        if not key:
            continue
        role = a.role if a.role in ("subcontractor", "customer", "owner") else "skip"
        people[key] = {"name": a.name.strip().title(), "role": role}
        if role != "skip":
            updated += apply_person_answer(session, company_id, company.sector or "general",
                                           key, role)
    profile["people"] = people
    company.profile = profile
    session.add(company)
    session.commit()
    return {"updated": updated, "profile": profile}


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
        matches = sum(1 for t in txs
                      if match_rule([r], matchable_text(t.description, t.description_extra)) is not None)
        out.append({
            "id": r.id, "match_text": r.match_text,
            "account_type": r.account_type.value if r.account_type else None,
            "category": r.category, "subcategory": r.subcategory, "matches": matches,
            # `scope`/`sector`: si la regla es de ESTE libro o de todo un sector (la hoja de reglas
            # los muestra como una sola columna "Sector": "This book" o el nombre del sector).
            "scope": r.scope.value, "sector": r.sector,
        })
    out.sort(key=lambda x: x["matches"], reverse=True)
    return out


@app.post("/api/companies/{company_id}/rules")
def create_rule(
    company_id: int,
    payload: RuleCreate,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Crea una regla A MANO y la aplica a las transacciones existentes que matchee. El frontend
    valida el keyword con el guardián (cobertura/colisiones) ANTES de llamar aquí."""
    company = owned_company(company_id, user, session, write=True)
    match_text = payload.match_text.strip().upper()
    if len(match_text) < 2:
        raise HTTPException(status_code=422, detail="keyword demasiado corto")
    dup = session.exec(
        select(CategoryRule)
        .where(CategoryRule.scope == RuleScope.COMPANY)
        .where(CategoryRule.company_id == company_id)
        .where(CategoryRule.match_text == match_text)
    ).first()
    if dup:
        raise HTTPException(status_code=409, detail="ya existe una regla con ese keyword")
    chart = categories_for_company(session, company_id, company.sector)  # sector + propias
    if payload.category not in (chart.get(payload.account_type.value) or []):
        raise HTTPException(status_code=422, detail="categoría fuera del plan de cuentas")
    rule = CategoryRule(
        scope=RuleScope.COMPANY, company_id=company_id, match_text=match_text,
        account_type=payload.account_type, category=payload.category,
        subcategory=(payload.subcategory or "").strip() or None,
    )
    session.add(rule)
    applied = apply_rule_to_transactions(session, company_id, rule)
    session.commit()
    session.refresh(rule)
    return {"id": rule.id, "match_text": rule.match_text,
            "account_type": rule.account_type.value, "category": rule.category,
            "subcategory": rule.subcategory, "applied": applied}


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
    if "subcategory" in data:  # "" desde la UI = limpiar (volver al match_text)
        data["subcategory"] = (data["subcategory"] or "").strip() or None
    for field, value in data.items():
        setattr(rule, field, value)
    session.add(rule)
    applied = apply_rule_to_transactions(session, company_id, rule)
    session.commit()
    session.refresh(rule)
    return {"id": rule.id, "match_text": rule.match_text,
            "account_type": rule.account_type.value if rule.account_type else None,
            "category": rule.category, "subcategory": rule.subcategory, "applied": applied}


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


def _company_audit(session: Session, company_id: int) -> list[dict]:
    """Corre el AUDITOR determinista sobre los datos vivos de la empresa (SQL + Python, sin IA →
    gratis). Incluye la señal de DESVÍO DE REGLA exacta (matched_rule_id vs la regla viva).
    Se usa en el endpoint de auditoría Y automáticamente tras cada subida / aplicar reglas (FASE 9)."""
    txs = session.exec(
        select(Transaction).where(Transaction.company_id == company_id)
    ).all()
    items = [{
        "id": t.id, "description": t.description,
        "account_type": t.account_type.value if t.account_type else None,
        "category": t.category, "is_inflow": t.is_inflow,
        "matched_rule_id": t.matched_rule_id,
    } for t in txs]
    rules = session.exec(
        select(CategoryRule).where(CategoryRule.company_id == company_id)
    ).all()
    rules_by_id = {r.id: {
        "match_text": r.match_text,
        "account_type": r.account_type.value if r.account_type else None,
        "category": r.category,
    } for r in rules}
    return audit_categorization(items, rules_by_id)


def _audit_by_transaction(session: Session, company_id: int) -> dict[int, dict]:
    """Invierte la salida del AUDITOR (hallazgos por GRUPO, con `transaction_ids`) a un mapa
    **por transacción** → la "observación" de la hoja (FASE 10, paso 6).

    El auditor agrupa (una inconsistencia cubre varias transacciones del mismo comercio); la hoja
    necesita un renglón por fila. Si una transacción cae en varios hallazgos, gana el más severo.
    """
    order = {"high": 3, "medium": 2, "low": 1}
    by_tx: dict[int, dict] = {}
    for f in _company_audit(session, company_id):
        # Se pasa TODO el hallazgo menos lo voluminoso: el frontend traduce por `type` usando los
        # campos estructurados (account_type, direction, merchant…), no el texto crudo.
        obs = {k: v for k, v in f.items() if k not in ("transaction_ids", "examples")}
        obs["text"] = f.get("detail")
        for tid in f.get("transaction_ids", []):
            prev = by_tx.get(tid)
            if prev is None or order.get(obs["severity"], 0) > order.get(prev["severity"], 0):
                by_tx[tid] = obs
    return by_tx


# ─────────────────────────────────────────────────────────────────────────────────────────────
# LA HOJA (FASE 10): etiquetas con significado + formato libre de celda.
# Son dos cosas a propósito: la etiqueta es DATO (se filtra, la IA la lee); el formato es
# PRESENTACIÓN (no entra al P&L ni lo lee nadie más que el ojo del bookkeeper).
# ─────────────────────────────────────────────────────────────────────────────────────────────

class LabelIn(BaseModel):
    name: str
    color: str = "#f4b400"


@app.get("/api/companies/{company_id}/labels")
def list_labels(company_id: int, user: User = Depends(current_user),
                session: Session = Depends(get_session)):
    """Catálogo de etiquetas que el bookkeeper definió para esta empresa."""
    owned_company(company_id, user, session)
    return session.exec(
        select(SheetLabel).where(SheetLabel.company_id == company_id).order_by(SheetLabel.id)
    ).all()


@app.post("/api/companies/{company_id}/labels")
def create_label(company_id: int, payload: LabelIn, user: User = Depends(current_user),
                 session: Session = Depends(get_session)):
    owned_company(company_id, user, session)
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="la etiqueta necesita un nombre")
    existing = session.exec(
        select(SheetLabel).where(SheetLabel.company_id == company_id, SheetLabel.name == name)
    ).first()
    if existing:
        return existing
    label = SheetLabel(company_id=company_id, name=name, color=payload.color)
    session.add(label); session.commit(); session.refresh(label)
    return label


@app.delete("/api/labels/{label_id}")
def delete_label(label_id: int, user: User = Depends(current_user),
                 session: Session = Depends(get_session)):
    label = session.get(SheetLabel, label_id)
    if not label:
        raise HTTPException(status_code=404, detail="no existe")
    owned_company(label.company_id, user, session)
    # Se quitan primero las aplicaciones: borrar una etiqueta no debe dejar filas colgadas.
    session.exec(delete(TransactionLabel).where(TransactionLabel.label_id == label_id))
    session.delete(label); session.commit()
    return {"ok": True}


class TxLabelsIn(BaseModel):
    """Estado COMPLETO de etiquetas de la transacción (reemplaza, no suma)."""
    label_ids: list[int] = []


@app.put("/api/transactions/{transaction_id}/labels")
def set_transaction_labels(transaction_id: int, payload: TxLabelsIn,
                           user: User = Depends(current_user),
                           session: Session = Depends(get_session)):
    """Fija las etiquetas de una transacción. Es un PUT (estado completo) para que marcar y
    desmarcar sean la misma operación y no haya que llevar la cuenta en el frontend."""
    tx = owned_transaction(transaction_id, user, session, write=True)
    valid = {l.id for l in session.exec(
        select(SheetLabel).where(SheetLabel.company_id == tx.company_id)).all()}
    bad = set(payload.label_ids) - valid
    if bad:
        raise HTTPException(status_code=422, detail=f"etiquetas ajenas a esta empresa: {sorted(bad)}")
    session.exec(delete(TransactionLabel).where(TransactionLabel.transaction_id == transaction_id))
    for lid in dict.fromkeys(payload.label_ids):
        session.add(TransactionLabel(transaction_id=transaction_id, label_id=lid))
    session.commit()
    return {"transaction_id": transaction_id, "label_ids": payload.label_ids}


class CellFormatIn(BaseModel):
    """Formato a aplicar. `transaction_ids` permite pintar un RANGO de una sola vez.
    Un campo en `None` no se toca; para limpiar se manda `clear: true`."""
    transaction_ids: list[int]
    column: Optional[str] = None      # None = la fila entera
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    bg: Optional[str] = None
    fg: Optional[str] = None
    font_size: Optional[int] = None
    font_family: Optional[str] = None
    clear: bool = False


@app.post("/api/companies/{company_id}/cell-formats")
def apply_cell_format(company_id: int, payload: CellFormatIn,
                      user: User = Depends(current_user),
                      session: Session = Depends(get_session)):
    """Aplica (o limpia) formato sobre una o muchas celdas. UPSERT por (transacción, columna)."""
    owned_company(company_id, user, session)
    owned = {t.id for t in session.exec(
        select(Transaction).where(Transaction.company_id == company_id,
                                  Transaction.id.in_(payload.transaction_ids))).all()}
    bad = set(payload.transaction_ids) - owned
    if bad:
        raise HTTPException(status_code=403, detail=f"transacciones ajenas: {sorted(bad)}")

    changed = 0
    for tid in owned:
        row = session.exec(select(CellFormat).where(
            CellFormat.transaction_id == tid, CellFormat.col == payload.column)).first()
        if payload.clear:
            if row:
                session.delete(row); changed += 1
            continue
        if row is None:
            row = CellFormat(transaction_id=tid, col=payload.column)
        for f in ("bold", "italic", "bg", "fg", "font_size", "font_family"):
            v = getattr(payload, f)
            if v is not None:
                setattr(row, f, v)
        row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        session.add(row); changed += 1
    session.commit()
    return {"changed": changed}


@app.get("/api/companies/{company_id}/audit")
def audit_company(
    company_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """AGENTE AUDITOR: escanea las transacciones categorizadas y devuelve los **sospechosos**
    (desvío de regla, inconsistencia por comercio, contradicción de signo, sin categorizar).
    Determinista, sin IA. Corre a demanda Y automáticamente tras cada subida (badge)."""
    owned_company(company_id, user, session)
    return _company_audit(session, company_id)


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
    proposals = propose_rules(candidates, [r.match_text for r in rules], sector,
                              format_profile_facts(company.profile if company else None))
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
    # AUDITOR AUTOMÁTICO (FASE 9 Paso 4): tras cambiar el reglamento, el agente verifica los libros.
    return {"created": created, "applied": applied,
            "audit_findings": len(_company_audit(session, company_id))}


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
        if not any(match_rule([r], matchable_text(t.description, t.description_extra)) is not None for t in txs):
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
    with_audit: bool = False,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Lista las transacciones de una empresa (más recientes primero).

    `with_audit=true` adjunta a cada fila su **observación** del agente auditor (la 6ª columna de la
    hoja, FASE 10). Se calcula en memoria sobre los datos vivos — determinista y sin IA, así que no
    cuesta nada; por eso va en la misma llamada en vez de obligar al frontend a cruzar dos listas.
    """
    owned_company(company_id, user, session)
    txs = session.exec(
        select(Transaction)
        .where(Transaction.company_id == company_id)
        .order_by(Transaction.date.desc())
    ).all()
    if not with_audit:
        return txs
    obs = _audit_by_transaction(session, company_id)

    # Etiquetas aplicadas (dato) y formato de celda (presentación) — en la MISMA llamada para que
    # la hoja pinte de una sola vez y no encadene tres peticiones al abrir.
    labels = {l.id: l for l in session.exec(
        select(SheetLabel).where(SheetLabel.company_id == company_id)).all()}
    by_tx: dict[int, list[dict]] = {}
    for tl in session.exec(
        select(TransactionLabel).join(
            Transaction, Transaction.id == TransactionLabel.transaction_id
        ).where(Transaction.company_id == company_id)
    ).all():
        lab = labels.get(tl.label_id)
        if lab:
            by_tx.setdefault(tl.transaction_id, []).append(
                {"id": lab.id, "name": lab.name, "color": lab.color})

    fmts: dict[int, dict[str, dict]] = {}
    for cf in session.exec(
        select(CellFormat).join(
            Transaction, Transaction.id == CellFormat.transaction_id
        ).where(Transaction.company_id == company_id)
    ).all():
        fmts.setdefault(cf.transaction_id, {})[cf.col or "_row"] = {
            "bold": cf.bold, "italic": cf.italic, "bg": cf.bg, "fg": cf.fg,
            "font_size": cf.font_size, "font_family": cf.font_family,
        }

    return [{
        **t.model_dump(),
        "observation": obs.get(t.id),
        "labels": by_tx.get(t.id, []),
        "formats": fmts.get(t.id, {}),
    } for t in txs]


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


@app.get("/api/companies/{company_id}/pivot")
def get_pivot(
    company_id: int,
    start: Optional[date] = None,
    end: Optional[date] = None,
    account: Optional[str] = None,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """EL PIVOTE (FASE 10 paso 7): categoría → subcategoría → mes. Totales agregados en SQL."""
    owned_company(company_id, user, session)
    return pivot(session, company_id, start, end, account)


@app.get("/api/companies/{company_id}/pivot/leaves")
def get_pivot_leaves(
    company_id: int,
    section: str,
    category: str,
    subcategory: str,
    month: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """El último `+` del pivote: las transacciones de ese mes, con día y descripción.
    Se piden aparte para no cargar el árbol entero al abrir."""
    owned_company(company_id, user, session)
    return pivot_leaves(session, company_id, section, category, subcategory, month)


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

    # VOLVER A SEGUIR LA REGLA (FASE 10) — el inverso de escribir a mano.
    #
    # Modelo mental de hoja de cálculo (idea de Jose): una celda puede tener una "fórmula" (la
    # decidió una regla → `matched_rule_id`) o un valor escrito "a mano" (`source="manual"`, sin
    # regla). Editar la celda rompe la fórmula; esto la restaura: busca la regla que matchearía
    # esta descripción y la vuelve a aplicar.
    if rule_action == "follow_rule":
        company = session.get(Company, tx.company_id)
        rules = load_rules(session, tx.company_id, company.sector if company else "")
        rule = match_rule(rules, matchable_text(tx.description, tx.description_extra))
        if rule is None:
            raise HTTPException(
                status_code=404,
                detail="Ninguna regla cubre esta transacción. Crea una en la hoja de reglas.",
            )
        tx.account_type = rule.account_type
        tx.category = rule.category
        tx.subcategory = (rule.subcategory or rule.match_text).upper()
        tx.source = "rule"
        tx.matched_rule_id = rule.id
        tx.confidence = 1.0
        session.add(tx); session.commit(); session.refresh(tx)
        return {"id": tx.id, "applied": 0, "rule_updated": False,
                "followed_rule": {"id": rule.id, "match_text": rule.match_text,
                                  "category": rule.category}}

    # EL CANDADO DEL PLAN DE CUENTAS (FASE 10). `categories.py` mantiene un plan CERRADO por sector
    # para que la IA no invente cuentas — pero hasta ahora ese candado solo existía del lado de la IA:
    # este endpoint guardaba cualquier string. Con la hoja editable (paso 6) eso se vuelve un agujero
    # real: el P&L agrupa por el TEXTO de la categoría (`reports.py`), así que "Materials" y
    # "Materiales" se parten en dos renglones, y el auditor reporta inconsistencias fantasma.
    if data.get("category"):
        company = session.get(Company, tx.company_id)
        chart = categories_for_company(session, tx.company_id, company.sector if company else "")
        valid = {c for cats in chart.values() for c in cats}
        if data["category"] not in valid:
            raise HTTPException(
                status_code=422,
                detail=(f"'{data['category']}' no está en el plan de cuentas de esta empresa. "
                        "Agrégala en Ajustes si de verdad hace falta."),
            )
        # La categoría PERTENECE a una sección: si llega sin `account_type`, se deduce del plan.
        # Así una celda editada nunca queda con la cuenta en una sección y el monto sumando en otra.
        if not data.get("account_type"):
            for section, cats in chart.items():
                if data["category"] in cats:
                    data["account_type"] = AccountType(section)
                    break
    if data.get("subcategory"):
        data["subcategory"] = data["subcategory"].upper()  # subcategorías siempre en MAYÚSCULA
    for field, value in data.items():
        setattr(tx, field, value)
    # Si el usuario tocó la categorización, la intención sobre el REGLAMENTO decide (FASE 9 Paso 2):
    # "only_this" = excepción (no toca reglas) · "update_rule" = edita la regla que la cubría ·
    # default = aprende/actualiza regla por frase específica (comportamiento clásico).
    applied = 0  # cuántas OTRAS transacciones alineó la regla (para feedback "en vivo")
    rule_updated = False
    rule_skipped_generic = False  # el guardián vetó la frase automática (era genérica)
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
            elif tx.account_type is not None and tx.category:
                # El GUARDIÁN vetó la frase (genérica: cazaría otros comercios) → la corrección
                # queda como excepción puntual y el frontend se lo explica al dueño.
                rule_skipped_generic = True
    session.add(tx)
    session.commit()
    session.refresh(tx)
    # Devuelve la tx + cuántas OTRAS alineó la regla → el frontend lo muestra y re-pinta de verde.
    return {"id": tx.id, "applied": applied, "rule_updated": rule_updated,
            "rule_skipped_generic": rule_skipped_generic}


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

    cat = recategorize_with_answer(tx.description, signed, sector, payload.answer,
                                   format_profile_facts(company.profile if company else None))

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

    cat = recategorize_with_answer(rep.description, signed, sector, payload.answer,
                                   format_profile_facts(company.profile if company else None))
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
