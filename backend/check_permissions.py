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

# ── La prueba de permisos NUNCA sale a internet (3-oct). ──────────────────────────────────────────
# La pasada del bookkeeper SÍ atraviesa el permiso, y algunas rutas llaman servicios externos (Teller
# al vincular un banco, la IA al importar o proponer reglas). Con las claves del `.env` cargadas, eso
# era gastar crédito y mandar datos de prueba afuera en cada corrida. Se bloquea TODA conexión salvo
# la base de datos; las rutas que intenten salir fallan en local (y eso no es un 403: la prueba sigue
# midiendo solo permisos). Cubre también los servicios que se agreguen después, sin tocar esta lista.
import socket  # noqa: E402
from urllib.parse import urlparse  # noqa: E402

def _allowed_hosts() -> set:
    hosts = {"127.0.0.1", "::1", "localhost"}
    from dotenv import load_dotenv  # el mismo .env que lee db.py
    load_dotenv()
    db_host = urlparse(os.getenv("DATABASE_URL", "")).hostname
    if db_host:
        hosts.add(db_host)
        try:
            hosts |= {ai[4][0] for ai in socket.getaddrinfo(db_host, None)}
        except OSError:
            pass
    return hosts

_ALLOWED = _allowed_hosts()
BLOCKED: set = set()
_real_connect = socket.socket.connect

def _local_only(self, address):
    host = address[0] if isinstance(address, tuple) else None   # None = socket UNIX (local)
    if host is not None and host not in _ALLOWED:
        BLOCKED.add(host)
        raise ConnectionRefusedError(f"check_permissions no sale a internet ({host})")
    return _real_connect(self, address)

socket.socket.connect = _local_only

from datetime import date, datetime, timezone  # noqa: E402
from decimal import Decimal  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, delete, select  # noqa: E402

import auth  # noqa: E402
from db import engine  # noqa: E402
from main import app  # noqa: E402
from models import (CategorizationChange, CategoryRule, CellFormat, Company,  # noqa: E402
                    CompanyCategory, CompanyMember,
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
    ("POST", "/api/transactions/clarify-group"): {"transaction_ids": [], "answer": "x"},
    ("POST", "/api/companies/{company_id}/checkout"): {"quota": 100},
    ("PATCH", "/api/firm"): {"brand_name": "PERMCHECK"},
    ("PATCH", "/api/companies/{company_id}/settlement"): {"wait_days": 5},
    ("POST", "/api/companies/{company_id}/bank-connections"): {"public_token": "public-permcheck"},
    ("POST", "/api/companies/{company_id}/rules/preview"): {"keyword": "HOME DEPOT"},
    ("POST", "/api/companies/{company_id}/transactions/{transaction_id}/bank-removed"): {"action": "keep"},
    ("POST", "/api/companies/{company_id}/bank-connections/link-token"): {"language": "es"},
    ("POST", "/api/companies/{company_id}/checkout/confirm"): {"session_id": "cs_x"},
    ("POST", "/api/companies/{company_id}/transactions/set-account"): {
        "transaction_ids": ["__TX__"], "account": "Chase Checking"},
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


def drop_fixtures(s: Session) -> None:
    """Borra TODO lo creado por la prueba. Usa el borrado en cascada del demo (`demo._delete`), que
    DESCUBRE en la base qué cuelga de cada fila — por clave foránea y por referencia suave. Antes había
    una lista de tablas escrita a mano y se rompió dos veces con tablas nuevas de otras áreas
    (`sheetlabel` el 27-sep, `bankconnection` de Teller el 3-oct)."""
    import demo  # noqa: E402 (aquí, para no cargarlo si nadie limpia)
    users = s.exec(select(User).where(User.firebase_uid.in_([BOOK_UID, CLIENT_UID]))).all()
    uids = [u.id for u in users]
    fids = [f.id for f in s.exec(select(Firm).where(Firm.name == "PERMCHECK FIRM")).all()]
    # Por FIRMA y por DUEÑO, nunca por nombre: la prueba renombra el libro (`PATCH /companies`).
    cids: set[int] = set()
    if fids:
        cids |= set(s.exec(select(Company.id).where(Company.firm_id.in_(fids))).all())
    if uids:
        cids |= set(s.exec(select(Company.id).where(Company.user_id.in_(uids))).all())
    counts: dict[str, int] = {}
    conn = s.connection()
    demo._delete(conn, "company", sorted(cids), counts)
    demo._delete(conn, "firm", fids, counts)
    demo._delete(conn, "user", uids, counts)
    s.commit()


ANON_UID = "__permcheck_anonymous__"


def check_anonymous_tokens(client: TestClient) -> list[str]:
    """DEUDA #17 (4-oct): un token ANÓNIMO de Firebase solo entra en el demo. Demo y producción comparten
    el proyecto de Firebase, y cualquiera saca un token anónimo con la API key pública del frontend.

    Se apaga `AUTH_DEV_MODE` y se reemplaza la verificación de Firebase por una de mentira (sin red) que
    decodifica tokens inventados — así se prueba el camino REAL de producción (`Authorization: Bearer`)."""
    import auth
    import demo
    print("\n  --- tokens anónimos de Firebase (DEUDA #17) ---")
    fake_claims = {
        "anon": {"uid": ANON_UID, "firebase": {"sign_in_provider": "anonymous"}},
        "google": {"uid": "__permcheck_google__", "email": "g@test.local", "firebase": {"sign_in_provider": "google.com"}},
    }
    def fake_verify(token: str) -> dict:
        if token not in fake_claims:
            raise ValueError("firma inválida")
        return fake_claims[token]
    saved = (auth.AUTH_DEV_MODE, auth._verify_firebase_token, demo.DEMO_MODE)
    fails: list[str] = []
    def expect(label: str, ok: bool, got) -> None:
        print(f"  {'✅' if ok else '❌'} {label} → {got}")
        if not ok:
            fails.append(f"DEUDA #17: {label} → {got}")
    try:
        auth.AUTH_DEV_MODE, auth._verify_firebase_token, demo.DEMO_MODE = False, fake_verify, False
        # 1) Producción, por HTTP: 401 y NINGÚN usuario creado en la base.
        r = client.get("/api/me", headers={"Authorization": "Bearer anon"})
        with Session(engine) as s:
            created = s.exec(select(User).where(User.firebase_uid == ANON_UID)).first() is not None
        expect("fuera del demo, token anónimo en /api/me", r.status_code == 401 and not created,
               f"{r.status_code} · usuario creado: {created}")
        # 2) Producción: un token de Google sí pasa (no se rompió el login normal).
        ident = auth.current_identity(authorization="Bearer google", x_dev_uid=None, x_dev_email=None)
        expect("fuera del demo, token de Google", ident["uid"] == "__permcheck_google__", ident["uid"])
        # 3) Un token inválido sigue siendo 401.
        try:
            auth.current_identity(authorization="Bearer basura", x_dev_uid=None, x_dev_email=None)
            expect("token inválido", False, "entró")
        except Exception as e:
            expect("token inválido", getattr(e, "status_code", None) == 401, getattr(e, "detail", e))
        # 4) En el demo, el anónimo SÍ entra (sin pasar por la base: no se siembra nada aquí).
        demo.DEMO_MODE = True
        ident = auth.current_identity(authorization="Bearer anon", x_dev_uid=None, x_dev_email=None)
        expect("en el demo, token anónimo", ident["uid"] == ANON_UID, ident["uid"])
    finally:
        auth.AUTH_DEV_MODE, auth._verify_firebase_token, demo.DEMO_MODE = saved
        with Session(engine) as s:   # por si una regresión llegó a crearlo
            u = s.exec(select(User).where(User.firebase_uid == ANON_UID)).first()
            if u:
                import demo as _d
                _d._delete(s.connection(), "user", [u.id], {}); s.commit()
    return fails


DEMO_UIDS = {"anon": "__permcheck_demo_anon__", "google": "__permcheck_demo_google__",
             "broken": "__permcheck_demo_broken__"}


def check_demo_cleanup_firebase() -> list[str]:
    """DEUDA #18 (4-oct): la limpieza de 7 días borra también el usuario ANÓNIMO de Firebase, nunca uno
    de Google (demo y producción comparten el proyecto), y un fallo de Firebase no deja la base a medias.

    Firebase se reemplaza por uno de mentira (sin red). Las oficinas de prueba vencen en el año 2000 y la
    limpieza corre con `now` = 2-ene-2000: así SOLO toca estas, nunca una oficina de demo real de la base."""
    from datetime import datetime
    from types import SimpleNamespace
    import demo
    print("\n  --- limpieza del demo y usuarios de Firebase (DEUDA #18) ---")
    fails: list[str] = []
    def expect(label: str, ok: bool, got) -> None:
        print(f"  {'✅' if ok else '❌'} {label} → {got}")
        if not ok:
            fails.append(f"DEUDA #18: {label} → {got}")

    class FakeFirebase:
        def __init__(self): self.deleted: list[str] = []
        def get_user(self, uid):
            if uid == DEMO_UIDS["broken"]:
                raise ConnectionError("Firebase caído")
            if uid == DEMO_UIDS["google"]:
                return SimpleNamespace(provider_data=[object()], email="g@test.local", phone_number=None)
            return SimpleNamespace(provider_data=[], email=None, phone_number=None)
        def delete_user(self, uid): self.deleted.append(uid)

    def plant(s: Session) -> None:
        for uid in DEMO_UIDS.values():
            u = User(firebase_uid=uid); s.add(u); s.commit(); s.refresh(u)
            f = Firm(name="PERMCHECK DEMO", owner_user_id=u.id, demo_expires_at=datetime(2000, 1, 1))
            s.add(f); s.commit(); s.refresh(f)
            s.add(FirmMember(firm_id=f.id, user_id=u.id, email="", role=FirmRole.OWNER))
            s.add(Company(name="PERMCHECK DEMO BOOK", sector="construction", firm_id=f.id, user_id=u.id))
            s.commit()

    def leftovers(s: Session) -> int:
        return len(s.exec(select(User).where(User.firebase_uid.in_(list(DEMO_UIDS.values())))).all())

    def drop(s: Session) -> None:
        fids = list(s.exec(select(Firm.id).where(Firm.name == "PERMCHECK DEMO")).all())
        uids = list(s.exec(select(User.id).where(User.firebase_uid.in_(list(DEMO_UIDS.values())))).all())
        conn = s.connection()
        demo._delete(conn, "firm", fids, {}); demo._delete(conn, "user", uids, {}); s.commit()

    saved = (demo.DEMO_MODE, demo._firebase)
    try:
        # 1) Firebase responde: borra SOLO el anónimo; el de Google se queda; el que falla se cuenta.
        fake = FakeFirebase()
        demo.DEMO_MODE, demo._firebase = True, (lambda: fake)
        with Session(engine) as s:
            drop(s); plant(s)
            counts = demo.cleanup_expired_offices(s, now=datetime(2000, 1, 2))
            left = leftovers(s)
        expect("borra en Firebase solo el anónimo", fake.deleted == [DEMO_UIDS["anon"]], fake.deleted)
        expect("no borra la cuenta de Google ni oculta el fallo",
               counts.get("firebase_kept_not_anonymous") == 1 and counts.get("firebase_errors") == 1,
               {k: v for k, v in counts.items() if k.startswith("firebase")})
        expect("la base quedó limpia (3 oficinas y sus dueños)", left == 0, f"{left} usuarios quedan")
        # 2) firebase-admin ni arranca: la limpieza de la base igual termina.
        def broken():
            raise RuntimeError("sin credenciales")
        demo._firebase = broken
        with Session(engine) as s:
            plant(s)
            try:
                counts = demo.cleanup_expired_offices(s, now=datetime(2000, 1, 2)); err = None
            except Exception as e:
                err = e
            left = leftovers(s)
        expect("Firebase caído no rompe la limpieza de la base", err is None and left == 0,
               f"error: {err} · usuarios quedan: {left}")
        # 3) Fuera del demo nunca se llama a Firebase (las pruebas y la producción no lo tocan).
        fake = FakeFirebase()
        demo.DEMO_MODE, demo._firebase = False, (lambda: fake)
        with Session(engine) as s:
            plant(s)
            demo.cleanup_expired_offices(s, now=datetime(2000, 1, 2))
        expect("fuera del demo no se toca Firebase", fake.deleted == [], fake.deleted)
    finally:
        demo.DEMO_MODE, demo._firebase = saved
        with Session(engine) as s:
            drop(s)
    return fails


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

    leaks.extend(check_anonymous_tokens(client))
    leaks.extend(check_demo_cleanup_firebase())

    print(f"\n  Rutas comprobadas: {checked}  ·  permitidas al cliente: {len(CLIENT_ALLOWED)}")
    if BLOCKED:
        print(f"  🔒 Conexiones a internet BLOQUEADAS durante la prueba: {sorted(BLOCKED)}")
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
