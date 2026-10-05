"""
ContaBu — autenticación (Firebase Auth) y resolución del usuario (multi-tenant).

Flujo en producción:
  frontend hace login con Firebase → manda el ID token en `Authorization: Bearer <token>`
  → aquí se VERIFICA con `firebase-admin` y se obtiene el `uid` → se busca/crea el `User`.

Los endpoints NUNCA confían en el `company_id` de la URL para saber quién es el usuario:
la identidad sale SIEMPRE del token verificado (deuda técnica #2 🔴).

Costura de desarrollo (`AUTH_DEV_MODE=true`): en local, sin proyecto Firebase, se acepta el
`uid` directamente desde la cabecera `X-Dev-Uid`. Permite probar el aislamiento multi-tenant
(401 sin identidad, 403 entre usuarios) sin montar Firebase. NUNCA debe activarse en producción.
"""

import os

from fastapi import Depends, Header, HTTPException
from sqlmodel import Session, select

from db import get_session
from models import Company, CompanyMember, Firm, FirmMember, FirmRole, Role, Transaction, User

AUTH_DEV_MODE = os.getenv("AUTH_DEV_MODE", "").lower() in {"1", "true", "yes"}

_fb_ready = False


def firebase_auth():
    """El módulo `firebase_admin.auth`, con la app inicializada una sola vez. Import perezoso: el modo dev
    funciona sin la dependencia. Lo usan la verificación de tokens y la limpieza del demo (`demo.py`)."""
    global _fb_ready
    import firebase_admin
    from firebase_admin import auth as fb_auth

    if not _fb_ready:
        # Usa las credenciales por defecto del entorno (Cloud Run / GOOGLE_APPLICATION_CREDENTIALS).
        firebase_admin.initialize_app()
        _fb_ready = True
    return fb_auth


def _verify_firebase_token(token: str) -> dict:
    """Verifica un ID token de Firebase y devuelve el claims decodificado (uid, email…)."""
    return firebase_auth().verify_id_token(token)


def is_anonymous_token(decoded: dict) -> bool:
    """¿El token es de una sesión ANÓNIMA de Firebase? El claim, verificado en la doc oficial (4-oct,
    "Security Rules and Firebase Authentication"): `firebase.sign_in_provider`, que puede valer
    custom · password · phone · **anonymous** · google.com · facebook.com · github.com · twitter.com."""
    return (decoded.get("firebase") or {}).get("sign_in_provider") == "anonymous"


def current_identity(
    authorization: str | None = Header(default=None),
    x_dev_uid: str | None = Header(default=None),
    x_dev_email: str | None = Header(default=None),
) -> dict:
    """Identidad del que llama: `{uid, email}` del token Firebase (o de `X-Dev-Uid`/`X-Dev-Email` en
    modo dev). El email habilita los roles por empresa (enlazar invitaciones por email)."""
    if AUTH_DEV_MODE:
        if not x_dev_uid:
            raise HTTPException(status_code=401, detail="missing X-Dev-Uid (dev mode)")
        return {"uid": x_dev_uid, "email": x_dev_email}

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        decoded = _verify_firebase_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="invalid token")
    # DEUDA #17 (4-oct): demo y producción comparten el proyecto de Firebase (`contabu-prod`) y el login
    # ANÓNIMO está activo para el demo. Cualquiera saca un token anónimo con la API key pública del
    # frontend; sin este filtro, entraría al backend de PRODUCCIÓN y le crearía una oficina en la base
    # real que nunca se borra. Solo el servicio de demo (`DEMO_MODE`) los acepta.
    if is_anonymous_token(decoded) and not _demo_mode():
        raise HTTPException(status_code=401, detail="anonymous sessions are only allowed in the demo")
    return {"uid": decoded["uid"], "email": decoded.get("email")}


def _demo_mode() -> bool:
    """`demo.DEMO_MODE`, leído en el momento (import perezoso: `demo` importa `models`, no `auth`)."""
    import demo
    return demo.DEMO_MODE


def current_user(
    identity: dict = Depends(current_identity),
    session: Session = Depends(get_session),
) -> User:
    """Devuelve el `User` del `uid`; lo crea en el primer login (get-or-create). Guarda el email y
    **enlaza las invitaciones pendientes** (CompanyMember por email sin `user_id`) → roles por empresa."""
    uid = identity["uid"]
    email = (identity.get("email") or "").strip().lower() or None
    user = session.exec(select(User).where(User.firebase_uid == uid)).first()
    if user is None:
        user = User(firebase_uid=uid, email=email)
        session.add(user)
        session.commit()
        session.refresh(user)
    elif email and user.email != email:
        user.email = email
        session.add(user)
        session.commit()

    # Enlazar invitaciones pendientes (mismo email, aún sin user_id) → el usuario gana acceso.
    # Son DOS niveles (FASE 13): al LIBRO (`CompanyMember`, incluye el rol CLIENT) y a la OFICINA
    # (`FirmMember`, que da acceso a TODOS sus libros de una sola vez).
    if user.email:
        linked = False
        for model in (CompanyMember, FirmMember):
            pending = session.exec(
                select(model)
                .where(model.email == user.email)
                .where(model.user_id == None)  # noqa: E711
            ).all()
            for m in pending:
                m.user_id = user.id
                session.add(m)
                linked = True
        if linked:
            session.commit()
    return user


# Qué rol de OFICINA equivale a qué rol sobre un libro. Un bookkeeper de la firma trabaja todos sus
# libros (= MEMBER); el dueño de la firma manda (= OWNER); un VIEWER de la firma solo lee.
_FIRM_TO_COMPANY_ROLE: dict[FirmRole, Role] = {
    FirmRole.OWNER: Role.OWNER,
    FirmRole.BOOKKEEPER: Role.MEMBER,
    FirmRole.VIEWER: Role.VIEWER,
}

# De menos a más permisivo. Sirve para quedarse con el rol MÁS ALTO cuando un usuario llega por dos
# caminos a la vez (p. ej. VIEWER de la oficina pero MEMBER invitado en un libro concreto).
_ROLE_RANK: dict[Role, int] = {Role.CLIENT: 0, Role.VIEWER: 1, Role.MEMBER: 2, Role.OWNER: 3}

# Quiénes son "el operador" del libro: los que pueden tocar el CRITERIO CONTABLE (reglas, plan de
# cuentas, categorías, la hoja). Ni VIEWER (solo lee) ni CLIENT (aporta datos, no decide).
BOOKKEEPER_ROLES: frozenset[Role] = frozenset({Role.OWNER, Role.MEMBER})


def effective_role(company: Company, user: User, session: Session) -> Role | None:
    """Rol del usuario sobre el libro. `None` = sin acceso.

    Hay TRES caminos de acceso y se toma el MÁS PERMISIVO de los que apliquen:
      1. **creador** del libro (`Company.user_id`) → OWNER
      2. **invitado al libro** (`CompanyMember`) → su rol, que incluye CLIENT
      3. **miembro de la oficina** (`FirmMember` de `company.firm_id`) → el equivalente de su
         `FirmRole`; es lo que hace que se invite UNA vez y se vean los 40 libros.

    Se toma el más permisivo (y no "la firma manda") para que un rol dado a mano en un libro concreto
    nunca se PIERDA: a un socio VIEWER de la oficina se le puede dar MEMBER en un libro suelto.

    Es la ÚNICA fuente de verdad del acceso: un endpoint que no pase por aquí no está protegido.
    """
    roles: list[Role] = []

    if company.user_id == user.id:
        roles.append(Role.OWNER)

    m = session.exec(
        select(CompanyMember)
        .where(CompanyMember.company_id == company.id)
        .where(CompanyMember.user_id == user.id)
    ).first()
    if m:
        roles.append(m.role)

    if company.firm_id is not None:
        fm = session.exec(
            select(FirmMember)
            .where(FirmMember.firm_id == company.firm_id)
            .where(FirmMember.user_id == user.id)
        ).first()
        if fm:
            roles.append(_FIRM_TO_COMPANY_ROLE[fm.role])

    if not roles:
        return None
    return max(roles, key=lambda r: _ROLE_RANK[r])


def owned_company(company_id: int, user: User, session: Session, *, write: bool = False) -> Company:
    """Carga la empresa y valida ACCESO por rol. 404 si no existe; 403 si el usuario no tiene acceso.

    `write=True` exige ser **operador** del libro (OWNER/MEMBER): rechaza VIEWER *y* CLIENT.

    ⚠️ Que CLIENT caiga aquí es a propósito y es lo que hace este cambio seguro: al aparecer el rol
    nuevo, las escrituras de la consola quedan **cerradas por defecto** (fail closed). Lo que el
    cliente SÍ debe poder hacer (hoy: subir su extracto) se abre una ruta a la vez con
    `client_writable_company()`, nunca al revés. (La foto de recibo se eliminó el 3-oct.)
    """
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="company not found")
    role = effective_role(company, user, session)
    if role is None:
        raise HTTPException(status_code=403, detail="forbidden")
    if write and role not in BOOKKEEPER_ROLES:
        raise HTTPException(status_code=403, detail="read-only (viewer)" if role == Role.VIEWER
                            else "not allowed for client accounts")
    return company


# NOTA de diseño: NO hay un `readable_company()` ni un `bookkeeper_company()` aparte.
#   - leer          → `owned_company(...)`                 (pasa cualquier rol con acceso)
#   - consola       → `owned_company(..., write=True)`     (OWNER/MEMBER: ya rechaza VIEWER y CLIENT)
#   - lo del cliente→ `client_writable_company(...)`       (la lista blanca, abajo)
#   - dueño         → `owner_company(...)`
# Serían alias de una línea sobre lo de arriba, y renombrar los 42 call sites de `owned_company` en
# `main.py` —archivo que otras áreas están editando en paralelo— es pedir un conflicto por nada.


def console_company(company_id: int, user: User, session: Session) -> Company:
    """LECTURA de datos de CONSOLA: reglas, plan de cuentas, equipo, marcas, cupo, auditoría.

    Pasan OWNER · MEMBER · VIEWER. **CLIENT no**: es lectura, sí, pero del *criterio* del bookkeeper
    —su reglamento, su plan de cuentas, a quién más le dio acceso, cuánto le queda de cupo—, no de
    los números del cliente. Esas son las que `owned_company()` deja pasar y este cierra.
    """
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="company not found")
    role = effective_role(company, user, session)
    if role is None:
        raise HTTPException(status_code=403, detail="forbidden")
    if role == Role.CLIENT:
        raise HTTPException(status_code=403, detail="not allowed for client accounts")
    return company


def client_writable_company(company_id: int, user: User, session: Session) -> Company:
    """Lo que el CLIENTE aporta (hoy: subir su extracto; mañana, responder una pregunta).

    Pasan OWNER · MEMBER · CLIENT. **VIEWER no**: es de solo lectura y eso no cambia.
    Úsalo SOLO en los endpoints de la lista blanca del cliente (ver docs/FASE 13.md Paso 3).
    """
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="company not found")
    role = effective_role(company, user, session)
    if role is None:
        raise HTTPException(status_code=403, detail="forbidden")
    if role == Role.VIEWER:
        raise HTTPException(status_code=403, detail="read-only (viewer)")
    return company


def owner_company(company_id: int, user: User, session: Session) -> Company:
    """Como `owned_company` pero EXIGE rol OWNER (gestionar equipo, renombrar, eliminar)."""
    company = owned_company(company_id, user, session)
    if effective_role(company, user, session) != Role.OWNER:
        raise HTTPException(status_code=403, detail="owner only")
    return company


def user_firm(user: User, session: Session) -> tuple[Firm | None, FirmRole | None]:
    """La OFICINA del usuario y su rol en ella — `(None, None)` si no está en ninguna.

    Es lo que distingue a un bookkeeper de un cliente: el cliente **no pertenece a ninguna firma**, y
    de eso depende qué cáscara le muestra el frontend (consola vs plataforma del cliente).
    """
    fm = session.exec(select(FirmMember).where(FirmMember.user_id == user.id)).first()
    if fm is None:
        return None, None
    return session.get(Firm, fm.firm_id), fm.role


def owned_transaction(transaction_id: int, user: User, session: Session, *, write: bool = False) -> Transaction:
    """Carga una transacción y valida acceso por rol a su empresa (403 si no; read-only si VIEWER)."""
    tx = session.get(Transaction, transaction_id)
    if tx is None:
        raise HTTPException(status_code=404, detail="transaction not found")
    owned_company(tx.company_id, user, session, write=write)
    return tx
