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
from models import Company, Transaction, User

AUTH_DEV_MODE = os.getenv("AUTH_DEV_MODE", "").lower() in {"1", "true", "yes"}

_fb_ready = False


def _verify_firebase_token(token: str) -> str:
    """Verifica un ID token de Firebase y devuelve el `uid`. Importa firebase-admin de forma
    perezosa para que el modo dev funcione sin tener la dependencia instalada."""
    global _fb_ready
    import firebase_admin
    from firebase_admin import auth as fb_auth

    if not _fb_ready:
        # Usa las credenciales por defecto del entorno (Cloud Run / GOOGLE_APPLICATION_CREDENTIALS).
        firebase_admin.initialize_app()
        _fb_ready = True
    return fb_auth.verify_id_token(token)["uid"]


def current_uid(
    authorization: str | None = Header(default=None),
    x_dev_uid: str | None = Header(default=None),
) -> str:
    """Identidad del que llama: el `uid` del token Firebase (o de `X-Dev-Uid` en modo dev)."""
    if AUTH_DEV_MODE:
        if not x_dev_uid:
            raise HTTPException(status_code=401, detail="missing X-Dev-Uid (dev mode)")
        return x_dev_uid

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        return _verify_firebase_token(token)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="invalid token")


def current_user(
    uid: str = Depends(current_uid),
    session: Session = Depends(get_session),
) -> User:
    """Devuelve el `User` del `uid`; lo crea en el primer login (get-or-create)."""
    user = session.exec(select(User).where(User.firebase_uid == uid)).first()
    if user is None:
        user = User(firebase_uid=uid)
        session.add(user)
        session.commit()
        session.refresh(user)
    return user


def owned_company(company_id: int, user: User, session: Session) -> Company:
    """Carga la empresa y valida que pertenezca al usuario. 404 si no existe, 403 si no es suya."""
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="company not found")
    if company.user_id != user.id:
        raise HTTPException(status_code=403, detail="forbidden")
    return company


def owned_transaction(transaction_id: int, user: User, session: Session) -> Transaction:
    """Carga una transacción y valida que su empresa pertenezca al usuario (403 si no)."""
    tx = session.get(Transaction, transaction_id)
    if tx is None:
        raise HTTPException(status_code=404, detail="transaction not found")
    owned_company(tx.company_id, user, session)  # valida pertenencia (403 si no es suya)
    return tx
