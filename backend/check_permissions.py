"""
FASE 13 Paso 3 — la matriz de permisos del rol CLIENT, comprobada contra TODAS las rutas.

**Por qué es un script y no una tabla en un documento:** una matriz escrita a mano queda vieja en la
primera ruta nueva. Esto recorre `app.routes` de verdad: el día que alguien agregue un endpoint sin
decidir el permiso del cliente, ESTO FALLA en vez de abrir un hueco en silencio.

Corre así (necesita `AUTH_DEV_MODE=true`, que activa la cabecera `X-Dev-Uid`):

    AUTH_DEV_MODE=true .venv/bin/python check_permissions.py

Crea sus propios datos de prueba (una firma, un libro, un bookkeeper y un cliente), prueba, y los
BORRA al final — no toca los libros reales.

Salida: un renglón por ruta, un resumen y **código de salida 1** si algo falló.
"""

import os
import sys

os.environ.setdefault("AUTH_DEV_MODE", "true")

from datetime import date, datetime, timezone  # noqa: E402
from decimal import Decimal  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, delete, select  # noqa: E402

import auth  # noqa: E402
from db import engine  # noqa: E402
from main import app  # noqa: E402
from models import (BookCriterion, CategoryRule, CellFormat, Company, CompanyCategory, CompanyMember,  # noqa: E402
                    Firm, FirmMember, FirmRole, LearnedMerchant, MonthlyInsight, PnlReport,
                    Role, SheetLabel, Transaction, TransactionLabel, User)

if not auth.AUTH_DEV_MODE:
    sys.exit("AUTH_DEV_MODE no está activo: corre con AUTH_DEV_MODE=true")

CLIENT_UID = "__permcheck_client__"
BOOK_UID = "__permcheck_bookkeeper__"

# ──────────────────────────────────────────────────────────────────────────────────────────────────
# LA LISTA BLANCA: lo único que un CLIENT puede alcanzar. Todo lo demás debe rechazarse.
# Claves = (método, plantilla de ruta) tal como FastAPI las registra.
# ──────────────────────────────────────────────────────────────────────────────────────────────────
CLIENT_ALLOWED: set[tuple[str, str]] = {
    # Decisión de Jose (30-sep-2026): el dueño NO ve el libro en construcción — ni el P&L, ni el
    # pivote, ni los movimientos categorizados, ni los reportes. Todo eso cambia el año entero; lo
    # ve cuando el bookkeeper se lo ENVÍA (Solicitud). Lo que queda es lo que el dueño APORTA.
    ("GET", "/api/me"),                                            # quién soy y cuál es mi libro
    ("GET", "/api/companies/{company_id}"),                        # nombre de mi negocio
    ("POST", "/api/companies/{company_id}/transactions/upload"),   # "subir un extracto"
    ("POST", "/api/companies/{company_id}/transactions/receipt"),  # "foto a un recibo"
    ("GET", "/api/hello"),                                         # público, sin identidad
}

# Rutas que NO se prueban porque no pertenecen al modelo de acceso por libro.
SKIP: set[tuple[str, str]] = {
    ("POST", "/api/stripe/webhook"),   # lo llama Stripe con su firma, no un usuario
    ("POST", "/api/companies"),        # crea un libro NUEVO: no hay `company_id` que validar (Paso 4)
}

# Cuerpos mínimos VÁLIDOS para las rutas que mutan el libro. Sin esto FastAPI responde 422
# (valida el cuerpo antes de correr la función) y la prueba no llegaría a comprobar el permiso.
# Son justo las rutas peligrosas: las que reescriben la contabilidad.
BODIES: dict[tuple[str, str], dict] = {
    ("PATCH", "/api/transactions/{transaction_id}"): {"category": "Materials"},
    ("PATCH", "/api/companies/{company_id}"): {"name": "hackeada"},
    ("PATCH", "/api/companies/{company_id}/thresholds"): {"supermarket_threshold": 1},
    ("PATCH", "/api/companies/{company_id}/profile"): {"payroll": "yes"},
    ("POST", "/api/companies/{company_id}/rules"): {
        "match_text": "X", "account_type": "OPERATING_EXPENSE", "category": "Materials"},
    ("PATCH", "/api/companies/{company_id}/rules/{rule_id}"): {"category": "Materials"},
    ("POST", "/api/companies/{company_id}/custom-categories"): {
        "account_type": "OPERATING_EXPENSE", "name": "Inventada"},
    ("POST", "/api/companies/{company_id}/labels"): {"name": "mía", "color": "#ff0000"},
    ("PUT", "/api/transactions/{transaction_id}/labels"): {"label_ids": []},
    ("POST", "/api/companies/{company_id}/cell-formats"): {
        "transaction_ids": [], "col": None, "bold": True},
    ("POST", "/api/companies/{company_id}/members"): {"email": "x@y.com", "role": "MEMBER"},
    ("PATCH", "/api/companies/{company_id}/members/{member_id}"): {"role": "MEMBER"},
    ("POST", "/api/companies/{company_id}/pnl-reports"): {"name": "mío"},
    ("POST", "/api/companies/{company_id}/transactions/set-account"): {
        "transaction_ids": [], "bank_account": "X"},
    ("POST", "/api/transactions/{transaction_id}/clarify"): {"answer": "un trabajador"},
    ("POST", "/api/transactions/clarify-group"): {"transaction_ids": [], "answer": "x"},
    ("POST", "/api/companies/{company_id}/checkout"): {"quota": 100},
    ("PATCH", "/api/firm"): {"brand_name": "PERMCHECK"},
    ("PATCH", "/api/companies/{company_id}/settlement"): {"wait_days": 5},
    ("POST", "/api/companies/{company_id}/checkout/confirm"): {"session_id": "cs_x"},
    ("POST", "/api/companies/{company_id}/transactions/set-account"): {
        "transaction_ids": ["__TX__"], "account": "Chase Checking"},
    ("POST", "/api/companies/{company_id}/interview/answers"): {"answers": []},
    ("POST", "/api/companies/{company_id}/rule-proposals/validate"): {"keyword": "X", "covers": []},
    ("POST", "/api/companies/{company_id}/rule-proposals/apply"): {"rules": []},
    # "__TX__" se reemplaza por el id de la transacción REAL de prueba: con una lista vacía el
    # endpoint corta con 422 ("no transactions") ANTES de mirar el permiso, y no probaría nada.
    ("POST", "/api/transactions/clarify-group"): {"transaction_ids": ["__TX__"], "answer": "x"},
}

# Parámetros OBLIGATORIOS en la URL (query). Sin ellos FastAPI responde 422 antes del permiso.
QUERY: dict[tuple[str, str], dict] = {
    ("GET", "/api/companies/{company_id}/pivot/leaves"): {
        "section": "COGS", "category": "Materials", "subcategory": "Home Depot", "month": "2026-06"},
}

# Rutas que reciben un ARCHIVO (multipart), no JSON. Se manda un archivo mínimo para que la
# validación del cuerpo pase y se llegue de verdad a comprobar el permiso.
FILES: dict[tuple[str, str], dict] = {
    ("POST", "/api/companies/{company_id}/rules/import"):
        {"file": ("reglas.csv", b"match,category\nX,Materials\n", "text/csv")},
    ("POST", "/api/companies/{company_id}/transactions/upload"):
        {"file": ("extracto.csv", b"date,description,amount\n2026-06-01,X,10\n", "text/csv")},
    ("POST", "/api/companies/{company_id}/transactions/receipt"):
        {"file": ("recibo.jpg", b"\xff\xd8\xff\xdb-no-es-una-foto-real", "image/jpeg")},
}


def _fill(body: dict, tx_id: int) -> dict:
    """Reemplaza el centinela "__TX__" por el id real de la transacción de prueba."""
    return {k: ([tx_id if x == "__TX__" else x for x in v] if isinstance(v, list) else v)
            for k, v in body.items()}


def build_fixtures(s: Session) -> tuple[int, int]:
    """Crea firma + libro + bookkeeper + cliente + 1 transacción. Devuelve (company_id, tx_id)."""
    bk = User(firebase_uid=BOOK_UID, email="permcheck-bk@test.local")
    cl = User(firebase_uid=CLIENT_UID, email="permcheck-client@test.local")
    s.add(bk); s.add(cl); s.commit(); s.refresh(bk); s.refresh(cl)

    firm = Firm(name="PERMCHECK FIRM", owner_user_id=bk.id, brand_name="PERMCHECK")
    s.add(firm); s.commit(); s.refresh(firm)
    s.add(FirmMember(firm_id=firm.id, user_id=bk.id, email=bk.email, role=FirmRole.OWNER))

    co = Company(name="PERMCHECK BOOK", sector="construction", firm_id=firm.id, user_id=bk.id)
    s.add(co); s.commit(); s.refresh(co)

    # El cliente entra por INVITACIÓN al libro: es el único camino al rol CLIENT.
    s.add(CompanyMember(company_id=co.id, user_id=cl.id, email=cl.email, role=Role.CLIENT))
    tx = Transaction(company_id=co.id, date=date(2026, 6, 1), description="HOME DEPOT",
                     amount=Decimal("100.00"), is_inflow=False, category="Materials")
    s.add(tx); s.commit(); s.refresh(tx)
    return co.id, tx.id


# Todo lo que cuelga de una empresa, en orden de dependencias. Se borra ENTERO porque la prueba
# llama endpoints de escritura: si alguno se escapa, deja filas (así se descubrió la fuga de
# `POST /labels`, que creó una `SheetLabel` y reventó el DELETE de la empresa).
_COMPANY_CHILDREN = (BookCriterion, CategoryRule, CompanyCategory, CompanyMember, LearnedMerchant,
                     MonthlyInsight, PnlReport, SheetLabel)


def drop_fixtures(s: Session) -> None:
    """Borra TODO lo creado, en orden de dependencias. Si esto no corre, la base queda sucia."""
    users = s.exec(select(User).where(User.firebase_uid.in_([BOOK_UID, CLIENT_UID]))).all()
    uids = [u.id for u in users]
    firms = s.exec(select(Firm).where(Firm.name == "PERMCHECK FIRM")).all()
    fids = [f.id for f in firms]
    # Por FIRMA y por DUEÑO, nunca por nombre: la prueba llama `PATCH /companies` con
    # {"name": "hackeada"} y el bookkeeper SÍ puede renombrar, así que el libro cambia de nombre a
    # media corrida. Buscarlo por "PERMCHECK BOOK" dejaba una empresa colgando de la firma.
    cids: list[int] = []
    if fids:
        cids += s.exec(select(Company.id).where(Company.firm_id.in_(fids))).all()
    if uids:
        cids += s.exec(select(Company.id).where(Company.user_id.in_(uids))).all()
    cids = list(set(cids))

    if cids:
        txs = s.exec(select(Transaction.id).where(Transaction.company_id.in_(cids))).all()
        if txs:
            s.exec(delete(CellFormat).where(CellFormat.transaction_id.in_(txs)))
            s.exec(delete(TransactionLabel).where(TransactionLabel.transaction_id.in_(txs)))
        for model in _COMPANY_CHILDREN:
            s.exec(delete(model).where(model.company_id.in_(cids)))
        s.exec(delete(Transaction).where(Transaction.company_id.in_(cids)))
    if fids:
        s.exec(delete(FirmMember).where(FirmMember.firm_id.in_(fids)))
    if cids:
        s.exec(delete(Company).where(Company.id.in_(cids)))
    if fids:
        s.exec(delete(Firm).where(Firm.id.in_(fids)))
    if uids:
        s.exec(delete(CompanyMember).where(CompanyMember.user_id.in_(uids)))
        s.exec(delete(User).where(User.id.in_(uids)))
    s.commit()


def main() -> int:
    with Session(engine) as s:
        drop_fixtures(s)              # por si una corrida anterior murió a medias
        company_id, tx_id = build_fixtures(s)

    client = TestClient(app, raise_server_exceptions=False)
    headers = {"X-Dev-Uid": CLIENT_UID, "X-Dev-Email": "permcheck-client@test.local"}

    # `999999` en cualquier otro id: el recurso NO es del cliente, así que un 404 también es un
    # rechazo legítimo. Lo que NUNCA puede pasar es un 2xx.
    subs = {"company_id": str(company_id), "transaction_id": str(tx_id)}

    leaks: list[str] = []
    unproven: list[str] = []
    checked = 0

    try:
        for route in app.routes:
            tmpl = getattr(route, "path", None)
            methods = getattr(route, "methods", None) or set()
            if not tmpl or not tmpl.startswith("/api"):
                continue
            for method in sorted(methods - {"HEAD", "OPTIONS"}):
                key = (method, tmpl)
                if key in SKIP:
                    print(f"  ⏭️  {method:6} {tmpl}  (no aplica)")
                    continue

                url = tmpl
                for name in ("company_id", "transaction_id"):
                    url = url.replace("{" + name + "}", subs[name])
                # Cualquier otro id: uno que no existe.
                while "{" in url:
                    start = url.index("{"); end = url.index("}") + 1
                    url = url[:start] + "999999" + url[end:]

                kwargs = {"headers": headers}
                if key in BODIES:
                    kwargs["json"] = _fill(BODIES[key], tx_id)
                elif key in FILES:
                    kwargs["files"] = FILES[key]
                if key in QUERY:
                    kwargs["params"] = QUERY[key]
                r = client.request(method, url, **kwargs)
                checked += 1
                allowed = key in CLIENT_ALLOWED
                ok2xx = 200 <= r.status_code < 300

                if allowed:
                    if r.status_code == 403:
                        leaks.append(f"BLOQUEADA pero debería pasar: {method} {tmpl} → 403")
                        print(f"  ❌ {method:6} {tmpl}  → 403 (¡debería pasar!)")
                    else:
                        print(f"  ✅ {method:6} {tmpl}  → {r.status_code} (permitida)")
                elif ok2xx:
                    leaks.append(f"🔴 FUGA: {method} {tmpl} → {r.status_code} (el cliente pudo)")
                    print(f"  🔴 {method:6} {tmpl}  → {r.status_code}  ¡FUGA!")
                elif r.status_code == 403:
                    print(f"  ✅ {method:6} {tmpl}  → 403 (rechazada)")
                elif r.status_code == 422:
                    unproven.append(f"{method} {tmpl}")
                    print(f"  ⚠️  {method:6} {tmpl}  → 422 (el cuerpo se validó ANTES del permiso: "
                          f"agrega su cuerpo a BODIES para probarlo de verdad)")
                else:
                    print(f"  ✅ {method:6} {tmpl}  → {r.status_code} (rechazada)")
    finally:
        with Session(engine) as s:
            drop_fixtures(s)

    # ──────────────────────────────────────────────────────────────────────────────────────────
    # SEGUNDA DIRECCIÓN: que el BOOKKEEPER no haya quedado bloqueado.
    # Una prueba que solo comprueba rechazos pasaría igual si se bloqueara a TODO el mundo. Aquí se
    # exige lo contrario: el dueño de la oficina no puede recibir 403 en ninguna ruta de su libro.
    # ──────────────────────────────────────────────────────────────────────────────────────────
    print("\n  --- el bookkeeper de la oficina NO debe recibir 403 ---")
    bk_headers = {"X-Dev-Uid": BOOK_UID, "X-Dev-Email": "permcheck-bk@test.local"}
    with Session(engine) as s:
        company_id, tx_id = build_fixtures(s)
    subs["company_id"] = str(company_id)
    subs["transaction_id"] = str(tx_id)
    blocked: list[str] = []
    try:
        for route in app.routes:
            tmpl = getattr(route, "path", None)
            methods = getattr(route, "methods", None) or set()
            if not tmpl or not tmpl.startswith("/api"):
                continue
            for method in sorted(methods - {"HEAD", "OPTIONS"}):
                key = (method, tmpl)
                if key in SKIP:
                    continue
                url = tmpl
                for name in ("company_id", "transaction_id"):
                    url = url.replace("{" + name + "}", subs[name])
                while "{" in url:
                    a = url.index("{"); b = url.index("}") + 1
                    url = url[:a] + "999999" + url[b:]
                kwargs = {"headers": bk_headers}
                if key in BODIES:
                    kwargs["json"] = _fill(BODIES[key], tx_id)
                elif key in FILES:
                    kwargs["files"] = FILES[key]
                if key in QUERY:
                    kwargs["params"] = QUERY[key]
                r = client.request(method, url, **kwargs)
                if r.status_code == 403:
                    blocked.append(f"{method} {tmpl} → 403: {r.json().get('detail')}")
                    print(f"  ❌ {method:6} {tmpl}  → 403 (¡el bookkeeper quedó bloqueado!)")
        if not blocked:
            print("  ✅ El bookkeeper pasa en todas: ningún 403.")
    finally:
        with Session(engine) as s:
            drop_fixtures(s)
    leaks.extend(blocked)

    print(f"\n  Rutas comprobadas: {checked}  ·  permitidas al cliente: {len(CLIENT_ALLOWED)}")
    if unproven:
        print(f"  ⚠️  Sin probar ({len(unproven)}): {', '.join(unproven)}")
    if leaks:
        print("\n  🔴 FALLÓ:")
        for x in leaks:
            print("   -", x)
        return 1
    print("  ✅ Ninguna fuga. El cliente no alcanza nada fuera de su lista blanca.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
