"""
ContaBu — capa de PAGOS (Stripe Checkout, modelo M5 por volumen de transacciones).

- `create_checkout_session`: crea la sesión de Checkout (Stripe aloja la página de pago) para un tier.
- `apply_session`: tras el pago, lee la sesión y deja la empresa como suscrita (status + cupo).
- `handle_event`: procesa los webhooks de Stripe (renovación/cancelación) → actualiza la empresa.

Reglas de oro: NO tocamos datos de tarjeta (los maneja Stripe). El estado real de la suscripción vive
en Stripe; ContaBu solo refleja `stripe_customer_id` / `stripe_subscription_id` / `subscription_status`
/ `tx_quota` en `Company`. Todo configurable por entorno (claves de test/live sin tocar código).
"""
import logging
import os
from datetime import datetime, timezone

import stripe
from sqlalchemy import func
from sqlmodel import Session, select

from models import Company, Transaction

# Transacciones con IA gratis (prueba, DE POR VIDA) para una empresa sin suscripción (modelo M5).
FREE_TRIAL_AI = 100

logger = logging.getLogger("contabu.billing")

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
_FRONTEND = os.getenv("FRONTEND_BASE_URL", "http://localhost:5173")

# tier (cupo mensual de tx) → Stripe price_id. El cupo ES la clave (M5 cobra por volumen).
PRICE_BY_QUOTA: dict[int, str] = {
    100: os.getenv("STRIPE_PRICE_100", ""),
    200: os.getenv("STRIPE_PRICE_200", ""),
    400: os.getenv("STRIPE_PRICE_400", ""),
    1000: os.getenv("STRIPE_PRICE_1000", ""),
}
# price_id → cupo (para resolver el cupo desde lo que se compró, en webhooks).
QUOTA_BY_PRICE: dict[str, int] = {pid: q for q, pid in PRICE_BY_QUOTA.items() if pid}


def ai_quota_status(session: Session, company: Company) -> dict:
    """Cupo de categorizaciones con IA. Suscrito: **mensual** (`tx_quota`/mes). Sin suscripción:
    **prueba de por vida** (`FREE_TRIAL_AI`). Cuenta solo transacciones `ai_categorized=True`
    (la entrada manual es gratis e ilimitada). Devuelve used/limit/remaining/monthly."""
    active = company.subscription_status == "active" and bool(company.tx_quota)
    q = select(func.count()).select_from(Transaction).where(
        Transaction.company_id == company.id, Transaction.ai_categorized == True  # noqa: E712
    )
    if active:
        start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        used = session.exec(q.where(Transaction.created_at >= start)).one()
        limit = company.tx_quota or 0
    else:
        used = session.exec(q).one()  # de por vida
        limit = FREE_TRIAL_AI
    return {"used": used, "limit": limit, "remaining": max(0, limit - used), "monthly": active}


def create_checkout_session(session: Session, company: Company, quota: int, email: str | None) -> str:
    """Crea una Checkout Session (modo suscripción) para el `quota` (tier) elegido y devuelve la URL
    a la que redirigir al usuario. Reusa/crea el customer de Stripe de la empresa."""
    price_id = PRICE_BY_QUOTA.get(quota)
    if not price_id:
        raise ValueError(f"tier inválido: {quota}")

    customer_id = company.stripe_customer_id
    if not customer_id:
        customer = stripe.Customer.create(email=email or None, metadata={"company_id": company.id})
        customer_id = customer.id
        company.stripe_customer_id = customer_id
        session.add(company)
        session.commit()

    checkout = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=f"{_FRONTEND}/?billing=success&session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{_FRONTEND}/?billing=cancel",
        metadata={"company_id": company.id, "quota": quota},
    )
    logger.info("checkout | company=%s quota=%s -> session=%s", company.id, quota, checkout.id)
    return checkout.url


def _set_subscription(company: Company, sub_id: str | None, status: str | None, quota: int | None) -> None:
    company.stripe_subscription_id = sub_id
    company.subscription_status = status
    company.tx_quota = quota


def apply_session(session: Session, session_id: str) -> dict:
    """Tras el redirect de éxito: lee la Checkout Session de Stripe y, si está pagada, deja la empresa
    suscrita (status + cupo). Idempotente. Esto permite probar e2e sin webhooks; el webhook cubre
    renovaciones/cancelaciones después."""
    cs = stripe.checkout.Session.retrieve(session_id, expand=["subscription"])
    company_id = (cs.metadata or {}).get("company_id")
    quota = int((cs.metadata or {}).get("quota") or 0) or None
    if not company_id:
        return {"ok": False, "reason": "sin company_id"}
    company = session.get(Company, int(company_id))
    if not company:
        return {"ok": False, "reason": "empresa no encontrada"}
    paid = cs.payment_status == "paid" or cs.status == "complete"
    if paid:
        sub = cs.subscription
        sub_id = sub.id if hasattr(sub, "id") else sub
        _set_subscription(company, sub_id, "active", quota)
        if cs.customer:
            company.stripe_customer_id = cs.customer if isinstance(cs.customer, str) else cs.customer.id
        session.add(company)
        session.commit()
    return {"ok": paid, "status": company.subscription_status, "tx_quota": company.tx_quota}


def handle_event(session: Session, payload: bytes, sig_header: str | None) -> dict:
    """Procesa un webhook de Stripe (verifica la firma) y refleja el cambio en la empresa:
    suscripción activa/al día → cupo; cancelada/impaga → sin cupo."""
    import json

    if _WEBHOOK_SECRET:
        # Solo VERIFICA la firma; se descarta el objeto tipado que devuelve (en stripe v15
        # `data.object` es un Subscription/Session tipado, NO un dict — sin `.get()`).
        stripe.Webhook.construct_event(payload, sig_header, _WEBHOOK_SECRET)
    # Trabajar SIEMPRE sobre el JSON plano (dicts): mismo camino en dev y prod.
    event = json.loads(payload)

    etype = event["type"]
    obj = event["data"]["object"]

    if etype == "checkout.session.completed":
        cid = (obj.get("metadata") or {}).get("company_id")
        quota = int((obj.get("metadata") or {}).get("quota") or 0) or None
        company = session.get(Company, int(cid)) if cid else None
        if company:
            _set_subscription(company, obj.get("subscription"), "active", quota)
            session.add(company); session.commit()

    elif etype in ("customer.subscription.updated", "customer.subscription.deleted"):
        sub_id = obj.get("id")
        company = session.exec(
            select(Company).where(Company.stripe_subscription_id == sub_id)
        ).first()
        if company:
            status = obj.get("status")  # active · past_due · canceled · unpaid …
            if etype == "customer.subscription.deleted" or status in ("canceled", "unpaid"):
                _set_subscription(company, None, "canceled", None)  # vuelve a free
            else:
                price = ((obj.get("items") or {}).get("data") or [{}])[0].get("price", {}).get("id")
                company.subscription_status = status
                if price in QUOTA_BY_PRICE:
                    company.tx_quota = QUOTA_BY_PRICE[price]
            session.add(company); session.commit()

    logger.info("webhook | %s", etype)
    return {"received": True, "type": etype}
