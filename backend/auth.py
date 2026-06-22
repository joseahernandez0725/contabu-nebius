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
from models import Company, CompanyMember, Role, Transaction, User

AUTH_DEV_MODE = os.getenv("AUTH_DEV_MODE", "").lower() in {"1", "true", "yes"}

_fb_ready = False


def _verify_firebase_token(token: str) -> dict:
    """Verifica un ID token de Firebase y devuelve el claims decodificado (uid, email…). Importa
    firebase-admin de forma perezosa para que el modo dev funcione sin tener la dependencia instalada."""
    global _fb_ready
    import firebase_admin
    from firebase_admin import auth as fb_auth

    if not _fb_ready:
        # Usa las credenciales por defecto del entorno (Cloud Run / GOOGLE_APPLICATION_CREDENTIALS).
        firebase_admin.initialize_app()
        _fb_ready = True
    return fb_auth.verify_id_token(token)


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
        return {"uid": decoded["uid"], "email": decoded.get("email")}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="invalid token")


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
    if user.email:
        pending = session.exec(
            select(CompanyMember)
            .where(CompanyMember.email == user.email)
            .where(CompanyMember.user_id == None)  # noqa: E711
        ).all()
        if pending:
            for m in pending:
                m.user_id = user.id
                session.add(m)
            session.commit()
    return user


def member_role(company: Company, user: User, session: Session) -> Role | None:
    """Rol del usuario sobre la empresa: OWNER si es el dueño (`Company.user_id`), o el rol de su
    `CompanyMember`; `None` si no tiene acceso."""
    if company.user_id == user.id:
        return Role.OWNER
    m = session.exec(
        select(CompanyMember)
        .where(CompanyMember.company_id == company.id)
        .where(CompanyMember.user_id == user.id)
    ).first()
    return m.role if m else None


def owned_company(company_id: int, user: User, session: Session, *, write: bool = False) -> Company:
    """Carga la empresa y valida ACCESO por rol. 404 si no existe; 403 si el usuario no es miembro;
    403 si `write=True` y el rol es VIEWER (solo lectura)."""
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="company not found")
    role = member_role(company, user, session)
    if role is None:
        raise HTTPException(status_code=403, detail="forbidden")
    if write and role == Role.VIEWER:
        raise HTTPException(status_code=403, detail="read-only (viewer)")
    return company


def owner_company(company_id: int, user: User, session: Session) -> Company:
    """Como `owned_company` pero EXIGE rol OWNER (gestionar equipo, renombrar, eliminar)."""
    company = owned_company(company_id, user, session)
    if member_role(company, user, session) != Role.OWNER:
        raise HTTPException(status_code=403, detail="owner only")
    return company


def owned_transaction(transaction_id: int, user: User, session: Session, *, write: bool = False) -> Transaction:
    """Carga una transacción y valida acceso por rol a su empresa (403 si no; read-only si VIEWER)."""
    tx = session.get(Transaction, transaction_id)
    if tx is None:
        raise HTTPException(status_code=404, detail="transaction not found")
    owned_company(tx.company_id, user, session, write=write)
    return tx
