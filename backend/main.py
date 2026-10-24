"""
ContaBu — backend (FastAPI).
Endpoints de la API: prueba (/api/hello), identidad (/api/me) e ingestión de archivos bancarios.

Multi-tenant (deuda #2 🔴): la identidad sale SIEMPRE del usuario autenticado (token Firebase,
ver `auth.py`); los endpoints validan que la empresa/transacción pertenezca al usuario antes de
tocarla. El `company_id` de la URL ya NO se confía a ciegas.
"""

import json
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal
import os
import re
from typing import Optional

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, delete, select

from ai import RuleColumnMapping, map_rule_columns
from audit import audit_categorization
from billing import apply_session, create_checkout_session, handle_event
# `effective_role` es la única fuente de verdad del acceso (FASE 13); los helpers dicen la
# INTENCIÓN de cada endpoint: leer · escribir como consola · escribir como cliente · ser dueño.
from auth import (current_identity, current_user, effective_role, owned_company, owner_company, owned_transaction,
                  client_writable_company, console_company, user_firm)
from categories import (help_for, LINES, LOCKED, TAX_FORMS, categories_for_company, chart_rows, line_form, seed_chart,
                        suggested_line, switch_plan)
from company_profile import format_profile_facts
from corrections import (VIA_FOLLOW_RULE, VIA_MANUAL, VIA_RULE_DELETED, VIA_RULE_PRECEDENCE, VIA_RULE_UPDATE,
                         VIA_CHART_MOVE, VIA_CHART_RENAME, last_change, record_change,
                         snapshot, undo_change)
from db import get_session
import demo  # FASE 13 — despliegue de demo (no hace nada si DEMO_MODE está apagado)
import book_notes  # FASE 11 — las notas del libro (Auditoría / IA)
from ingestion import ingest_csv, mask_account, parse_spreadsheet
from models import (
    AccountType, CategoryRule, Company, CompanyCategory, CompanyMember, PnlReport, Role, RuleScope,
    Transaction, User, ProposalDismissal,
    Firm, FirmMember, FirmRole,
    SheetLabel,
    TransactionLabel,
    CellFormat,
    CategorizationChange,
    BankConnection,)
from reports import pivot, pivot_leaves, income_statement
import bank
from rules import (
    apply_rule_to_transactions, build_import_rules, create_company_rule, find_rule_candidates,
    is_check, is_deposit, load_rules, match_rule, matchable_text, upsert_company_rule, validate_keyword,
    place_priority, reapply_rule_precedence, redecide_transaction, rule_overlaps, simulate_delete,
    simulate_rule_change, rule_trace,
    suggest_keyword, normalize,
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
    # Por qué se corrigió (opcional). No es campo de la transacción: va al historial de correcciones.
    reason: Optional[str] = None


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


class CompanyProfile(BaseModel):
    """Perfil de la empresa que se edita en el onboarding / ajustes (nombre y sector)."""
    name: Optional[str] = None
    sector: Optional[str] = None
    # Sector "other": a qué se dedica el negocio, en texto libre. Antes viajaba por la entrevista
    # (`PATCH /profile`, eliminado el 2-oct); es un dato del ALTA y la IA lo recibe como hecho
    # (`format_profile_facts` → "The owner describes the business as …").
    trade: Optional[str] = None
    # CÓMO TRIBUTA (Movimientos, 18-oct): "schedule_c" | "1120s". Se elige con el sector y se cambia en
    # Ajustes; decide el plan de cuentas por defecto. En el demo no se pregunta (todo es Schedule C).
    tax_form: Optional[str] = None


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
    """Una categoría que se agrega AL VUELO al plan del libro (desde la celda o el diálogo de una regla)."""
    account_type: AccountType  # sección del P&L
    name: str                  # nombre de la cuenta (en inglés)


class RuleUpdate(BaseModel):
    """Edición de una regla de empresa desde la tabla de reglas (comercio → categoría)."""
    match_text: Optional[str] = None   # la palabra clave (14-oct): la hoja muestra antes la vista previa
    account_type: Optional[AccountType] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None  # comercio presentable que asigna la regla ("" → limpiar)
    priority: Optional[int] = None     # prioridad manual (1 = primero); null → orden automático
    style: Optional[dict] = None       # resaltado de la fila en la hoja de Reglas: {bg, bold, italic}
    valid_from: Optional[date] = None  # ventana de fechas, INCLUSIVA (null = sin límite por ese lado)
    valid_to: Optional[date] = None
    amount_min: Optional[Decimal] = None  # monto, INCLUSIVO y sobre la magnitud (null = sin límite)
    amount_max: Optional[Decimal] = None


class RuleCreate(BaseModel):
    """Regla creada A MANO por el dueño desde la tabla de reglas (hallazgo 8.10: el reglamento
    también se escribe directo, no solo vía correcciones/propuestas)."""
    match_text: str
    account_type: AccountType
    category: str
    subcategory: Optional[str] = None
    priority: Optional[int] = None
    valid_from: Optional[date] = None  # ventana de fechas (4-oct, "la fórmula del libro")
    valid_to: Optional[date] = None
    amount_min: Optional[Decimal] = None  # monto (8-oct), INCLUSIVO y sobre la magnitud
    amount_max: Optional[Decimal] = None


def _valid_window(valid_from: Optional[date], valid_to: Optional[date]) -> None:
    """Una ventana de fechas al revés no caza nada: se rechaza en vez de guardarla."""
    if valid_from is not None and valid_to is not None and valid_from > valid_to:
        raise HTTPException(status_code=422, detail="la fecha de inicio va después de la de fin")


def _valid_amount(amount_min: Optional[Decimal], amount_max: Optional[Decimal]) -> None:
    """El monto es una magnitud (≥ 0) y el rango no puede ir al revés."""
    if any(a is not None and a < 0 for a in (amount_min, amount_max)):
        raise HTTPException(status_code=422, detail="el monto de una regla va sin signo (≥ 0)")
    if amount_min is not None and amount_max is not None and amount_min > amount_max:
        raise HTTPException(status_code=422, detail="el monto mínimo va después del máximo")


def _money(v) -> Optional[Decimal]:
    return None if v is None else Decimal(str(v)).quantize(Decimal("0.01"))


def _same_rule(r: CategoryRule, match_text: str, valid_from: Optional[date], valid_to: Optional[date],
               amount_min=None, amount_max=None) -> bool:
    """Dos reglas son la MISMA si tienen la misma palabra clave y las mismas condiciones (fechas y
    monto): así conviven "STARBUCKS", "STARBUCKS del 3 al 17-ago" y "SAFEWAY más de $50"."""
    return (normalize(r.match_text) == normalize(match_text)
            and (r.valid_from, r.valid_to) == (valid_from, valid_to)
            and (_money(r.amount_min), _money(r.amount_max)) == (_money(amount_min), _money(amount_max)))


def _rule_out(r: CategoryRule) -> dict:
    return {"valid_from": r.valid_from.isoformat() if r.valid_from else None,
            "valid_to": r.valid_to.isoformat() if r.valid_to else None,
            "amount_min": float(r.amount_min) if r.amount_min is not None else None,
            "amount_max": float(r.amount_max) if r.amount_max is not None else None}


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
        # Orígenes extra por variable de entorno, separados por coma (FASE 13, 3-oct): el servicio de demo
        # pone el de SU frontend (p. ej. https://demo.contabu.com) sin tocar el código — el subdominio
        # todavía no está decidido (docs/DEMO Y ENTREGA.md).
        *[o.strip() for o in os.getenv("EXTRA_CORS_ORIGINS", "").split(",") if o.strip()],
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/hello")
def hello():
    """Endpoint de prueba: responde un JSON. El frontend lo pedirá y lo mostrará."""
    return {"message": "¡Hola desde el backend de ContaBu! 🚀"}


def _create_own_firm(user: User, session: Session) -> tuple[Firm, FirmRole]:
    """Oficina de UN usuario (FASE 13 Paso 4). Jose: quien se registra *"queda como un bookkeeper así
    sea un solo usuario, con todas las herramientas"*. El rol CLIENT nunca nace aquí: solo por invitación."""
    firm = Firm(name=(user.email or "").strip() or "My Firm", owner_user_id=user.id)
    demo.mark_if_demo(firm)  # solo en el demo: fecha de borrado (la limpieza solo toca estas)
    session.add(firm)
    session.flush()
    session.add(FirmMember(firm_id=firm.id, user_id=user.id, email=user.email or "", role=FirmRole.OWNER))
    return firm, FirmRole.OWNER


@app.get("/api/me")
def get_me(
    user: User = Depends(current_user),
    identity: dict = Depends(current_identity),
    session: Session = Depends(get_session),
):
    """El usuario, su OFICINA y los libros que ve. Respuesta: `{user, firm, firm_role, companies, roles}`.

    - Ve los libros de su oficina (todos), los que creó y los que le compartieron por invitación.
    - **Cuenta nueva sin nada** (ni oficina, ni libros, ni invitación) → nace una **oficina de un usuario**
      con su primer libro (FASE 13 Paso 4). Antes se creaba solo una empresa suelta, sin oficina.
    - **Un cliente** (invitado como CLIENT, sin oficina) → `firm = null`. El frontend usa eso para elegir
      cáscara: consola del bookkeeper o plataforma del cliente.
    Las invitaciones pendientes ya se enlazaron en `current_user`, ANTES de llegar aquí: por eso un
    invitado nunca recibe una oficina que no pidió.
    - **En el DEMO** (23-oct) no se crea nada aquí: `demo.door` dice la puerta (`demo.door` en la respuesta)
      y, solo a quien tiene código, le deja su oficina. Sin código se ven los libros de la visita guiada.
    """
    door = demo.door(session, user, anonymous=bool(identity.get("anonymous"))) if demo.DEMO_MODE else None
    firm, firm_role = user_firm(user, session)

    ids: set[int] = set()
    if firm is not None:
        ids |= set(session.exec(select(Company.id).where(Company.firm_id == firm.id)).all())
    ids |= set(session.exec(select(Company.id).where(Company.user_id == user.id)).all())
    ids |= set(session.exec(select(CompanyMember.company_id).where(CompanyMember.user_id == user.id)).all())

    if not ids and not demo.DEMO_MODE and (firm is None or firm_role == FirmRole.OWNER):
        if firm is None:
            firm, firm_role = _create_own_firm(user, session)
        # sector vacío = AÚN NO ELEGIDO → el frontend muestra el onboarding (selector de sector).
        books = [Company(name="My Business", sector="", user_id=user.id, firm_id=firm.id)]
        session.add(books[0])
        session.commit()
        ids = {b.id for b in books}

    companies = session.exec(select(Company).where(Company.id.in_(ids)).order_by(Company.id)).all() if ids else []
    roles: dict[int, str] = {}
    for c in companies:
        r = effective_role(c, user, session)
        if r is not None:
            roles[c.id] = r.value
    out = {
        "user": user,
        "firm": _firm_out(firm, firm_role) if firm is not None else None,
        "firm_role": firm_role.value if firm_role is not None else None,
        "companies": [c for c in companies if c.id in roles],
        "roles": roles,
    }
    if door is not None:
        out["demo"] = {"door": door}
    return out


# ==================================================================================================
# FASE 13 — LAS DOS PUERTAS DEL DEMO (área `Firm / bookkeepers`, 23-oct). Solo con DEMO_MODE: fuera del
# demo responden 404. La lógica vive en `demo.py`; aquí solo HTTP.
# ==================================================================================================

class DemoCodeIn(BaseModel):
    code: str


@app.post("/api/demo/access")
def demo_redeem_code(payload: DemoCodeIn, request: Request, user: User = Depends(current_user),
                     identity: dict = Depends(current_identity), session: Session = Depends(get_session)):
    """Canjea el CÓDIGO DE ACCESO del demo en vivo (lo pide la app después de "Entrar con Google"). Lo
    verifica el backend en tiempo constante; sin un código válido no se crea oficina ni se gasta IA. 403
    `demo_code_invalid` · 429 `demo_code_locked` (demasiados intentos) · 403 `demo_google_required`."""
    if not demo.DEMO_MODE:
        raise HTTPException(status_code=404, detail="not found")
    ip = demo.client_ip(request.headers, request.client.host if request.client else None)
    try:
        name = demo.redeem_code(session, user, payload.code, ip=ip, anonymous=bool(identity.get("anonymous")))
    except demo.CodeRejected as e:
        raise HTTPException(status_code=429 if e.code == "demo_code_locked" else 403, detail={"code": e.code})
    return {"ok": True, "code_name": name}


@app.get("/api/demo/intro")
def demo_intro(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """La pantalla de inicio del recorrido guiado: los agentes con su modelo y el antes/después de Rivera HVAC
    de la visita, contado por SQL (`demo.intro`). La visita es pública: cualquiera con sesión la lee."""
    if not demo.DEMO_MODE:
        raise HTTPException(status_code=404, detail="not found")
    return demo.intro(session)


@app.get("/api/companies/{company_id}/demo/proposals")
def demo_tour_proposals(company_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Las reglas que el ④ propuso al crear la visita (guardadas; en solo lectura). Vacío en cualquier otro libro."""
    if not demo.DEMO_MODE:
        raise HTTPException(status_code=404, detail="not found")
    return demo.tour_proposals(session, console_company(company_id, user, session))


@app.get("/api/companies/{company_id}/demo/guide")
def demo_guide_pick(company_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """El hallazgo que muestra la parada 5 del recorrido (`demo.guide_pick`)."""
    if not demo.DEMO_MODE:
        raise HTTPException(status_code=404, detail="not found")
    return demo.guide_pick(session, console_company(company_id, user, session))


@app.get("/api/companies/{company_id}/findings/judges")
def finding_judges(company_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Quién juzgó de verdad cada hallazgo y con qué modelo, sacado de `agentcall` (`demo.judges`): la
    etiqueta "Judged by Nemotron 3 Nano" del detalle. Lectura de consola (como el tablero de Auditoría)."""
    return {str(k): v for k, v in demo.judges(session, console_company(company_id, user, session)).items()}


@app.post("/api/demo/pool/refill")
def demo_pool_refill(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Rellena las oficinas en espera (`demo.refill_pool`). La pide el frontend SIN esperarla —al abrir la
    pantalla del código y al canjearlo— porque en Cloud Run el trabajo solo avanza dentro de una petición.
    Pasa cualquiera con sesión: el trabajo tiene techo (`DEMO_POOL_SIZE`) y nadie se lleva una oficina sin
    código. Para cebarla a mano: `demo.py pool`."""
    if not demo.DEMO_MODE:
        raise HTTPException(status_code=404, detail="not found")
    return demo.refill_pool(session)


# ==================================================================================================
# FASE 13 — AJUSTES DE LA OFICINA (área `Firm / bookkeepers`). Lo que vale para TODOS los libros.
# Los ajustes de UN libro siguen en `/api/companies/{id}`.
# ==================================================================================================

class FirmUpdate(BaseModel):
    name: Optional[str] = None
    # El SELLO que ven los clientes de esta oficina (y el encabezado del PDF de la FASE 12).
    # "" → se borra y se cae al nombre de la oficina.
    brand_name: Optional[str] = None
    brand_color: Optional[str] = None


def _firm_out(firm, role) -> dict:
    return {"id": firm.id, "name": firm.name, "brand_name": firm.brand_name,
            "brand_color": firm.brand_color, "brand_logo_url": firm.brand_logo_url,
            "role": role.value if role else None}


@app.get("/api/firm")
def get_firm(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """La oficina del usuario y su rol en ella. 404 si no pertenece a ninguna (un cliente)."""
    firm, role = user_firm(user, session)
    if firm is None:
        raise HTTPException(status_code=404, detail="no firm")
    return _firm_out(firm, role)


@app.patch("/api/firm")
def update_firm(payload: FirmUpdate, user: User = Depends(current_user),
                session: Session = Depends(get_session)):
    """Edita nombre y marca de la oficina. Solo el ADMINISTRADOR (FirmRole.OWNER)."""
    firm, role = user_firm(user, session)
    if firm is None:
        raise HTTPException(status_code=404, detail="no firm")
    if role is None or role.value != "OWNER":
        raise HTTPException(status_code=403, detail="firm owner only")
    if payload.name is not None:
        if not payload.name.strip():
            raise HTTPException(status_code=422, detail="name required")
        firm.name = payload.name.strip()
    if payload.brand_name is not None:
        firm.brand_name = payload.brand_name.strip() or None
    if payload.brand_color is not None:
        color = payload.brand_color.strip()
        if color and not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            raise HTTPException(status_code=422, detail="brand_color must be #RRGGBB")
        firm.brand_color = color or None
    session.add(firm)
    session.commit()
    session.refresh(firm)
    return _firm_out(firm, role)


# ==================================================================================================
# FASE 13 — DÍAS DE ESPERA DEL LIBRO (área `Firm / bookkeepers`).
# 🔄 1-oct, decisión de Jose: fuera los ajustes por monto (las tarjetas de supermercado, gasolinera y
# propias). Lo que el motor no decida con una REGLA queda Pendiente, y el criterio del bookkeeper vive
# en las NOTAS de la pestaña Auditoría (F11). Los días de espera NO son un monto y se quedan, como un
# ajuste simple: `Company.settlement_buffer_days` (el motor lo lee en `ingestion.ingest_csv`).
# ==================================================================================================

class SettlementIn(BaseModel):
    wait_days: int


@app.patch("/api/companies/{company_id}/settlement")
def update_settlement(company_id: int, payload: SettlementIn, user: User = Depends(current_user),
                      session: Session = Depends(get_session)):
    """Cuántos días esperar antes de registrar lo reciente (por si el banco aún lo está procesando)."""
    company = owned_company(company_id, user, session, write=True)
    if not 0 <= payload.wait_days <= 30:
        raise HTTPException(status_code=422, detail="wait_days_range")
    company.settlement_buffer_days = payload.wait_days
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


@app.post("/api/companies")
def create_company(
    payload: CompanyCreate,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Crea un libro NUEVO dentro de la OFICINA del usuario (antes nacía suelto, sin `firm_id`, y los
    demás bookkeepers de la oficina no lo veían). Sin oficina → nace la suya (FASE 13 Paso 4). Un
    VIEWER de la oficina no crea libros. Se crea con `sector=""` → el frontend lo lleva al onboarding."""
    if demo.DEMO_MODE and not demo.access_of(session, user):
        # DEMO (23-oct): sin código de acceso no nace ninguna oficina, tampoco por aquí.
        raise HTTPException(status_code=403, detail={"code": "demo_access_required"})
    firm, firm_role = user_firm(user, session)
    if firm is None:
        firm, firm_role = _create_own_firm(user, session)
    if firm_role == FirmRole.VIEWER:
        raise HTTPException(status_code=403, detail="read-only (viewer)")
    name = (payload.name or "").strip() or "New Business"
    company = Company(name=name, sector="", user_id=user.id, firm_id=firm.id)
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
    """Sube un CSV bancario y dispara el pipeline: la IA SOLO mapea las columnas → parse → regla →
    transferencia → Pendiente → guarda. La IA no categoriza (deuda #16, 4-oct).

    `limit=1000` es solo un **tope de seguridad** por subida; el frontend sube por lotes (`chunk`).
    """
    # LISTA BLANCA DEL CLIENTE (FASE 13 Paso 3): subir el extracto es lo que el cliente APORTA,
    # no una edición del libro → `client_writable_company` en vez de la puerta de la consola.
    company = client_writable_company(company_id, user, session)
    content = await file.read()
    # chunk>0 → subida por LOTES (cada lote bajo el límite del navegador); si no, tope de seguridad.
    limit = chunk if chunk > 0 else 1000
    result = ingest_csv(session, company_id, company.sector, content,
                        limit=limit, offset=offset, filename=file.filename)
    # AUDITOR AUTOMÁTICO (FASE 9 Paso 4): al terminar la subida (último lote), el agente revisa los
    # libros solo — sin que el dueño lo pida — y el frontend muestra el badge con los hallazgos.
    # Solo en el último lote (correr por lote sería redundante); determinista y gratis (sin IA).
    is_last_chunk = result.get("offset", 0) + result.get("processed", 0) >= result.get("total", 0)
    if is_last_chunk:
        result["audit_findings"] = len(_company_audit(session, company_id))
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



# ════════════════════════════════════════════════════════════════════════════════════════════════
# BANCOS VINCULADOS (Plaid) — área Movimientos, 3-oct-2026. Lógica en `bank.py`.
# El access_token nace en el servidor (canje del public_token), se guarda cifrado y NUNCA viaja
# al navegador. Al navegador solo llegan link_tokens de un solo uso.
# ════════════════════════════════════════════════════════════════════════════════════════════════

class BankLinkTokenRequest(BaseModel):
    """Para abrir Plaid Link. Con `connection_id` se abre en modo actualización (reparar)."""
    connection_id: Optional[int] = None
    language: Optional[str] = None  # "es" | "en" — el idioma de Link


class BankLinkMetadata(BaseModel):
    """Lo que Plaid Link devuelve en `metadata` y sirve para reconocer un banco ya vinculado (14-oct)."""
    institution_id: Optional[str] = None
    institution_name: Optional[str] = None
    account_masks: list[str] = []


class BankConnectionCreate(BankLinkMetadata):
    """Lo que devuelve Plaid Link en `onSuccess`: el public_token (de un solo uso) y el banco."""
    public_token: str


def _bank_duplicate(session: Session, company: Company, meta: BankLinkMetadata) -> Optional[dict]:
    """El banco ya vinculado (en este libro u otro de la oficina), o None. Ver `bank.find_duplicate`."""
    dup = bank.find_duplicate(session, company, meta.institution_id, meta.institution_name, meta.account_masks)
    if dup is None:
        return None
    book = session.get(Company, dup.company_id)
    return {"connection_id": dup.id, "company_id": dup.company_id, "company_name": book.name if book else None,
            "institution_name": dup.institution_name, "same_book": dup.company_id == company.id}


def _bank_connection_out(c: BankConnection) -> dict:
    """La conexión SIN el token (ni cifrado) y sin el cursor."""
    return {"id": c.id, "environment": c.environment, "institution_name": c.institution_name,
            "accounts": c.accounts or [], "status": c.status, "last_error": c.last_error,
            "last_synced_at": c.last_synced_at.isoformat() if c.last_synced_at else None}


def _owned_bank_connection(company_id: int, connection_id: int, session: Session) -> BankConnection:
    conn = session.get(BankConnection, connection_id)
    if conn is None or conn.company_id != company_id:
        raise HTTPException(status_code=404, detail="Conexión no encontrada.")
    return conn


def _bank_http_error(e: Exception) -> HTTPException:
    """Errores con código estable para que la pantalla los traduzca (EN/ES)."""
    if isinstance(e, bank.BankConfigError):
        return HTTPException(status_code=503, detail={"code": "bank_not_configured", "reason": e.code})
    if e.disconnected:
        return HTTPException(status_code=409, detail={"code": "bank_disconnected", "reason": e.code})
    return HTTPException(status_code=502, detail={"code": "bank_error", "reason": e.code})


def _bank_sync(session: Session, conn: BankConnection, company: Company) -> dict:
    """Sincroniza y deja el estado de la conexión al día."""
    try:
        summary = bank.sync(session, conn, company)
    except bank.BankConfigError as e:
        raise _bank_http_error(e)
    except bank.BankError as e:
        session.rollback()
        conn = session.get(BankConnection, conn.id)
        conn.last_error = e.code
        if e.disconnected:
            conn.status = "disconnected"
        session.add(conn)
        session.commit()
        raise _bank_http_error(e)
    conn.status = "active"
    conn.last_error = None
    conn.last_synced_at = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add(conn)
    session.commit()
    session.refresh(conn)
    return {"connection": _bank_connection_out(conn), "sync": summary,
            "audit_findings": len(_company_audit(session, company.id))}


@app.get("/api/companies/{company_id}/bank-connections")
def list_bank_connections(
    company_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Si la vinculación está configurada (sin secretos) y los bancos vinculados al libro."""
    console_company(company_id, user, session)
    rows = session.exec(select(BankConnection).where(BankConnection.company_id == company_id)
                        .order_by(BankConnection.id)).all()
    return {"config": bank.public_config(), "connections": [_bank_connection_out(c) for c in rows]}


@app.post("/api/companies/{company_id}/bank-connections/link-token")
def create_bank_link_token(
    company_id: int,
    payload: BankLinkTokenRequest,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Un link_token de un solo uso para abrir Plaid Link (nuevo banco, o reparar uno cortado)."""
    owned_company(company_id, user, session, write=True)
    access_token = None
    if payload.connection_id is not None:
        conn = _owned_bank_connection(company_id, payload.connection_id, session)
        try:
            access_token = bank.decrypt_token(conn.access_token_enc)
        except bank.BankConfigError as e:
            raise _bank_http_error(e)
    try:
        return {"link_token": bank.create_link_token(user.id, payload.language or "en", access_token)}
    except (bank.BankConfigError, bank.BankError) as e:
        raise _bank_http_error(e)


@app.post("/api/companies/{company_id}/bank-connections/check")
def check_bank_connection(
    company_id: int,
    payload: BankLinkMetadata,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """ANTES de canjear el public_token (14-oct): ¿este banco ya está vinculado, aquí o en otro libro de la
    oficina? La pantalla lo pregunta en el `onSuccess` de Link y, si lo está, NO canjea. No toca nada."""
    company = owned_company(company_id, user, session, write=True)
    return {"duplicate": _bank_duplicate(session, company, payload)}


@app.post("/api/companies/{company_id}/bank-connections")
def create_bank_connection(
    company_id: int,
    payload: BankConnectionCreate,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Canjea el public_token que devolvió Plaid Link, guarda la conexión (token cifrado) y hace la
    primera sincronización. Si ese Item ya estaba en el libro, actualiza su token en vez de duplicarlo."""
    company = owned_company(company_id, user, session, write=True)
    public_token = (payload.public_token or "").strip()
    if not public_token:
        raise HTTPException(status_code=422, detail="public_token es obligatorio")
    # SEGUNDA BARRERA (14-oct): la pantalla ya lo preguntó, pero si el banco está vinculado, el token NO se
    # canjea. Canjear crea la conexión en Plaid, y en el plan Trial cada conexión cuenta y no se recupera.
    dup = _bank_duplicate(session, company, payload)
    if dup is not None:
        raise HTTPException(status_code=409, detail={"code": "bank_duplicate", "reason": dup["company_name"],
                                                     "duplicate": dup})
    try:
        access_token, item_id = bank.exchange_public_token(public_token)
        enc = bank.encrypt_token(access_token)
    except (bank.BankConfigError, bank.BankError) as e:
        raise _bank_http_error(e)
    conn = session.exec(select(BankConnection).where(BankConnection.company_id == company_id)
                        .where(BankConnection.item_id == item_id)).first()
    if conn is None:
        conn = BankConnection(company_id=company_id, environment=bank.environment(),
                              item_id=item_id, access_token_enc=enc, created_by_user_id=user.id)
    conn.access_token_enc = enc
    conn.institution_name = (payload.institution_name or "").strip() or conn.institution_name
    conn.institution_id = (payload.institution_id or "").strip() or conn.institution_id
    session.add(conn)
    session.commit()
    session.refresh(conn)
    return _bank_sync(session, conn, company)


@app.post("/api/companies/{company_id}/bank-connections/{connection_id}/sync")
def sync_bank_connection(
    company_id: int,
    connection_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """"Actualizar": trae lo nuevo desde el cursor. Lo que ya estaba no se duplica. También se usa
    después de reparar una conexión en Plaid Link (modo actualización)."""
    company = owned_company(company_id, user, session, write=True)
    return _bank_sync(session, _owned_bank_connection(company_id, connection_id, session), company)


@app.delete("/api/companies/{company_id}/bank-connections/{connection_id}")
def delete_bank_connection(
    company_id: int,
    connection_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Desvincular: borra el Item en Plaid (`/item/remove`) y la conexión. Los movimientos ya
    importados SE QUEDAN en el libro (son contabilidad, no caché del banco). Si el banco ya había
    cortado la conexión, se borra igual."""
    owned_company(company_id, user, session, write=True)
    conn = _owned_bank_connection(company_id, connection_id, session)
    revoked = True
    try:
        bank.remove_item(bank.decrypt_token(conn.access_token_enc), conn.environment)
    except (bank.BankError, bank.BankConfigError):
        revoked = False  # cortada o token ilegible: no queda acceso que revocar en Plaid
    session.delete(conn)
    session.commit()
    return {"deleted": connection_id, "revoked": revoked}


class BankRemovedDecision(BaseModel):
    action: str  # "keep" (quitar la marca, el movimiento se queda) | "delete" (eliminarlo del libro)


@app.post("/api/companies/{company_id}/transactions/{transaction_id}/bank-removed")
def decide_bank_removed(
    company_id: int,
    transaction_id: int,
    payload: BankRemovedDecision,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """El banco vinculado RETIRÓ un movimiento ya registrado (aviso `removed` de Plaid) y el bookkeeper
    decide: `keep` quita la marca y lo deja; `delete` lo elimina del libro (con sus etiquetas y su
    formato; el historial de correcciones se queda: es historia). Solo filas marcadas por el banco."""
    owned_company(company_id, user, session, write=True)
    tx = session.get(Transaction, transaction_id)
    if not tx or tx.company_id != company_id:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if tx.bank_removed_at is None:
        raise HTTPException(status_code=409, detail="not_removed_by_bank")
    if payload.action == "keep":
        tx.bank_removed_at = None
        session.add(tx)
    elif payload.action == "delete":
        for model in (TransactionLabel, CellFormat):
            for row in session.exec(select(model).where(model.transaction_id == tx.id)).all():
                session.delete(row)
        session.delete(tx)
    else:
        raise HTTPException(status_code=422, detail="action must be keep or delete")
    session.commit()
    return {"id": transaction_id, "action": payload.action}


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
    had_sector, old_sector, old_form = bool(company.sector), company.sector, company.tax_form
    if data.get("name"):
        company.name = data["name"].strip()
    if data.get("sector"):
        company.sector = data["sector"].strip().lower()
    if data.get("tax_form"):
        if data["tax_form"] not in TAX_FORMS:
            raise HTTPException(status_code=422, detail={"code": "bad_tax_form"})
        if demo.DEMO_MODE and data["tax_form"] != "schedule_c":
            raise HTTPException(status_code=403, detail={"code": "demo_schedule_c"})  # el demo es Schedule C
        company.tax_form = data["tax_form"]
    if "trade" in data:
        profile = dict(company.profile or {})   # copia: un JSON mutado en sitio no se guarda
        trade = (data["trade"] or "").strip()[:120]
        if trade:
            profile["trade"] = trade
        else:
            profile.pop("trade", None)
        company.profile = profile
    session.add(company)
    session.flush()
    # EL PLAN DE CUENTAS DEL LIBRO (18-oct): al elegir el sector (el alta) recibe su copia del plan por
    # defecto; si después cambia de formulario o de sector, pasa al plan nuevo sin perder lo que está en uso.
    if company.sector and not had_sector:
        seed_chart(session, company)
    elif had_sector and (company.sector != old_sector or company.tax_form != old_form):
        switch_plan(session, company)
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
    for model in (Transaction, CategoryRule, CompanyCategory, PnlReport, CompanyMember,
                  CategorizationChange,   # + el historial de correcciones del libro
                  BankConnection):        # + sus bancos vinculados (Teller)
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
    company = console_company(company_id, user, session)
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


@app.get("/api/companies/{company_id}/categories")
def get_categories(
    company_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Plan de cuentas EFECTIVO del libro (`{sección: [categorías activas]}`, 18-oct: el plan es por libro).
    Lo usa el frontend para el selector de categoría (y derivar el account_type)."""
    # FASE 13 (decisión de Jose, 30-sep): el dueño del negocio NO ve el libro en construcción —
    # cambia todo el año. Lo ve cuando el bookkeeper se lo ENVÍA. → solo la oficina (no CLIENT).
    company = console_company(company_id, user, session)
    return categories_for_company(session, company_id, company.sector)


# ==================================================================================================
# MOVIMIENTOS — EL PLAN DE CUENTAS DEL LIBRO (18-oct-2026). Cada libro tiene SU copia del plan por
# defecto (`categories.py`) y el bookkeeper la edita en Ajustes: renombrar (o fusionar con otra),
# agregar, mover de sección, cambiar la línea del IRS y quitar (en uso → se archiva). Renombrar y mover
# arrastran los movimientos y las reglas de esa categoría, y queda en el historial de cada movimiento.
# ==================================================================================================

class ChartCreate(BaseModel):
    """Una categoría nueva del plan. El nombre es libre (en inglés); la sección y la línea las pide el editor."""
    account_type: AccountType
    name: str
    irs_line: Optional[str] = None   # None = la que se sugiere para la sección (`suggested_line`)


class ChartUpdate(BaseModel):
    name: Optional[str] = None
    account_type: Optional[AccountType] = None
    irs_line: Optional[str] = None   # "" = sin línea
    archived: Optional[bool] = None
    merge: bool = False              # renombrar a un nombre que YA existe = fusionar con esa categoría


def _chart_usage(session: Session, company_id: int) -> tuple[Counter, Counter]:
    """Cuántos movimientos y cuántas reglas usan cada categoría (SQL: GROUP BY)."""
    txs = Counter(dict(session.exec(
        select(Transaction.category, func.count(Transaction.id)).where(Transaction.company_id == company_id)
        .where(Transaction.category.is_not(None)).group_by(Transaction.category)).all()))
    rules = Counter(dict(session.exec(
        select(CategoryRule.category, func.count(CategoryRule.id)).where(CategoryRule.company_id == company_id)
        .group_by(CategoryRule.category)).all()))
    return txs, rules


def _is_locked(cat: CompanyCategory) -> bool:
    """Las que escribe el detector de transferencias (`rules.classify_transfer`): no se renombran, no se
    mueven ni se quitan."""
    return cat.name in LOCKED or (cat.default_key or "") in LOCKED


def _chart_out(cat: CompanyCategory, txs: Counter, rules: Counter, form: str = "schedule_c") -> dict:
    return {"id": cat.id, "name": cat.name, "account_type": cat.account_type.value, "irs_line": cat.irs_line,
            "archived": cat.archived_at is not None, "locked": _is_locked(cat),
            "help": help_for(cat.default_key, form), "custom": cat.default_key is None,
            "tx_count": txs.get(cat.name, 0), "rule_count": rules.get(cat.name, 0)}


def _chart_category(session: Session, company_id: int, cat_id: int) -> CompanyCategory:
    cat = session.get(CompanyCategory, cat_id)
    if cat is None or cat.company_id != company_id:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    return cat


def _chart_line(company: Company, line: Optional[str]) -> Optional[str]:
    """Una línea del IRS válida PARA EL FORMULARIO DEL LIBRO (o ninguna)."""
    line = (line or "").strip() or None
    if line is not None and line_form(line) != (company.tax_form or "schedule_c"):
        raise HTTPException(status_code=422, detail={"code": "bad_line"})
    return line


def _chart_name(name: Optional[str]) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise HTTPException(status_code=422, detail={"code": "empty_name"})
    if len(name) > 80 or ":" in name:   # ":" separa el espacio de nombres de las traducciones
        raise HTTPException(status_code=422, detail={"code": "bad_name"})
    return name


def _same_name(session: Session, company_id: int, name: str, but: Optional[int] = None) -> Optional[CompanyCategory]:
    for c in session.exec(select(CompanyCategory).where(CompanyCategory.company_id == company_id)).all():
        if c.id != but and c.name.lower() == name.lower():
            return c
    return None


def _next_position(session: Session, company_id: int, section: AccountType) -> int:
    top = session.exec(select(func.max(CompanyCategory.position)).where(CompanyCategory.company_id == company_id)
                       .where(CompanyCategory.account_type == section)).one()
    return (top if top is not None else -1) + 1


def _relabel(session: Session, company_id: int, user_id: Optional[int], old: str, old_section: AccountType,
             new: str, new_section: AccountType, via: str) -> int:
    """Lleva los movimientos, las reglas y el historial de la categoría `old` a `new` / `new_section`.
    Un movimiento cambia de sección solo si estaba en la de la categoría (uno que ya estaba en otra es un
    error aparte: no se tapa). La HISTORIA pasa a las etiquetas nuevas (el ④ aprende de ella y debe
    proponer cuentas que existen), y cada movimiento recibe su fila del cambio (`via`): queda en su
    historial. Lo escrito a mano sigue siendo a mano. Devuelve cuántos movimientos cambiaron."""
    o_sec, n_sec = old_section.value, new_section.value
    for side in ("before", "after"):
        cat_col = getattr(CategorizationChange, f"category_{side}")
        for ch in session.exec(select(CategorizationChange).where(CategorizationChange.company_id == company_id)
                               .where(cat_col == old)).all():
            setattr(ch, f"category_{side}", new)
            if getattr(ch, f"account_type_{side}") == o_sec:
                setattr(ch, f"account_type_{side}", n_sec)
            session.add(ch)
    for d in session.exec(select(ProposalDismissal).where(ProposalDismissal.company_id == company_id)
                          .where(ProposalDismissal.category == old)).all():
        d.category = new
        if d.account_type == o_sec:
            d.account_type = n_sec
        session.add(d)
    for r in session.exec(select(CategoryRule).where(CategoryRule.company_id == company_id)
                          .where(CategoryRule.category == old)).all():
        r.category = new
        if r.account_type == old_section:
            r.account_type = new_section
        session.add(r)
    session.flush()
    changed = 0
    for tx in session.exec(select(Transaction).where(Transaction.company_id == company_id)
                           .where(Transaction.category == old)).all():
        before = snapshot(tx)
        tx.category = new
        if tx.account_type == old_section:
            tx.account_type = new_section
        session.add(tx)
        changed += record_change(session, tx, before, via=via, user_id=user_id) is not None
    return changed


@app.get("/api/companies/{company_id}/chart")
def get_chart(
    company_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """El plan del libro para el editor de Ajustes: cada categoría con su sección, su línea del IRS, si está
    archivada o bloqueada, su ayuda y cuántos movimientos y reglas la usan. Más las líneas del formulario
    del libro y la que se sugiere por sección."""
    company = console_company(company_id, user, session)
    rows = chart_rows(session, company_id)
    session.commit()   # por si el libro recibió aquí su copia del plan
    txs, rules = _chart_usage(session, company_id)
    form = company.tax_form or "schedule_c"
    return {"tax_form": form,
            "lines": [{"code": c, "label": lbl} for c, lbl in LINES[form]],
            "suggested": {s.value: suggested_line(form, s.value) for s in AccountType},
            "accounts": [_chart_out(c, txs, rules, form) for c in rows]}


@app.post("/api/companies/{company_id}/chart")
def create_chart_category(
    company_id: int,
    payload: ChartCreate,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Agrega una categoría al plan del libro, con su sección y su línea (por defecto, la sugerida)."""
    company = owned_company(company_id, user, session, write=True)
    chart_rows(session, company_id)
    name = _chart_name(payload.name)
    other = _same_name(session, company_id, name)
    if other is not None:
        raise HTTPException(status_code=409, detail={"code": "exists", "id": other.id, "name": other.name})
    line = (_chart_line(company, payload.irs_line) if payload.irs_line is not None
            else suggested_line(company.tax_form or "schedule_c", payload.account_type.value))
    cat = CompanyCategory(company_id=company_id, account_type=payload.account_type, name=name, irs_line=line,
                          position=_next_position(session, company_id, payload.account_type))
    session.add(cat)
    session.commit()
    session.refresh(cat)
    return _chart_out(cat, Counter(), Counter())


@app.patch("/api/companies/{company_id}/chart/{category_id}")
def update_chart_category(
    company_id: int,
    category_id: int,
    payload: ChartUpdate,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Renombra (o fusiona con otra, con `merge`), mueve de sección, cambia la línea o archiva/restaura una
    categoría. Renombrar y mover arrastran sus movimientos y sus reglas (`_relabel`)."""
    company = owned_company(company_id, user, session, write=True)
    cat = _chart_category(session, company_id, category_id)
    data = payload.model_dump(exclude_unset=True)
    touches = {"name", "account_type", "archived"} & data.keys()
    if _is_locked(cat) and (touches - {"name"} or (data.get("name") and _chart_name(data["name"]) != cat.name)):
        raise HTTPException(status_code=409, detail={"code": "locked"})
    moved = 0
    if "irs_line" in data:
        cat.irs_line = _chart_line(company, data["irs_line"])
    if data.get("name"):
        name = _chart_name(data["name"])
        other = _same_name(session, company_id, name, but=cat.id)
        if other is not None:
            if not data.get("merge"):
                raise HTTPException(status_code=409, detail={"code": "exists", "id": other.id, "name": other.name,
                                                             "account_type": other.account_type.value})
            # FUSIONAR: todo lo de esta pasa a la otra (con su sección) y esta sale del plan.
            moved = _relabel(session, company_id, user.id, cat.name, cat.account_type, other.name,
                             other.account_type, VIA_CHART_RENAME)
            session.delete(cat)
            session.commit()
            txs, rules = _chart_usage(session, company_id)
            return {**_chart_out(other, txs, rules, company.tax_form), "merged_from": category_id, "moved": moved}
        if name != cat.name:
            moved += _relabel(session, company_id, user.id, cat.name, cat.account_type, name, cat.account_type,
                              VIA_CHART_RENAME)
            cat.name = name
    if data.get("account_type") and data["account_type"] != cat.account_type:
        new_sec = data["account_type"]
        moved += _relabel(session, company_id, user.id, cat.name, cat.account_type, cat.name, new_sec, VIA_CHART_MOVE)
        cat.account_type = new_sec
        cat.position = _next_position(session, company_id, new_sec)
    if "archived" in data:
        cat.archived_at = (cat.archived_at or datetime.now(timezone.utc).replace(tzinfo=None)) if data["archived"] else None
    session.add(cat)
    session.commit()
    session.refresh(cat)
    txs, rules = _chart_usage(session, company_id)
    return {**_chart_out(cat, txs, rules, company.tax_form), "moved": moved}


@app.delete("/api/companies/{company_id}/chart/{category_id}")
def delete_chart_category(
    company_id: int,
    category_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Quita una categoría del plan. Si algún movimiento o regla la usa, se ARCHIVA (sale del selector, lo
    ya categorizado se queda); si no, se borra."""
    owned_company(company_id, user, session, write=True)
    cat = _chart_category(session, company_id, category_id)
    if _is_locked(cat):
        raise HTTPException(status_code=409, detail={"code": "locked"})
    txs, rules = _chart_usage(session, company_id)
    if txs.get(cat.name) or rules.get(cat.name):
        cat.archived_at = cat.archived_at or datetime.now(timezone.utc).replace(tzinfo=None)
        session.add(cat)
        session.commit()
        return {"archived": category_id}
    session.delete(cat)
    session.commit()
    return {"deleted": category_id}


@app.post("/api/companies/{company_id}/custom-categories")
def add_custom_category(
    company_id: int,
    payload: CustomCategoryCreate,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Agregar una categoría AL VUELO (desde la celda de la Hoja o el diálogo de una regla). Idempotente:
    si ya existe en esa sección, la devuelve (y si estaba archivada, la restaura). Su línea, la sugerida."""
    company = owned_company(company_id, user, session, write=True)
    chart_rows(session, company_id)
    name = _chart_name(payload.name)
    other = _same_name(session, company_id, name)
    if other is not None:
        if other.account_type != payload.account_type:
            raise HTTPException(status_code=409, detail="Esa categoría ya existe en otra sección del plan.")
        if other.archived_at is not None:
            other.archived_at = None
            session.add(other)
            session.commit()
            session.refresh(other)
        return other
    cat = CompanyCategory(company_id=company_id, account_type=payload.account_type, name=name,
                          irs_line=suggested_line(company.tax_form or "schedule_c", payload.account_type.value),
                          position=_next_position(session, company_id, payload.account_type))
    session.add(cat)
    session.commit()
    session.refresh(cat)
    return cat


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
    console_company(company_id, user, session)
    rules = session.exec(
        select(CategoryRule)
        .where(CategoryRule.scope == RuleScope.COMPANY)
        .where(CategoryRule.company_id == company_id)
    ).all()
    txs = session.exec(
        select(Transaction).where(Transaction.company_id == company_id)
    ).all()
    # ORDEN REAL de evaluación (4-oct): el bookkeeper ve en qué lugar corre cada regla, en vez de
    # deducirlo de prioridad + largo + ventana. Y qué reglas cazan los mismos movimientos.
    ranked = load_rules(session, company_id, "")
    order = {r.id: i for i, r in enumerate(ranked, start=1)}
    by_id = {r.id: r for r in rules}
    overlaps = rule_overlaps(ranked, [t for t in txs if t.source != "manual"])
    clash = lambda c: [{"id": k, "match_text": by_id[k].match_text, "rows": n}  # noqa: E731
                       for k, n in c.most_common() if k in by_id]
    out = []
    for r in rules:
        # Lo escrito a mano NO cuenta: está desconectado de las reglas (Jose, 3-oct).
        hit = [t for t in txs if t.source != "manual"
               and match_rule([r], matchable_text(t.description, t.description_extra), t.date, t.amount) is not None]
        matches = len(hit)
        inflow = sum(1 for t in hit if t.is_inflow)
        # Hacia dónde va el dinero de lo que caza: así el editor de categorías ofrece primero las
        # secciones que tienen sentido (un pago no debería ir a Ingresos).
        direction = None if not hit else ("in" if inflow == len(hit) else "out" if inflow == 0 else "mixed")
        out.append({
            "id": r.id, "match_text": r.match_text,
            "account_type": r.account_type.value if r.account_type else None,
            "category": r.category, "subcategory": r.subcategory, "matches": matches,
            "direction": direction,
            "priority": r.priority, "style": r.style, **_rule_out(r),
            "order": order.get(r.id),  # None = no corre (sin sección del P&L)
            "origin": r.origin,        # "agent" | "import" | None
            "wins": overlaps.get(r.id, {}).get("wins", 0),
            "lost_to": clash(overlaps[r.id]["lost_to"]) if r.id in overlaps else [],
            "beats": clash(overlaps[r.id]["beats"]) if r.id in overlaps else [],
            # `scope`/`sector`: si la regla es de ESTE libro o de todo un sector (la hoja de reglas
            # los muestra como una sola columna "Sector": "This book" o el nombre del sector).
            "scope": r.scope.value, "sector": r.sector,
        })
    out.sort(key=lambda x: x["matches"], reverse=True)
    return out


class RulePreviewIn(BaseModel):
    """Vista previa de una regla ANTES de crearla. Sin `keyword`, se sugiere a partir de la fila."""
    keyword: Optional[str] = None
    transaction_id: Optional[int] = None
    valid_from: Optional[date] = None  # con ventana: qué cazaría SOLO entre esas fechas
    valid_to: Optional[date] = None
    amount_min: Optional[Decimal] = None  # con monto: qué cazaría SOLO en ese rango
    amount_max: Optional[Decimal] = None


@app.post("/api/companies/{company_id}/rules/preview")
def preview_rule(
    company_id: int,
    payload: RulePreviewIn,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Qué cazaría una regla con esta palabra clave, sin crear nada (la ventana "Crear regla" la
    llama mientras el bookkeeper escribe). Con `transaction_id` y sin `keyword`, sugiere una palabra
    clave limpia para esa fila (`rules.suggest_keyword`). Devuelve cuántos movimientos caza, cuántos
    están Pendientes, cuántos son escritos a mano (no se tocan), si el dinero entra o sale, y si ya
    existe una regla con esa palabra."""
    console_company(company_id, user, session)
    txs = session.exec(select(Transaction).where(Transaction.company_id == company_id)
                       .order_by(Transaction.date.desc())).all()
    keyword = (payload.keyword or "").strip()
    seed = None
    if payload.transaction_id is not None:
        seed = next((t for t in txs if t.id == payload.transaction_id), None)
        if seed is None:
            raise HTTPException(status_code=404, detail="Transacción no encontrada.")
        if not keyword:
            keyword = suggest_keyword(seed.description, seed.description_extra)
    out = {"keyword": keyword, "matches": 0, "pending": 0, "manual": 0, "inflow": 0, "outflow": 0,
           "examples": [], "duplicate": None, "seed_matched": None}
    if len(normalize(keyword)) < 2:
        return out
    _valid_window(payload.valid_from, payload.valid_to)
    _valid_amount(payload.amount_min, payload.amount_max)
    probe = CategoryRule(id=-1, scope=RuleScope.COMPANY, company_id=company_id, match_text=keyword,
                         account_type=AccountType.OPERATING_EXPENSE, category="-",
                         valid_from=payload.valid_from, valid_to=payload.valid_to,
                         amount_min=payload.amount_min, amount_max=payload.amount_max)
    for t in txs:
        if match_rule([probe], matchable_text(t.description, t.description_extra), t.date, t.amount) is None:
            continue
        out["matches"] += 1
        out["pending"] += t.category is None
        out["manual"] += t.source == "manual"
        out["inflow" if t.is_inflow else "outflow"] += 1
        if len(out["examples"]) < 8:
            out["examples"].append({"id": t.id, "date": t.date.isoformat(), "description": t.description,
                                    "amount": float(t.amount), "is_inflow": bool(t.is_inflow),
                                    "category": t.category, "source": t.source})
    if seed is not None:
        out["seed_matched"] = match_rule([probe], matchable_text(seed.description, seed.description_extra),
                                          seed.date, seed.amount) is not None
    dup = session.exec(select(CategoryRule).where(CategoryRule.company_id == company_id)
                       .where(CategoryRule.scope == RuleScope.COMPANY)).all()
    for r in dup:
        if _same_rule(r, keyword, payload.valid_from, payload.valid_to, payload.amount_min, payload.amount_max):
            out["duplicate"] = {"id": r.id, "match_text": r.match_text, "category": r.category}
            break
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
    _valid_window(payload.valid_from, payload.valid_to)
    _valid_amount(payload.amount_min, payload.amount_max)
    same = session.exec(
        select(CategoryRule)
        .where(CategoryRule.scope == RuleScope.COMPANY)
        .where(CategoryRule.company_id == company_id)
        .where(CategoryRule.match_text == match_text)
    ).all()
    if any(_same_rule(r, match_text, payload.valid_from, payload.valid_to, payload.amount_min, payload.amount_max)
           for r in same):
        raise HTTPException(status_code=409, detail="ya existe una regla con ese keyword y esas condiciones")
    chart = categories_for_company(session, company_id, company.sector)  # sector + propias
    if payload.category not in (chart.get(payload.account_type.value) or []):
        raise HTTPException(status_code=422, detail="categoría fuera del plan de cuentas")
    rule = CategoryRule(
        scope=RuleScope.COMPANY, company_id=company_id, match_text=match_text,
        account_type=payload.account_type, category=payload.category,
        subcategory=(payload.subcategory or "").strip() or None,
        valid_from=payload.valid_from, valid_to=payload.valid_to,
        amount_min=_money(payload.amount_min), amount_max=_money(payload.amount_max),
    )
    session.add(rule)
    if payload.priority is not None:
        place_priority(session, company_id, rule, _valid_priority(payload.priority))  # sin repetir
    applied = apply_rule_to_transactions(session, company_id, rule, user_id=user.id)
    # Una regla nueva puede tomar transacciones que otra regla con más prioridad debe conservar.
    reassigned = reapply_rule_precedence(session, company_id, user_id=user.id)
    session.commit()
    session.refresh(rule)
    return {"id": rule.id, "match_text": rule.match_text,
            "account_type": rule.account_type.value, "category": rule.category,
            "subcategory": rule.subcategory, "priority": rule.priority, **_rule_out(rule),
            "applied": applied, "reassigned": reassigned}


def _valid_priority(value: Optional[int]) -> Optional[int]:
    """Prioridad manual: entero ≥ 1, o None (orden automático)."""
    if value is None:
        return None
    if value < 1:
        raise HTTPException(status_code=422, detail="la prioridad debe ser 1 o mayor")
    return value


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
    company = owned_company(company_id, user, session, write=True)
    rule = _owned_company_rule(company_id, rule_id, session)
    data = payload.model_dump(exclude_unset=True)
    if data.get("category") or data.get("account_type"):
        # La categoría tiene que estar en el plan del LIBRO y en su sección (18-oct: el plan es por libro).
        chart = categories_for_company(session, company_id, company.sector)
        cat = data.get("category") or rule.category
        at = data.get("account_type") or rule.account_type
        if cat not in (chart.get(at.value if at else "") or []):
            home = next((s for s, cats in chart.items() if cat in cats), None)
            if home is None or "account_type" in data:
                raise HTTPException(status_code=422, detail="categoría fuera del plan de cuentas")
            data["account_type"] = AccountType(home)
    if "subcategory" in data:  # "" desde la UI = limpiar (volver al match_text)
        data["subcategory"] = (data["subcategory"] or "").strip() or None
    if "priority" in data:
        # Sin repetir (4-oct): poner la 1 inserta la regla ahí y empuja a las demás, como en Excel.
        place_priority(session, company_id, rule, _valid_priority(data.pop("priority")))
        data["_priority"] = True  # marca para lo de abajo (no es un campo)
    if "style" in data:
        st = data["style"] or {}
        bg = st.get("bg")
        clean = {"bg": bg if isinstance(bg, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", bg) else None,
                 "bold": bool(st.get("bold")), "italic": bool(st.get("italic"))}
        data["style"] = clean if (clean["bg"] or clean["bold"] or clean["italic"]) else None
    if "match_text" in data:
        data["match_text"] = (data["match_text"] or "").strip().upper()
        if len(normalize(data["match_text"])) < 2:
            raise HTTPException(status_code=422, detail="keyword demasiado corto")
    if data.keys() & {"match_text", "valid_from", "valid_to", "amount_min", "amount_max"}:
        mt = data.get("match_text", rule.match_text)
        vf = data.get("valid_from", rule.valid_from)
        vt = data.get("valid_to", rule.valid_to)
        amin = _money(data.get("amount_min", rule.amount_min))
        amax = _money(data.get("amount_max", rule.amount_max))
        for k, v in (("amount_min", amin), ("amount_max", amax)):
            if k in data:
                data[k] = v
        _valid_window(vf, vt)
        _valid_amount(amin, amax)
        others = session.exec(select(CategoryRule).where(CategoryRule.company_id == company_id)
                              .where(CategoryRule.id != rule.id)).all()
        if any(_same_rule(r, mt, vf, vt, amin, amax) for r in others):
            raise HTTPException(status_code=409, detail="ya existe una regla con ese keyword y esas condiciones")
        # Cambiar la palabra clave o las condiciones puede SOLTAR filas que ya no caen en ellas: vuelven
        # al motor (otra regla, transferencia o Pendiente). Lo manual no se toca.
        cond = CategoryRule(match_text=mt, category="-", scope=RuleScope.COMPANY,
                            valid_from=vf, valid_to=vt, amount_min=amin, amount_max=amax)
        released = [t for t in session.exec(select(Transaction).where(Transaction.company_id == company_id)
                                            .where(Transaction.matched_rule_id == rule.id)).all()
                    if t.source != "manual"
                    and match_rule([cond], matchable_text(t.description, t.description_extra), t.date, t.amount) is None]
    else:
        released = []
    moved_priority = bool(data.pop("_priority", False))
    for field, value in data.items():
        setattr(rule, field, value)
    session.add(rule)
    session.flush()
    ranked = load_rules(session, company_id, "") if released else []
    for t in released:
        before = snapshot(t)
        redecide_transaction(t, ranked)
        session.add(t)
        record_change(session, t, before, via=VIA_RULE_PRECEDENCE, user_id=user.id)
    # Cambiar SOLO la prioridad no re-aplica la regla a todo lo que matchea (eso pisaría lo que
    # otra regla o el bookkeeper decidieron): solo se re-decide qué regla gana.
    # Cambiar SOLO cómo se ve (style) no toca ninguna transacción.
    applied = (apply_rule_to_transactions(session, company_id, rule, user_id=user.id)
               if data.keys() - {"style"} else 0)
    reassigned = (reapply_rule_precedence(session, company_id, user_id=user.id)
                  if (data.keys() - {"style"}) or moved_priority else 0)
    session.commit()
    session.refresh(rule)
    return {"id": rule.id, "match_text": rule.match_text,
            "account_type": rule.account_type.value if rule.account_type else None,
            "category": rule.category, "subcategory": rule.subcategory,
            "priority": rule.priority, "style": rule.style, **_rule_out(rule),
            "applied": applied, "reassigned": reassigned, "released": len(released)}


class RuleEditPreviewIn(BaseModel):
    match_text: str


@app.post("/api/companies/{company_id}/rules/{rule_id}/edit-preview")
def preview_edit_rule(
    company_id: int,
    rule_id: int,
    payload: RuleEditPreviewIn,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """ANTES de cambiar la palabra clave de una regla (14-oct): cuántas filas conserva, cuántas suelta
    (y a dónde irían) y cuántas toma (y de dónde vienen). No toca nada."""
    console_company(company_id, user, session)
    rule = _owned_company_rule(company_id, rule_id, session)
    match_text = (payload.match_text or "").strip().upper()
    if len(normalize(match_text)) < 2:
        raise HTTPException(status_code=422, detail="keyword demasiado corto")
    changed = CategoryRule(id=rule.id, scope=RuleScope.COMPANY, company_id=company_id, match_text=match_text,
                           account_type=rule.account_type, category=rule.category, subcategory=rule.subcategory,
                           priority=rule.priority, valid_from=rule.valid_from, valid_to=rule.valid_to,
                           amount_min=rule.amount_min, amount_max=rule.amount_max)
    txs = session.exec(select(Transaction).where(Transaction.company_id == company_id)).all()
    sim = simulate_rule_change(load_rules(session, company_id, ""), rule.id, changed, txs)
    names = {r.id: r for r in session.exec(select(CategoryRule).where(CategoryRule.company_id == company_id)).all()}
    named = lambda c: [{"id": k, "match_text": names[k].match_text, "category": names[k].category, "rows": n}
                       for k, n in c.most_common() if k in names]
    dup = next((r for r in names.values() if r.id != rule.id and r.scope == RuleScope.COMPANY
                and _same_rule(r, match_text, rule.valid_from, rule.valid_to, rule.amount_min, rule.amount_max)), None)
    return {"id": rule.id, "before": rule.match_text, "match_text": match_text,
            **{k: v for k, v in sim.items() if k not in ("to_rules", "from_rules")},
            "to_rules": named(sim["to_rules"]), "from_rules": named(sim["from_rules"]),
            "duplicate": {"id": dup.id, "match_text": dup.match_text, "category": dup.category} if dup else None}


def _rule_rows(session: Session, company_id: int, rule_id: int) -> list[Transaction]:
    """Las filas que DECIDE una regla (no lo escrito a mano: está desconectado de las reglas)."""
    return session.exec(select(Transaction).where(Transaction.company_id == company_id)
                        .where(Transaction.matched_rule_id == rule_id)
                        .where(Transaction.source == "rule")).all()


@app.get("/api/companies/{company_id}/rules/{rule_id}/delete-preview")
def preview_delete_rule(
    company_id: int,
    rule_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """ANTES de borrar una regla (4-oct): cuántos movimientos decide y a dónde irían — a otra regla,
    a transferencia o a Pendiente. No toca nada."""
    console_company(company_id, user, session)
    rule = _owned_company_rule(company_id, rule_id, session)
    rows = _rule_rows(session, company_id, rule_id)
    sim = simulate_delete(load_rules(session, company_id, ""), rule_id, rows)
    names = {r.id: r for r in session.exec(select(CategoryRule).where(CategoryRule.company_id == company_id)).all()}
    return {"id": rule.id, "match_text": rule.match_text, "rows": len(rows),
            "to_rules": [{"id": k, "match_text": names[k].match_text, "category": names[k].category, "rows": n}
                         for k, n in sim["to_rules"].most_common()],
            "to_transfer": sim["to_transfer"], "to_pending": sim["to_pending"]}


@app.delete("/api/companies/{company_id}/rules/{rule_id}")
def delete_rule(
    company_id: int,
    rule_id: int,
    revert: bool = False,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Elimina una regla del reglamento.

    🔴 Decisión de Jose (4-oct): **borrar una regla devuelve sus movimientos al MOTOR** — como borrar
    la fórmula de una celda. Otra regla que aplique los toma; si no, transferencia; si no, quedan
    Pendientes. Lo escrito a mano no se toca. Cada fila que cambia deja su par antes → después en el
    historial (`via="rule_deleted"`). Las prioridades que quedan se renumeran sin hueco.

    Con `revert=true` —lo usa DESHACER al crear una regla— primero devuelve a su estado anterior las
    transacciones que esta regla movió (según el historial de correcciones), siempre que nadie las
    haya tocado después; lo que quede, va al motor igual."""
    owned_company(company_id, user, session, write=True)
    rule = _owned_company_rule(company_id, rule_id, session)
    reverted = 0
    if revert:
        changes = session.exec(
            select(CategorizationChange)
            .where(CategorizationChange.company_id == company_id)
            .where(CategorizationChange.matched_rule_id_after == rule_id)
            .order_by(CategorizationChange.id.desc())).all()
        seen: set[int] = set()
        for ch in changes:
            if ch.transaction_id in seen:
                continue  # solo el cambio MÁS RECIENTE de cada transacción
            seen.add(ch.transaction_id)
            tx = session.get(Transaction, ch.transaction_id)
            if tx is not None and tx.company_id == company_id and \
                    undo_change(session, tx, ch, user_id=user.id):
                reverted += 1
        session.flush()
    # Lo que la regla todavía decide vuelve al motor, sin ella.
    rest = [r for r in load_rules(session, company_id, "") if r.id != rule_id]
    moved = {"to_rule": 0, "to_transfer": 0, "to_pending": 0}
    for tx in _rule_rows(session, company_id, rule_id):
        before = snapshot(tx)
        redecide_transaction(tx, rest)
        moved["to_rule" if tx.source == "rule" else "to_transfer" if tx.source == "transfer" else "to_pending"] += 1
        session.add(tx)
        record_change(session, tx, before, via=VIA_RULE_DELETED, user_id=user.id)
    # Una fila que la regla solo "estampó" sin ser suya (procedencia vieja) pierde la referencia.
    for tx in session.exec(select(Transaction).where(Transaction.company_id == company_id)
                           .where(Transaction.matched_rule_id == rule_id)).all():
        tx.matched_rule_id = None
        session.add(tx)
    had_priority = rule.priority is not None
    session.delete(rule)
    session.flush()
    if had_priority:  # cerrar el hueco: 1, 2, 3… sin saltos
        ranked = sorted(session.exec(select(CategoryRule).where(CategoryRule.company_id == company_id)
                                     .where(CategoryRule.priority != None)).all(),  # noqa: E711
                        key=lambda r: (r.priority, r.id))
        for i, r in enumerate(ranked, start=1):
            if r.priority != i:
                r.priority = i
                session.add(r)
    session.commit()
    return {"deleted": rule_id, "reverted": reverted, **moved}

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
    rules = session.exec(
        select(CategoryRule).where(CategoryRule.company_id == company_id)
    ).all()
    # 23-oct (Auditoría/IA): la decisión del motor por fila, para no marcar un comercio separado por monto o fechas.
    import formula
    company = session.get(Company, company_id)
    ordered = load_rules(session, company_id, (company.sector if company else None) or "")
    items = [{
        "id": t.id, "description": t.description,
        "account_type": t.account_type.value if t.account_type else None,
        "category": t.category, "is_inflow": t.is_inflow,
        "matched_rule_id": t.matched_rule_id, "source": t.source,
        **dict(zip(("engine", "engine_conditional"), formula.engine_label(ordered, t))),
    } for t in txs]
    rules_by_id = {r.id: {
        "match_text": r.match_text,
        "account_type": r.account_type.value if r.account_type else None,
        "category": r.category,
    } for r in rules}
    return audit_categorization(items, rules_by_id)


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
    console_company(company_id, user, session)
    return session.exec(
        select(SheetLabel).where(SheetLabel.company_id == company_id).order_by(SheetLabel.id)
    ).all()


@app.post("/api/companies/{company_id}/labels")
def create_label(company_id: int, payload: LabelIn, user: User = Depends(current_user),
                 session: Session = Depends(get_session)):
    # write=True: crear una marca ES escribir. Sin esto pasaban VIEWER y CLIENT (bug previo).
    owned_company(company_id, user, session, write=True)
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
    # write=True: pintar una celda ES escribir. Sin esto pasaban VIEWER y CLIENT (bug previo).
    owned_company(company_id, user, session, write=True)
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
    console_company(company_id, user, session)
    return _company_audit(session, company_id)


# ─────────────────────────────────────────────────────────────────────────────────────────────
# NOTAS DEL LIBRO (FASE 11, área Auditoría / IA · 4-oct-2026). Lo que el bookkeeper le escribe al
# agente sobre ESTE cliente: 📌 tenlo en cuenta / 🚫 no me reportes. Lógica en `book_notes.py`.
# Leer = consola (el CLIENTE no las ve: es el criterio del bookkeeper). Escribir = operador.
# Una nota nunca categoriza: es la entrada del agente ④, que solo PROPONE filas de la fórmula.
# ─────────────────────────────────────────────────────────────────────────────────────────────

class BookNoteIn(BaseModel):
    text: Optional[str] = None
    kind: Optional[str] = None          # keep_in_mind | dont_report
    priority: Optional[str] = None      # high | normal
    merchant: Optional[str] = None      # alcance opcional
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    paused: Optional[bool] = None       # 18-oct: pausar / activar (solo en el PATCH)


class NotePreviewIn(BookNoteIn):
    note_id: Optional[int] = None       # probar un CAMBIO a una nota guardada (sin él, una nota nueva)
    lang: Optional[str] = "es"


def _note_out(n: "book_notes.BookNote") -> dict:
    """La nota + lo que entendió la IA de ella (18-oct, página de Notas)."""
    import note_checks
    return {**book_notes.to_dict(n), "understood": note_checks.understood(n)}


def _owned_note(session: Session, company_id: int, note_id: int) -> "book_notes.BookNote":
    note = session.get(book_notes.BookNote, note_id)
    if note is None or note.company_id != company_id:
        raise HTTPException(status_code=404, detail="note not found")
    return note


def _note_error(e: "book_notes.NoteError") -> HTTPException:
    return HTTPException(status_code=422, detail={"code": e.code})


@app.get("/api/companies/{company_id}/notes")
def list_book_notes(company_id: int, user: User = Depends(current_user),
                    session: Session = Depends(get_session)):
    console_company(company_id, user, session)
    return [_note_out(n) for n in book_notes.list_notes(session, company_id)]


@app.post("/api/companies/{company_id}/notes")
def create_book_note(company_id: int, payload: BookNoteIn, user: User = Depends(current_user),
                     session: Session = Depends(get_session)):
    owned_company(company_id, user, session, write=True)
    try:
        note = book_notes.create_note(session, company_id, payload.model_dump(exclude_unset=True, exclude={"paused"}),
                                      user_id=user.id)
    except book_notes.NoteError as e:
        raise _note_error(e)
    import note_checks
    session.flush()
    note_checks.adopt_preview(note)   # si se probó tal cual, su chequeo queda (no se vuelve a pagar)
    session.commit()
    session.refresh(note)
    return _note_out(note)


@app.patch("/api/companies/{company_id}/notes/{note_id}")
def update_book_note(company_id: int, note_id: int, payload: BookNoteIn,
                     user: User = Depends(current_user), session: Session = Depends(get_session)):
    owned_company(company_id, user, session, write=True)
    note = _owned_note(session, company_id, note_id)
    fields = payload.model_dump(exclude_unset=True, exclude={"paused"})
    try:
        if fields:
            book_notes.update_note(session, note, fields)
    except book_notes.NoteError as e:
        raise _note_error(e)
    if payload.paused is not None:
        book_notes.set_paused(session, note, payload.paused)
    import note_checks
    note_checks.adopt_preview(note)
    session.commit()
    session.refresh(note)
    return _note_out(note)


# La página de Notas (18-oct): PROBAR una nota antes de guardarla y QUÉ TOCÓ una guardada.
@app.post("/api/companies/{company_id}/notes/preview")
def preview_book_note(company_id: int, payload: NotePreviewIn, user: User = Depends(current_user),
                      session: Session = Depends(get_session)):
    """Qué entendería la IA y qué marcaría o callaría la nota, SIN guardarla. Gasta 1 llamada a Nemotron
    solo si es 📌 y ese texto no tiene ya un chequeo; 🚫 es gratis (lo aplica el código)."""
    import agent_calls
    import note_checks
    company = owned_company(company_id, user, session, write=True)
    since = agent_calls.mark(session, company)
    try:
        out = note_checks.preview(session, company, payload.model_dump(exclude_unset=True, exclude={"paused", "note_id", "lang"}),
                                  note_id=payload.note_id, user_id=user.id)
    except book_notes.NoteError as e:
        raise _note_error(e)
    except LookupError:
        raise HTTPException(status_code=404, detail={"code": "note_not_found"})
    except Exception as e:
        import nemotron
        if isinstance(e, nemotron.NemotronError):   # la llamada falló (quedó registrada): nada que mostrar
            raise HTTPException(status_code=502, detail={"code": "agent_error"})
        raise _agent_error(e)
    return {**out, "spent": agent_calls.spent(session, company, since)}


@app.get("/api/companies/{company_id}/notes/{note_id}/effects")
def book_note_effects(company_id: int, note_id: int, user: User = Depends(current_user),
                      session: Session = Depends(get_session)):
    """Lo que marcó la nota (📌) y lo que calla hoy (🚫), con sus movimientos."""
    import note_checks
    company = console_company(company_id, user, session)
    return note_checks.effects(session, company, _owned_note(session, company_id, note_id))


@app.delete("/api/companies/{company_id}/notes/{note_id}")
def delete_book_note(company_id: int, note_id: int, user: User = Depends(current_user),
                     session: Session = Depends(get_session)):
    owned_company(company_id, user, session, write=True)
    session.delete(_owned_note(session, company_id, note_id))
    session.commit()
    return {"deleted": note_id}


# ─────────────────────────────────────────────────────────────────────────────────────────────
# LOS AGENTES DE LA FÓRMULA (FASE 11, Auditoría / IA · 7-oct-2026), sobre Nemotron (Nebius).
# ④ /agents/draft: PROPONE filas de la fórmula (nunca las aplica: se aprueban con POST /rules).
# ① /agents/review: juzga los hallazgos del auditor. Lógica en `agents.py`; costo en `agent_calls.py`.
# 20-oct: un solo paso de agentes a la vez por libro (`agent_calls.exclusive`); el segundo recibe 409 agent_busy.
# Correr un agente = operador (gasta crédito y propone sobre el libro). Ver el costo = consola.
# ─────────────────────────────────────────────────────────────────────────────────────────────

class AgentRunIn(BaseModel):
    lang: Optional[str] = "es"          # idioma de las explicaciones (el de la interfaz)
    reasoning: Optional[bool] = None    # None = el de cada agente (`agent_models`); True/False lo fuerza
    focus_tx_ids: Optional[list[int]] = None   # ④: una regla para ESTE grupo de Pendientes
    note_id: Optional[int] = None              # ④: las filas de ESTA nota, recién guardada (14-oct)


def _agent_error(e: Exception) -> HTTPException:
    import agent_calls
    import nemotron
    if isinstance(e, agent_calls.CapReached):   # el de la oficina, o el GLOBAL del demo (Firm, 23-oct)
        return HTTPException(status_code=429, detail={"code": e.code})
    if isinstance(e, agent_calls.AlreadyRunning):   # 20-oct: otro paso de agentes ya corre sobre este libro
        return HTTPException(status_code=409, detail={"code": "agent_busy"})
    if isinstance(e, nemotron.NemotronUnavailable):
        return HTTPException(status_code=503, detail={"code": "ai_unavailable"})
    raise e


@app.post("/api/companies/{company_id}/agents/draft")
def run_formula_drafter(company_id: int, payload: AgentRunIn, user: User = Depends(current_user),
                        session: Session = Depends(get_session)):
    import agent_calls
    import agents
    company = owned_company(company_id, user, session, write=True)
    since = agent_calls.mark(session, company)
    try:
        with agent_calls.exclusive(company):
            res = agents.draft(session, company, lang=payload.lang or "es", reasoning=payload.reasoning,
                               user_id=user.id, focus_tx_ids=payload.focus_tx_ids, note_id=payload.note_id)
    except LookupError:
        raise HTTPException(status_code=404, detail={"code": "note_not_found"})
    except Exception as e:
        raise _agent_error(e)
    return {"outcome": res.outcome, "calls": res.calls, "spent": agent_calls.spent(session, company, since),
            "proposals": [agents.proposal_dict(p) for p in res.proposals],
            "rejected": [{"keyword": r["raw"].get("keyword") if isinstance(r["raw"], dict) else None,
                          "errors": r["errors"]} for r in res.rejected],
            # 20-oct: por qué no pasaron (la pantalla lo dice cuando pasan 0); "nothing_qualifies" en `outcome` =
            # ningún comercio tenía respaldo y no se llamó al modelo.
            "rejection_counts": res.rejection_counts,
            "usage": agent_calls.usage_dict(res.usage)}


@app.post("/api/companies/{company_id}/agents/review")
def run_reviewer(company_id: int, payload: AgentRunIn, user: User = Depends(current_user),
                 session: Session = Depends(get_session)):
    import agent_calls
    import agents
    company = owned_company(company_id, user, session, write=True)
    since = agent_calls.mark(session, company)
    try:
        with agent_calls.exclusive(company):
            res = agents.review(session, company, lang=payload.lang or "es", reasoning=payload.reasoning,
                                user_id=user.id)
    except Exception as e:
        raise _agent_error(e)
    return {"outcome": res.outcome, "silenced": res.silenced, "reviews": res.reviews,
            "usage": agent_calls.usage_dict(res.usage), "spent": agent_calls.spent(session, company, since)}


# "Auditar" (14-oct): el auditor (código) + las notas como chequeos + "¿personal para este negocio?" + el
# ① revisor. Sin redactar. Es la misma corrida que `/agents/review` (que ya hace todo eso), con otro nombre
# para la pantalla.
@app.post("/api/companies/{company_id}/agents/audit")
def run_audit(company_id: int, payload: AgentRunIn, user: User = Depends(current_user),
              session: Session = Depends(get_session)):
    return run_reviewer(company_id, payload, user, session)


class VerifyIn(BaseModel):
    lang: Optional[str] = "es"


def _rejudge(session: Session, company, res, lang: str, user_id: int) -> Optional[dict]:
    """La IDA Y VUELTA (14-oct): con la evidencia NUEVA del ②, el ① vuelve a juzgar ESOS hallazgos. Una
    vuelta, no un bucle. Si se acabó el tope o falla, la evidencia queda guardada igual."""
    import agents
    if not res.finding_ids:
        return None
    try:
        r = agents.review(session, company, lang=lang, user_id=user_id, finding_ids=res.finding_ids)
        return {"rejudged": r.reviewed, "outcome": r.outcome}
    except Exception:
        return {"rejudged": 0, "outcome": "error"}


@app.post("/api/companies/{company_id}/agents/verify")
def run_merchant_verifier(company_id: int, payload: Optional[VerifyIn] = None, user: User = Depends(current_user),
                          session: Session = Depends(get_session)):
    """② Verificador de comercios (Tavily), REDISEÑADO el 14-oct: busca solo los comercios que la
    auditoría marcó (memoria de la oficina primero; básica y, si no da confianza alta, una avanzada) y
    el ① vuelve a juzgar esos hallazgos. Nunca busca personas; no cambia ninguna categoría."""
    import agent_calls
    import merchant_verifier
    company = owned_company(company_id, user, session, write=True)
    since = agent_calls.mark(session, company)
    try:
        with agent_calls.exclusive(company):
            res = merchant_verifier.verify_flagged(session, company, user_id=user.id)
            again = _rejudge(session, company, res, (payload.lang if payload else None) or "es", user.id)
    except agent_calls.AlreadyRunning as e:
        raise _agent_error(e)
    except merchant_verifier.TavilyUnavailable:
        raise HTTPException(status_code=503, detail={"code": "search_unavailable"})
    return {"outcome": res.outcome, "items": res.items, "searched": res.searched,
            "from_cache": res.from_cache, "skipped": dict(res.skipped), "credits": res.credits,
            "flagged": res.flagged, "pending": res.pending,   # 20-oct: de dónde salió cada comercio
            "rejudge": again, "spent": agent_calls.spent(session, company, since)}


class SearchMerchantIn(BaseModel):
    transaction_id: int
    lang: Optional[str] = "es"


@app.post("/api/companies/{company_id}/agents/verify-merchant")
def search_merchant(company_id: int, payload: SearchMerchantIn, user: User = Depends(current_user),
                    session: Session = Depends(get_session)):
    """"Buscar este comercio" (14-oct): el comercio de UN movimiento, al instante, memoria de la oficina
    primero. Si trae evidencia nueva y el comercio tiene hallazgos, el ① los vuelve a juzgar."""
    import agent_calls
    import merchant_verifier
    company = owned_company(company_id, user, session, write=True)
    since = agent_calls.mark(session, company)
    try:
        with agent_calls.exclusive(company):
            res = merchant_verifier.search_one(session, company, payload.transaction_id, user_id=user.id)
            again = _rejudge(session, company, res, payload.lang or "es", user.id)
    except agent_calls.AlreadyRunning as e:
        raise _agent_error(e)
    except LookupError:
        raise HTTPException(status_code=404, detail={"code": "transaction_not_found"})
    except merchant_verifier.TavilyUnavailable:
        raise HTTPException(status_code=503, detail={"code": "search_unavailable"})
    return {"outcome": res.outcome, "item": res.items[0] if res.items else None,
            "skipped": next(iter(res.skipped), None), "searched": res.searched, "from_cache": res.from_cache,
            "rejudge": again, "spent": agent_calls.spent(session, company, since)}


@app.get("/api/companies/{company_id}/agents/estimate")
def agents_estimate(company_id: int, action: str, note_id: Optional[int] = None,
                    transaction_id: Optional[int] = None, lang: str = "es",
                    user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Cuánto costaría una acción, ANTES de correrla (llamadas a Nemotron y búsquedas de Tavily)."""
    import agents
    company = owned_company(company_id, user, session, write=True)
    if action not in ("audit", "verify", "draft", "review_book", "note", "search"):
        raise HTTPException(status_code=422, detail={"code": "bad_action"})
    return agents.estimate(session, company, action, note_id=note_id, transaction_id=transaction_id, lang=lang)


# ── La revisión del libro (FASE 11, 8-oct): la lista ÚNICA de hallazgos guardados + lo Pendiente
# agrupado + el encabezado de estado. Lógica en `book_review.py`. ──

class FindingResolveIn(BaseModel):
    status: str                      # "ok" (está bien, no vuelve a salir) | "open" (reabrir)
    note: Optional[str] = None


class DismissalIn(BaseModel):
    keyword: str
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    account_type: Optional[str] = None
    category: Optional[str] = None
    reason_code: str = "other"       # wrong_category | too_broad | one_off | not_now | other
    reason_text: Optional[str] = None


@app.get("/api/companies/{company_id}/audit/board")
def audit_board(company_id: int, lang: Optional[str] = None, user: User = Depends(current_user),
                session: Session = Depends(get_session)):
    """`lang`: el idioma de la pantalla — un veredicto del ① guardado en otro idioma se marca viejo."""
    import book_review
    company = console_company(company_id, user, session)
    return book_review.board(session, company, lang)


@app.get("/api/companies/{company_id}/audit/summary")
def audit_summary(company_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    import book_review
    company = console_company(company_id, user, session)
    return book_review.summary(session, company)


@app.post("/api/companies/{company_id}/audit/findings/{finding_id}/resolve")
def resolve_finding(company_id: int, finding_id: int, payload: FindingResolveIn,
                    user: User = Depends(current_user), session: Session = Depends(get_session)):
    import book_review
    company = owned_company(company_id, user, session, write=True)
    try:
        f = book_review.resolve(session, company, finding_id, payload.status, user_id=user.id, note=payload.note)
    except LookupError:
        raise HTTPException(status_code=404, detail="finding not found")
    except ValueError as e:
        raise HTTPException(status_code=422, detail={"code": str(e)})
    return {"id": f.id, "status": f.status}


@app.post("/api/companies/{company_id}/agents/dismissals")
def dismiss_proposal(company_id: int, payload: DismissalIn, user: User = Depends(current_user),
                     session: Session = Depends(get_session)):
    import book_review
    company = owned_company(company_id, user, session, write=True)
    try:
        d = book_review.dismiss(session, company, payload.model_dump(), user_id=user.id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail={"code": str(e)})
    return {"id": d.id}


@app.get("/api/companies/{company_id}/agents/usage")
def agent_usage(company_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    import agent_calls
    company = console_company(company_id, user, session)
    return agent_calls.summary(session, company)


# "El equipo trabajando" (18-oct, ronda de diseño): qué hizo cada agente en la última corrida del libro, con
# su modelo. Solo lee `AgentCall` (lógica en `agent_calls.team`). Consola, como `/agents/usage`.
# `since_id`: solo las llamadas de ESTA acción (la pantalla la sondea mientras corre; no gasta IA).
@app.get("/api/companies/{company_id}/agents/team")
def agent_team(company_id: int, since_id: Optional[int] = None, user: User = Depends(current_user),
               session: Session = Depends(get_session)):
    import agent_calls
    company = console_company(company_id, user, session)
    return agent_calls.team(session, company, since_id)


# El viejo PROPONEDOR (`POST /rule-proposals` y `/rule-proposals/validate`, con `ai.propose_rules` sobre
# Gemini) se borró el 13-oct (FASE 11): su pantalla, `RuleProposals.tsx`, ya no la montaba nadie y el
# agente ④ redactor hace ese trabajo sobre Nemotron (`POST /agents/draft`), con validador y prueba contra
# la historia. `/rule-proposals/apply` se queda: lo usan la hoja de Reglas (importar) y la aprobación del ④.

class RuleProposalApply(BaseModel):
    """Una regla aprobada del proponedor (lista para crear)."""
    keyword: str
    account_type: AccountType
    category: str
    subcategory: Optional[str] = None  # la trae el import; el proponedor no
    priority: Optional[int] = None     # ídem (columna "Priority" de la exportación)
    valid_from: Optional[date] = None  # ventana de fechas (columnas "From"/"To" de la exportación)
    valid_to: Optional[date] = None
    # De dónde sale (8-oct): "agent" = la propuso el agente ④ (la F11 lo manda al aprobar) · "import".
    origin: Optional[str] = None
    amount_min: Optional[Decimal] = None  # monto (columnas "Min amount"/"Max amount" de la exportación)
    amount_max: Optional[Decimal] = None


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
        if p.valid_from and p.valid_to and p.valid_from > p.valid_to:
            continue  # ventana al revés: no caza nada
        if (p.amount_min is not None and p.amount_min < 0) or (p.amount_max is not None and p.amount_max < 0) or \
                (p.amount_min is not None and p.amount_max is not None and p.amount_min > p.amount_max):
            continue  # monto inválido
        rule = create_company_rule(session, company_id, sector, p.keyword, p.account_type, p.category,
                                   (p.subcategory or "").strip() or None,
                                   p.priority if p.priority and p.priority >= 1 else None,
                                   p.valid_from, p.valid_to,
                                   p.origin if p.origin in ("agent", "import") else None,
                                   _money(p.amount_min), _money(p.amount_max))
        if rule is not None:
            created += 1
            session.flush()  # asigna id para apply_rule_to_transactions
            applied += apply_rule_to_transactions(session, company_id, rule, user_id=user.id)
    if created:
        session.flush()
        reapply_rule_precedence(session, company_id, user_id=user.id)  # varias reglas nuevas: que gane la que debe
    session.commit()
    # AUDITOR AUTOMÁTICO (FASE 9 Paso 4): tras cambiar el reglamento, el agente verifica los libros.
    return {"created": created, "applied": applied,
            "audit_findings": len(_company_audit(session, company_id))}


# Encabezados que escribe "Exportar" de la hoja de Reglas (frontend/src/components/RulesSheet.tsx).
_EXPORT_RULE_HEADERS = {"keyword": "keyword_column", "category": "category_column",
                        "subcategory": "subcategory_column", "section": "account_type_column"}


def _own_rule_columns(headers: list[str]) -> Optional[RuleColumnMapping]:
    """Si el archivo trae los encabezados de nuestra propia exportación, el mapeo es determinista."""
    found = {_EXPORT_RULE_HEADERS[h.strip().lower()]: h
             for h in headers if h.strip().lower() in _EXPORT_RULE_HEADERS}
    if "keyword_column" not in found or "category_column" not in found:
        return None
    return RuleColumnMapping(**found)


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
    # Importar el banco de reglas es trabajo de consola (además gasta IA en el mapeo).
    company = owned_company(company_id, user, session, write=True)
    sector = company.sector if company else "general"
    content = await file.read()
    headers, rows = parse_spreadsheet(content, file.filename)
    if not headers or not rows:
        raise HTTPException(status_code=422, detail="Archivo vacío o ilegible.")
    # Un archivo que salió de "Exportar" en la hoja de Reglas trae nuestros encabezados: se mapea
    # directo, sin gastar IA y sin riesgo de que la adivine mal. Cualquier otro formato → la IA.
    # Si no: Nemotron mira los ENCABEZADOS y un perfil de cada columna (nunca los valores), con el plan
    # de cuentas del libro para reconocer la columna de categorías (FASE 11, 13-oct).
    known = {c.lower() for cats in categories_for_company(session, company_id, sector).values() for c in cats}
    mapping = _own_rule_columns(headers) or map_rule_columns(headers, rows[:5], session=session, company=company,
                                                             known_categories=known)
    # "Priority" sale de nuestra exportación; se lee por nombre (la IA no la mapea).
    priority_col = next((h for h in headers if h.strip().lower() == "priority"), "")
    # "From"/"To" (la ventana de fechas) también salen de nuestra exportación y se leen por nombre.
    from_col = next((h for h in headers if h.strip().lower() in ("from", "valid from")), "")
    to_col = next((h for h in headers if h.strip().lower() in ("to", "valid to")), "")
    # "Min amount"/"Max amount" (la condición de monto, 8-oct): también por nombre.
    min_col = next((h for h in headers if h.strip().lower() in ("min amount", "amount min", "monto mín", "monto min")), "")
    max_col = next((h for h in headers if h.strip().lower() in ("max amount", "amount max", "monto máx", "monto max")), "")
    existing = [(r.match_text, r.valid_from, r.valid_to, _money(r.amount_min), _money(r.amount_max))
                for r in load_rules(session, company_id, sector)]
    preview = build_import_rules(
        headers, rows, mapping.keyword_column, mapping.category_column,
        mapping.account_type_column, sector, existing, mapping.subcategory_column, priority_col,
        from_col, to_col, min_col, max_col, chart=categories_for_company(session, company_id),
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
        if not any(match_rule([r], matchable_text(t.description, t.description_extra), t.date, t.amount) is not None for t in txs):
            session.delete(r)
            removed += 1
    session.commit()
    return {"removed": removed}


# --- P&L como PROYECTOS guardados (draft = recalcula en vivo · closed = congelado) ---

def _pnl_jsonable(pnl: dict) -> dict:
    """Convierte el P&L (con Decimals + breakdown) a JSON-serializable (Decimal → float).

    `uncategorized` (lo Pendiente, FASE 12) es un dict de conteos + Decimals: se convierte campo
    por campo. Si se congela un reporte SIN esta clave, el snapshot callaría lo Pendiente.
    """
    out: dict = {}
    for k, v in pnl.items():
        if k == "uncategorized":
            out[k] = {kk: (float(vv) if isinstance(vv, Decimal) else vv) for kk, vv in v.items()}
        elif k == "breakdown":
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
    """Lista los proyectos de P&L guardados (con su net income para mostrar en la lista).

    Lleva también `pending_count`: la lista enseña un net income por proyecto y, sin él, sería la
    única salida del P&L que calla lo Pendiente. `None` = **no se sabe** (un reporte CERRADO antes de
    la FASE 12 congeló un snapshot sin esa cuenta): se deja vacío en vez de afirmar un cero.
    """
    # FASE 13 (decisión de Jose, 30-sep): el dueño del negocio NO ve el libro en construcción —
    # cambia todo el año. Lo ve cuando el bookkeeper se lo ENVÍA. → solo la oficina (no CLIENT).
    console_company(company_id, user, session)
    reports = session.exec(
        select(PnlReport).where(PnlReport.company_id == company_id)
        .order_by(PnlReport.created_at.desc())
    ).all()
    out = []
    for r in reports:
        pnl = _report_pnl(session, company_id, r)
        u = pnl.get("uncategorized")
        out.append({**_report_meta(r), "net_income": pnl["net_income"],
                    "pending_count": u["count"] if u else None})
    return out


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
    # FASE 13 (decisión de Jose, 30-sep): el dueño del negocio NO ve el libro en construcción —
    # cambia todo el año. Lo ve cuando el bookkeeper se lo ENVÍA. → solo la oficina (no CLIENT).
    console_company(company_id, user, session)
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
    for_sheet: bool = False,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Lista las transacciones de una empresa (más recientes primero).

    `for_sheet=true` (la Hoja) adjunta a cada fila sus **etiquetas** y su **formato de celda**.
    🔄 13-oct: antes se llamaba `with_audit` y además corría el auditor entero en cada recarga para la
    columna Observación; Firm quitó esa columna (la marca de revisión sale de `GET /transactions/flags`),
    así que el auditor ya no corre aquí.
    """
    # FASE 13 (decisión de Jose, 30-sep): el dueño del negocio NO ve el libro en construcción —
    # cambia todo el año. Lo ve cuando el bookkeeper se lo ENVÍA. → solo la oficina (no CLIENT).
    console_company(company_id, user, session)
    txs = session.exec(
        select(Transaction)
        .where(Transaction.company_id == company_id)
        .order_by(Transaction.date.desc())
    ).all()
    if not for_sheet:
        return txs

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

    # (Lo que el banco vinculado retiró va en `bank_removed_at`; la marca de la Hoja sale de `flags`.)
    return [{
        **t.model_dump(),
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
    # FASE 13 (decisión de Jose, 30-sep): el dueño del negocio NO ve el libro en construcción —
    # cambia todo el año. Lo ve cuando el bookkeeper se lo ENVÍA. → solo la oficina (no CLIENT).
    console_company(company_id, user, session)
    return income_statement(session, company_id, start, end)


@app.get("/api/companies/{company_id}/pivot")
def get_pivot(
    company_id: int,
    start: Optional[date] = None,
    end: Optional[date] = None,
    account: Optional[list[str]] = Query(None),  # varias: ?account=A&account=B (filtro con casillas)
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """EL PIVOTE (FASE 10 paso 7): categoría → subcategoría → mes. Totales agregados en SQL."""
    # FASE 13 (decisión de Jose, 30-sep): el dueño del negocio NO ve el libro en construcción —
    # cambia todo el año. Lo ve cuando el bookkeeper se lo ENVÍA. → solo la oficina (no CLIENT).
    console_company(company_id, user, session)
    return pivot(session, company_id, start, end, account)


@app.get("/api/companies/{company_id}/pivot/leaves")
def get_pivot_leaves(
    company_id: int,
    section: str,
    category: str,
    subcategory: str,
    month: str,
    account: Optional[list[str]] = Query(None),  # el mismo filtro de cuentas que el árbol
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """El último `+` del pivote: las transacciones de ese mes, con día y descripción.
    Se piden aparte para no cargar el árbol entero al abrir."""
    # FASE 13 (decisión de Jose, 30-sep): el dueño del negocio NO ve el libro en construcción —
    # cambia todo el año. Lo ve cuando el bookkeeper se lo ENVÍA. → solo la oficina (no CLIENT).
    console_company(company_id, user, session)
    return pivot_leaves(session, company_id, section, category, subcategory, month, account)


@app.get("/api/companies/{company_id}/transactions/review")
def list_review(
    company_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Transacciones que la IA marcó como dudosas (necesitan confirmación humana)."""
    # FASE 13 (decisión de Jose, 30-sep): el dueño del negocio NO ve el libro en construcción —
    # cambia todo el año. Lo ve cuando el bookkeeper se lo ENVÍA. → solo la oficina (no CLIENT).
    console_company(company_id, user, session)
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
    reason = data.pop("reason", None)            # motivo de la corrección (solo historial)

    # EL TEXTO AGREGADO SOLO DONDE EL BANCO NO TRAE LA CONTRAPARTE (20-oct-2026): un cheque emitido
    # ("CHECK 1234") o un depósito ("MOBILE DEPOSIT REF…") no dicen a quién. En las demás filas el
    # banco ya trae quién es (Zelle/Venmo con FROM/TO, una ACH con ORIG CO NAME, un comercio con
    # nombre) — agregar texto ahí no aporta y podría reescribir la evidencia. El frontend ya bloquea
    # la celda; esto es el candado real (la Hoja no es el único camino a este endpoint).
    if "description_extra" in data and data["description_extra"] and not (
            is_check(tx.description) or is_deposit(tx.description)):
        raise HTTPException(
            status_code=422,
            detail=("This row can't take added text: the bank already names the other party. "
                    "Only checks and deposits (where it doesn't) can."),
        )
    # HISTORIAL DE CORRECCIONES (2-oct): foto ANTES de tocar nada → al final, el par antes/después.
    before = snapshot(tx)

    # VOLVER A SEGUIR LA REGLA (FASE 10) — el inverso de escribir a mano.
    #
    # Modelo mental de hoja de cálculo (idea de Jose): una celda puede tener una "fórmula" (la
    # decidió una regla → `matched_rule_id`) o un valor escrito "a mano" (`source="manual"`, sin
    # regla). Editar la celda rompe la fórmula; esto la restaura: busca la regla que matchearía
    # esta descripción y la vuelve a aplicar.
    if rule_action == "follow_rule":
        company = session.get(Company, tx.company_id)
        rules = load_rules(session, tx.company_id, company.sector if company else "")
        rule = match_rule(rules, matchable_text(tx.description, tx.description_extra), tx.date, tx.amount)
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
        session.add(tx)
        record_change(session, tx, before, via=VIA_FOLLOW_RULE, user_id=user.id, reason=reason)
        session.commit(); session.refresh(tx)
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
                applied = apply_rule_to_transactions(session, tx.company_id, rule, exclude_id=tx.id,
                                                     user_id=user.id)
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
                applied = apply_rule_to_transactions(session, tx.company_id, rule, exclude_id=tx.id,
                                                     user_id=user.id)
            elif tx.account_type is not None and tx.category:
                # El GUARDIÁN vetó la frase (genérica: cazaría otros comercios) → la corrección
                # queda como excepción puntual y el frontend se lo explica al dueño.
                rule_skipped_generic = True
    session.add(tx)
    # Solo escribe si cambió de verdad account_type/category/subcategory (una nota o
    # description_extra sueltos no son una corrección de categoría).
    record_change(session, tx, before, via=VIA_RULE_UPDATE if rule_updated else VIA_MANUAL,
                  user_id=user.id, reason=reason)
    session.commit()
    session.refresh(tx)
    # Devuelve la tx + cuántas OTRAS alineó la regla → el frontend lo muestra y re-pinta de verde.
    return {"id": tx.id, "applied": applied, "rule_updated": rule_updated,
            "rule_skipped_generic": rule_skipped_generic}


@app.post("/api/transactions/{transaction_id}/undo")
def undo_transaction_change(
    transaction_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """DESHACER (Ctrl+Z / botón): devuelve la transacción al estado ANTES de su último cambio de
    categorización, con su procedencia exacta (si la decidía una regla, vuelve a ser `ƒ`). Si alguien
    la cambió después, no se pisa: 409."""
    tx = owned_transaction(transaction_id, user, session, write=True)
    change = last_change(session, tx)
    if change is None:
        raise HTTPException(status_code=404, detail={"code": "nothing_to_undo"})
    if not undo_change(session, tx, change, user_id=user.id):
        raise HTTPException(status_code=409, detail={"code": "changed_since"})
    session.commit()
    session.refresh(tx)
    return {"id": tx.id, "category": tx.category, "subcategory": tx.subcategory,
            "account_type": tx.account_type.value if tx.account_type else None, "source": tx.source}


@app.post("/api/transactions/clarify-group")
def clarify_group(
    payload: ClarifyGroupPayload,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """El dueño explica UNA vez qué fueron varios movimientos de la misma contraparte (p. ej. la
    renta mensual). Su respuesta se guarda como NOTA de cada línea y la pregunta se cierra.

    🔴 Ya NO llama a la IA (decisión de Jose, 1-oct-2026: *"la IA no toca la categorización por
    NINGÚN camino"*). Antes `recategorize_with_answer` escribía la categoría con `source="ai"`.
    Ahora la categoría NO se toca: queda como estaba (casi siempre Pendiente) y el bookkeeper la
    resuelve en la Hoja — a mano o con una regla — leyendo la respuesta en la columna Nota."""
    if not payload.transaction_ids:
        raise HTTPException(status_code=422, detail="no transactions")
    answer = (payload.answer or "").strip()
    if not answer:
        raise HTTPException(status_code=422, detail="empty answer")
    txs = [owned_transaction(tid, user, session, write=True) for tid in payload.transaction_ids]
    for tx in txs:
        # Se AGREGA a la nota que ya hubiera (no se pisa lo que escribió el bookkeeper).
        tx.note = f"{tx.note}\n{answer}" if tx.note else answer
        tx.needs_review = False
        tx.review_question = None
        session.add(tx)
    session.commit()
    return {"updated": len(txs)}


# ─────────────────────────────────────────────────────────────────────────────────────────────
# FASE 13 — EL PANEL DE LA TRANSACCIÓN (área `Firm / bookkeepers`, 13-oct-2026). Lógica en
# `tx_insight.py`: solo JUNTA lo de `book_review` (hallazgos, ①, ②), las notas y el historial.
# Las dos son lectura de CONSOLA: el CLIENTE no ve los hallazgos ni el criterio del bookkeeper.
# ─────────────────────────────────────────────────────────────────────────────────────────────

@app.get("/api/companies/{company_id}/transactions/flags")
def transaction_flags(company_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """El puntito de cada fila de la hoja: {transaction_id: "problem" | "review" | "info"}."""
    import tx_insight
    company = console_company(company_id, user, session)
    return {str(k): v for k, v in tx_insight.flags(session, company).items()}


@app.get("/api/companies/{company_id}/transactions/{transaction_id}/insight")
def transaction_insight(company_id: int, transaction_id: int, lang: Optional[str] = None,
                        user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Todo lo que se sabe de UN movimiento: procedencia, historial, hallazgos, evidencia web y notas.
    `lang`: el idioma de la pantalla (igual que `/audit/board`), para la observación del ① guardada."""
    import tx_insight
    company = console_company(company_id, user, session)
    tx = tx_insight.transaction_of(session, company, transaction_id)
    if tx is None:
        raise HTTPException(status_code=404, detail="transaction not found")
    return tx_insight.insight(session, company, tx, lang=lang)


@app.get("/api/companies/{company_id}/transactions/{transaction_id}/rule-trace")
def transaction_rule_trace(company_id: int, transaction_id: int, user: User = Depends(current_user),
                           session: Session = Depends(get_session)):
    """POR QUÉ tiene su categoría (14-oct, Movimientos): las reglas que mencionan su descripción, en el orden
    real, y qué pasó con cada una. Solo lectura."""
    import tx_insight
    company = console_company(company_id, user, session)
    tx = tx_insight.transaction_of(session, company, transaction_id)
    if tx is None:
        raise HTTPException(status_code=404, detail="transaction not found")
    return rule_trace(load_rules(session, company.id, ""), tx)
