"""
ContaBu — el despliegue de DEMO del concurso (FASE 13, área `Firm / bookkeepers`).

**Las dos puertas** (Jose, 23-oct; `docs/DEMO Y ENTREGA.md` → "El plan del 22 al 30-oct"):
- **Visita guiada** — login ANÓNIMO de Firebase, sin cuenta. Todos ven UNA oficina compartida (`DemoOffice`
  kind="tour") con los 3 libros y la revisión de los agentes YA GUARDADA, como VIEWER: nadie la cambia y
  no gasta IA. No se crea nada por visitante (ni oficina ni membresía: el acceso de VIEWER lo da
  `demo_role`). La crea y la revisa UNA vez el script `demo.py tour`; nunca vence.
- **Demo en vivo** — "Entrar con Google" y después un **código de acceso** que verifica el backend
  (`redeem_code`). Sin código válido no hay oficina ni IA, tampoco por la API directa: `auth.effective_role`
  y `auth.user_firm` pasan por `demo_role` / `visible_firm`. Con código, el usuario recibe su oficina con los
  3 libros: una ya sembrada de la **piscina** (`DemoOffice` kind="pool") si hay, o sembrada en el momento.

Todo esto se enciende SOLO con la variable de entorno `DEMO_MODE=true` del servicio de demo
(`contabu-demo-api`). En producción y en desarrollo está apagado y este módulo no hace nada.

Tres candados, a propósito:
1. **`DEMO_MODE` y `AUTH_DEV_MODE` juntos no arrancan.** Con `AUTH_DEV_MODE` la cabecera `X-Dev-Uid` deja
   que cualquiera sea cualquiera; en un servicio público sería entregar todas las oficinas. Al importar
   este módulo con las dos encendidas, el backend NO arranca (fail closed).
2. **La limpieza solo borra lo que nació como demo**: oficinas con `Firm.demo_expires_at` vencida. Una
   oficina real tiene esa columna en NULL, así que un servicio de demo apuntado por error a la base real
   no puede borrar nada que no sea demo.
3. **La limpieza no depende de una lista de tablas escrita a mano**: descubre en el momento qué cuelga
   de cada fila — por clave foránea y por referencia suave (`company_id`, `transaction_id`, `firm_id`
   sin FK, como el historial de correcciones). Las tablas que agreguen otras áreas quedan cubiertas solas.

Y la visita guiada no se borra por ningún camino: su `demo_expires_at` es NULL (candado 2) y, además, la
limpieza excluye su oficina por nombre de papel (`_tour_ids`).

🔴 **La piscina se rellena DENTRO de una petición** (`POST /api/demo/pool/refill`): Cloud Run corre con la
CPU limitada fuera de las peticiones, así que un hilo que siembre después de responder no avanza. La pide
el frontend sin esperarla (al abrir la pantalla del código y al canjearlo), cuando Neon ya está despierto.
No se usa Cloud Scheduler a propósito: un pinchazo cada pocos minutos no deja dormir a Neon (Free: 100
CU-h/mes) y una vez por hora no sirve para nada. Para cebarla a mano, `demo.py pool`.
"""

import hashlib
import hmac
import logging
import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional

from sqlalchemy import text
from sqlmodel import Session, func, select

from models import (AgentCall, BookNote, Company, DemoAccess, DemoCodeAttempt, DemoOffice, DemoTourProposal,
                    Finding, Firm, FirmMember, FirmRole, Role, Transaction, User)

log = logging.getLogger("contabu.demo")

DEMO_MODE = os.getenv("DEMO_MODE", "").lower() in {"1", "true", "yes"}
DEMO_TTL_DAYS = int(os.getenv("DEMO_TTL_DAYS", "7"))
# Oficinas ya sembradas en espera de un código (la primera carga: ~5–6 s de siembra que el juez no espera).
DEMO_POOL_SIZE = int(os.getenv("DEMO_POOL_SIZE", "3"))
# El tope DIARIO GLOBAL de todo el demo (últimas 24 h, todas las oficinas). 0 = sin tope global. El tope por
# oficina sigue aparte (`AGENT_DAILY_CALLS`, `agent_calls.py`).
GLOBAL_DAILY_CALLS = int(os.getenv("DEMO_GLOBAL_DAILY_CALLS") or 600)
GLOBAL_DAILY_SEARCHES = int(os.getenv("DEMO_GLOBAL_DAILY_SEARCHES") or 100)

# Los dos códigos de acceso (vienen de Secret Manager). El de Devpost queda fijo hasta el 15-dic; el de
# invitados se cambia cuando haga falta, y si se VACÍA, quienes entraron con él pierden el acceso.
_CODE_ENV = {"devpost": "DEMO_ACCESS_CODE", "guest": "DEMO_ACCESS_CODE_GUEST"}
# Intentos fallidos permitidos antes de bloquear un rato.
MAX_FAILS_USER, USER_WINDOW = 5, timedelta(minutes=15)
MAX_FAILS_IP, IP_WINDOW = 20, timedelta(hours=1)

# Dueños de sistema de la visita y de la piscina. Ningún login de Firebase produce un uid con ":" de este
# modo (los anónimos y los de Google son aleatorios; los personalizados solo los firma la cuenta de servicio).
TOUR_UID = "demo:tour"
POOL_UID = "demo:pool"

# Candado 1 — se evalúa al importar: si las dos están encendidas, el servicio no levanta.
if DEMO_MODE and os.getenv("AUTH_DEV_MODE", "").lower() in {"1", "true", "yes"}:
    raise RuntimeError(
        "DEMO_MODE y AUTH_DEV_MODE no pueden estar encendidos a la vez: con AUTH_DEV_MODE cualquiera "
        "puede hacerse pasar por cualquier usuario en un despliegue público. Apaga AUTH_DEV_MODE."
    )


def _now() -> datetime:
    # Las columnas DateTime del proyecto se guardan sin zona (UTC); se compara en el mismo formato.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def mark_if_demo(firm: Firm) -> None:
    """En el demo, toda oficina que nace lleva su fecha de borrado. Fuera del demo, no hace nada."""
    if DEMO_MODE:
        firm.demo_expires_at = _now() + timedelta(days=DEMO_TTL_DAYS)


# ── Siembra de los libros del visitante ─────────────────────────────────────────────────────────

def seed_visitor_books(session: Session, firm: Firm, owner: User, *, strict: bool = False) -> list[Company]:
    """Los libros con que arranca la oficina de un visitante del demo.

    Llama al generador de datos de Movimientos, con la firma acordada en el 📬 Buzón (3-oct):

        demo_data.seed_demo_books(session, firm, owner) -> list[Company]

    que crea los libros DENTRO de `firm` (firm_id=firm.id, user_id=owner.id) con sus transacciones,
    reglas, notas e historial, y hace `session.flush()` — NO `commit()`: el commit lo hace `/api/me`, así
    oficina y libros nacen juntos o no nace nada.

    Si el generador todavía no existe (o falla), el visitante recibe un libro vacío como cualquier
    cuenta nueva: el demo queda pobre, pero no roto. Se deja en el log para que se note. Con `strict` (la
    visita y la piscina: nadie está esperando) el error sube, para no guardar una oficina pobre.
    """
    try:
        import demo_data  # área Movimientos
    except ImportError:
        if strict:
            raise
        demo_data = None
        log.warning("demo_data todavía no existe (Movimientos); el visitante recibe un libro vacío")
    if demo_data is not None:
        try:
            # SAVEPOINT: si el generador falla, se deshace SOLO lo suyo. Un rollback completo se llevaría
            # también la oficina recién creada del visitante (aún sin commit).
            with session.begin_nested():
                books = demo_data.seed_demo_books(session, firm, owner)
            if books:
                return list(books)
            log.warning("demo_data.seed_demo_books no creó libros; el visitante recibe un libro vacío")
        except Exception:  # un error del generador no debe impedir entrar al demo
            if strict:
                raise
            log.exception("demo_data.seed_demo_books falló; el visitante recibe un libro vacío")
    if strict:
        raise RuntimeError("demo_data.seed_demo_books no creó libros")
    book = Company(name="My Business", sector="", user_id=owner.id, firm_id=firm.id)
    session.add(book)
    session.flush()
    return [book]


# ── Las dos puertas ───────────────────────────────────────────────────────────────────────────────

def _cache(session: Session) -> dict:
    """Lo que se consulta varias veces en UNA petición (el rol se pide por cada libro): vive en la sesión."""
    return session.info.setdefault("demo", {})


def tour_firm_id(session: Session) -> Optional[int]:
    """La oficina de la visita guiada (o None si el script todavía no la creó)."""
    c = _cache(session)
    if "tour" not in c:
        c["tour"] = session.exec(select(DemoOffice.firm_id).where(DemoOffice.kind == "tour")).first()
    return c["tour"]


def access_of(session: Session, user: User) -> Optional[str]:
    """El código con que `user` entró al demo en vivo ("devpost" | "guest"), si ese código SIGUE puesto en el
    servicio. None = sin acceso: si Jose vacía `DEMO_ACCESS_CODE_GUEST`, sus usuarios caen aquí solos."""
    c = _cache(session).setdefault("access", {})
    if user.id not in c:
        row = session.exec(select(DemoAccess).where(DemoAccess.user_id == user.id)).first()
        live = row is not None and bool((os.getenv(_CODE_ENV.get(row.code_name, "")) or "").strip())
        c[user.id] = row.code_name if live else None
    return c[user.id]


def demo_role(session: Session, company: Company, user: User, role: Optional[Role]) -> Optional[Role]:
    """El rol sobre un libro EN EL DEMO, encima del de siempre (`auth.effective_role`):
    - un libro de la visita guiada → VIEWER para cualquiera con sesión (no hace falta membresía; tampoco el
      dueño de sistema puede escribir por la API);
    - cualquier otro libro → el rol de siempre, SOLO si el usuario tiene un código vigente. Así una cuenta sin
      código no alcanza ninguna oficina (tampoco las de visitantes anónimos de antes de las dos puertas)."""
    if company.firm_id is not None and company.firm_id == tour_firm_id(session):
        return Role.VIEWER
    return role if access_of(session, user) else None


def visible_firm(session: Session, user: User) -> tuple[Optional[Firm], Optional[FirmRole]]:
    """`auth.user_firm` en el demo: con código, su oficina (la primera que no sea la visita); sin código, la
    visita guiada como VIEWER (o nada, si todavía no existe)."""
    tour = tour_firm_id(session)
    if access_of(session, user):
        q = select(FirmMember).where(FirmMember.user_id == user.id).order_by(FirmMember.id)
        if tour is not None:
            q = q.where(FirmMember.firm_id != tour)
        fm = session.exec(q).first()
        return (session.get(Firm, fm.firm_id), fm.role) if fm else (None, None)
    return (session.get(Firm, tour), FirmRole.VIEWER) if tour else (None, None)


def door(session: Session, user: User, *, anonymous: bool) -> str:
    """Por qué puerta entra quien llega a `/api/me`, y deja lista su oficina si le toca una:
    "live" (tiene código: su oficina, asignada o sembrada aquí) · "tour" (anónimo: la visita) ·
    "needs_code" (Google sin código: la pantalla del código; puede mirar la visita) · "tour_missing"."""
    if access_of(session, user):
        ensure_live_office(session, user)
        return "live"
    if not anonymous:
        return "needs_code"
    return "tour" if tour_firm_id(session) is not None else "tour_missing"


# ── El código de acceso ───────────────────────────────────────────────────────────────────────────

def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def match_code(given: str) -> Optional[str]:
    """¿Qué código es `given`? ("devpost" | "guest" | None). En TIEMPO CONSTANTE: se compara con los DOS
    siempre, sobre huellas del mismo largo (`hmac.compare_digest`), así ni el tiempo ni el largo dicen nada.
    Un código vacío en el servicio está apagado: nunca coincide."""
    got = _digest((given or "").strip())
    found = None
    for name, env in _CODE_ENV.items():
        expected = (os.getenv(env) or "").strip()
        same = hmac.compare_digest(got, _digest(expected))
        if same and expected and found is None:
            found = name
    return found


def client_ip(headers, fallback: Optional[str]) -> str:
    """La IP del que llama. En Cloud Run el frente de Google AGREGA la IP real al final de
    `X-Forwarded-For` (lo de antes lo puede escribir el cliente), así que se toma la última. 🟡 Sin verificar
    en la consola: por eso el límite por IP es holgado y el que manda es el límite por usuario."""
    xff = (headers.get("x-forwarded-for") or "").split(",")
    last = xff[-1].strip() if xff else ""
    return last or fallback or "?"


def _ip_hash(ip: str) -> str:
    return hashlib.sha256(f"contabu-demo:{ip}".encode()).hexdigest()[:32]


class CodeRejected(Exception):
    """`code`: demo_code_invalid · demo_code_locked · demo_google_required · demo_not_ready."""
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def redeem_code(session: Session, user: User, given: str, *, ip: str, anonymous: bool) -> str:
    """Canjea un código: lo verifica, guarda CUÁL usó el usuario y le deja su oficina. Devuelve el nombre del
    código. El código NUNCA va al log ni a la base (solo su nombre); un fallo deja un intento (IP en huella)."""
    if anonymous:
        raise CodeRejected("demo_google_required")      # la visita no canjea: primero "Entrar con Google"
    if access_of(session, user):
        ensure_live_office(session, user)
        return access_of(session, user) or ""
    now = _now()
    h = _ip_hash(ip)
    fails_user = session.exec(select(func.count(DemoCodeAttempt.id)).where(
        DemoCodeAttempt.user_id == user.id, DemoCodeAttempt.created_at >= now - USER_WINDOW)).one()
    fails_ip = session.exec(select(func.count(DemoCodeAttempt.id)).where(
        DemoCodeAttempt.ip_hash == h, DemoCodeAttempt.created_at >= now - IP_WINDOW)).one()
    if fails_user >= MAX_FAILS_USER or fails_ip >= MAX_FAILS_IP:
        raise CodeRejected("demo_code_locked")          # ni se compara: el bloqueo no dice si acertó
    name = match_code(given)
    if name is None:
        session.add(DemoCodeAttempt(user_id=user.id, ip_hash=h, created_at=now))
        session.commit()
        log.info("demo: código rechazado (usuario %s)", user.id)
        raise CodeRejected("demo_code_invalid")
    row = session.exec(select(DemoAccess).where(DemoAccess.user_id == user.id)).first()
    if row is None:
        row = DemoAccess(user_id=user.id, code_name=name)
    row.code_name, row.granted_at = name, now
    session.add(row)
    session.commit()
    _cache(session).pop("access", None)
    log.info("demo: acceso concedido (usuario %s, código %s)", user.id, name)
    ensure_live_office(session, user)
    return name


# ── Las oficinas: la visita, la piscina y la del usuario ─────────────────────────────────────────

def _system_user(session: Session, uid: str) -> User:
    u = session.exec(select(User).where(User.firebase_uid == uid)).first()
    if u is None:
        u = User(firebase_uid=uid, email=None)
        session.add(u)
        session.flush()
    return u


def _new_office(session: Session, owner: User, kind: str, name: str) -> Firm:
    """Una oficina del demo con sus 3 libros. La visita NO lleva fecha de borrado (candado 2); las demás sí,
    y la de una asignada se vuelve a contar desde que se asigna. No hace commit."""
    firm = Firm(name=name, owner_user_id=owner.id)
    if kind != "tour":
        firm.demo_expires_at = _now() + timedelta(days=DEMO_TTL_DAYS)
    session.add(firm)
    session.flush()
    session.add(FirmMember(firm_id=firm.id, user_id=owner.id, email=owner.email or "", role=FirmRole.OWNER))
    session.add(DemoOffice(firm_id=firm.id, kind=kind, assigned_at=_now() if kind == "live" else None))
    seed_visitor_books(session, firm, owner, strict=kind != "live")
    return firm


_USER_COLS = ("user_id", "changed_by_user_id", "created_by_user_id", "resolved_by_user_id")


def _hand_over(session: Session, firm: Firm, old_user_id: int, user: User) -> None:
    """Pasa una oficina de la piscina a su dueño de verdad: la oficina, su membresía, los libros que creó el
    dueño de sistema y todo lo que la siembra firmó con él (el historial de correcciones…). Las columnas se
    DESCUBREN en la base (como la limpieza): las tablas por libro que agreguen otras áreas quedan cubiertas."""
    session.flush()
    conn = session.connection()
    ids = [r[0] for r in conn.execute(text("SELECT id FROM company WHERE firm_id = :f"), {"f": firm.id})]
    conn.execute(text("UPDATE firmmember SET user_id = :u, email = :e WHERE firm_id = :f AND user_id = :o"),
                 {"u": user.id, "e": user.email or "", "f": firm.id, "o": old_user_id})
    conn.execute(text("UPDATE company SET user_id = :u WHERE firm_id = :f AND user_id = :o"),
                 {"u": user.id, "f": firm.id, "o": old_user_id})
    if not ids:
        return
    cols = conn.execute(text("""
        SELECT c.table_name, c.column_name FROM information_schema.columns c
        WHERE c.table_schema = 'public' AND c.column_name = ANY(:cols) AND EXISTS (
          SELECT 1 FROM information_schema.columns k
          WHERE k.table_schema = 'public' AND k.table_name = c.table_name AND k.column_name = 'company_id')
    """), {"cols": list(_USER_COLS)}).fetchall()
    for table, col in cols:
        conn.execute(text(f'UPDATE "{table}" SET "{col}" = :u WHERE "{col}" = :o AND company_id = ANY(:ids)'),
                     {"u": user.id, "o": old_user_id, "ids": ids})


def claim_pool_office(session: Session, user: User) -> Optional[Firm]:
    """Toma UNA oficina de la piscina para `user` (o None si está vacía). `FOR UPDATE SKIP LOCKED`: dos
    canjes a la vez nunca se llevan la misma. Su fecha de borrado se cuenta desde AHORA. No hace commit."""
    now = _now()
    row = session.exec(
        select(DemoOffice).join(Firm, Firm.id == DemoOffice.firm_id)
        .where(DemoOffice.kind == "pool")
        .where(Firm.demo_expires_at > now + timedelta(hours=1))    # no una que la limpieza esté por borrar
        .order_by(DemoOffice.id).limit(1).with_for_update(skip_locked=True, of=DemoOffice)
    ).first()
    if row is None:
        return None
    firm = session.get(Firm, row.firm_id)
    old = firm.owner_user_id
    firm.owner_user_id = user.id
    firm.name = (user.email or "").strip() or "My Firm"
    firm.demo_expires_at = now + timedelta(days=DEMO_TTL_DAYS)
    row.kind, row.assigned_at = "live", now
    session.add(firm)
    session.add(row)
    _hand_over(session, firm, old, user)
    return firm


def ensure_live_office(session: Session, user: User) -> None:
    """Quien tiene código y todavía no tiene oficina (recién canjeado, o la suya venció) recibe una: de la
    piscina si hay (al instante) o sembrada aquí (los 5–6 s de siempre)."""
    if visible_firm(session, user)[0] is not None:
        return
    cleanup_quietly(session)
    # Una a la vez POR USUARIO (dos pestañas que entran juntas no se llevan dos oficinas): candado de la
    # transacción de esta sesión, se suelta en el commit; quien esperaba vuelve a mirar y ya la encuentra.
    session.execute(text("select pg_advisory_xact_lock(:s, :u)"), {"s": _LOCK_SPACE + 1, "u": user.id})
    if visible_firm(session, user)[0] is None and claim_pool_office(session, user) is None:
        _new_office(session, user, "live", (user.email or "").strip() or "My Firm")
    session.commit()


def pool_ready(session: Session) -> int:
    return int(session.exec(
        select(func.count(DemoOffice.id)).join(Firm, Firm.id == DemoOffice.firm_id)
        .where(DemoOffice.kind == "pool").where(Firm.demo_expires_at > _now() + timedelta(hours=1))).one())


_LOCK_SPACE = 0x44454D4F   # "DEMO": espacio de candados de Postgres; el segundo número dice cuál


@contextmanager
def _try_lock(which: int) -> Iterator[bool]:
    """Candado de TRANSACCIÓN en una conexión aparte (el pooler de Neon va en modo transacción: ver
    `agent_calls.exclusive`). `False` si otro ya lo tiene: no se espera."""
    from db import engine
    with engine.connect() as conn:
        try:
            yield bool(conn.execute(text("select pg_try_advisory_xact_lock(:s, :w)"),
                                    {"s": _LOCK_SPACE, "w": which}).scalar())
        finally:
            conn.rollback()


def refill_pool(session: Session) -> dict:
    """Siembra oficinas en espera hasta `DEMO_POOL_SIZE`. Una a la vez en todo el servicio (si otra petición
    ya está rellenando, vuelve al instante) y cada oficina con su commit: si la petición se corta, lo hecho
    queda. Barre lo vencido primero."""
    with _try_lock(1) as got:
        if not got:
            return {"busy": True, "ready": pool_ready(session), "created": 0}
        cleanup_quietly(session)
        owner = _system_user(session, POOL_UID)
        made = 0
        while pool_ready(session) < DEMO_POOL_SIZE:
            try:
                _new_office(session, owner, "pool", "Demo office (waiting)")
                session.commit()
            except Exception:
                session.rollback()
                log.exception("demo: no se pudo sembrar una oficina en espera")
                return {"busy": False, "ready": pool_ready(session), "created": made, "error": True}
            made += 1
        if made:
            log.info("demo: piscina +%d (lista: %d)", made, DEMO_POOL_SIZE)
        return {"busy": False, "ready": pool_ready(session), "created": made}


# ── El tope DIARIO GLOBAL ─────────────────────────────────────────────────────────────────────────

def check_global_cap(session: Session, agent: Optional[str] = None) -> None:
    """Lo llama `agent_calls.check_cap` antes de cada llamada: ¿le quedan al DEMO ENTERO llamadas a los
    agentes (o búsquedas en Tavily, `agent="verifier"`) en las últimas 24 h? Fuera del demo no hace nada."""
    if not DEMO_MODE:
        return
    import agent_calls
    searches = agent == "verifier"
    cap = GLOBAL_DAILY_SEARCHES if searches else GLOBAL_DAILY_CALLS
    if cap <= 0:
        return
    agents = ("verifier",) if searches else agent_calls._MODEL_AGENTS
    since = _now() - timedelta(hours=24)
    used = session.exec(select(func.count(AgentCall.id))
                        .where(AgentCall.created_at >= since, AgentCall.agent.in_(agents))).one()
    if used >= cap:
        raise agent_calls.GlobalCapReached(f"demo: {cap} {'búsquedas' if searches else 'llamadas'} en 24 h")


# ── La visita guiada: se crea y se revisa UNA vez (el script de abajo) ───────────────────────────

def build_tour(session: Session, *, review: bool = True, lang: str = "en") -> Firm:
    """Crea la oficina de la visita con los 3 libros y, con `review`, corre lo mismo que "Revisar el libro"
    (auditar → verificar en la web → el ① vuelve a juzgar → el ④ redacta reglas) y lo deja GUARDADO; las reglas
    propuestas, en `DemoTourProposal` (🔄 23-oct: en el resto de la app viven solo en pantalla). Gasta Nebius y
    Tavily una sola vez. Si la visita ya existe, se niega (para rehacerla, se borra a mano primero)."""
    if tour_firm_id(session) is not None:
        raise RuntimeError(f"la visita guiada ya existe (oficina {tour_firm_id(session)})")
    owner = _system_user(session, TOUR_UID)
    firm = _new_office(session, owner, "tour", "ContaBu guided tour")
    session.commit()
    _cache(session).pop("tour", None)
    if review:
        review_tour(session, firm, owner, lang=lang)
        draft_tour(session, firm, owner, lang=lang)
    return firm


def review_tour(session: Session, firm: Firm, owner: User, *, lang: str = "en") -> None:
    import agent_calls
    import agents
    import merchant_verifier
    books = session.exec(select(Company).where(Company.firm_id == firm.id).order_by(Company.id)).all()
    for company in books:
        with agent_calls.exclusive(company):
            res = agents.review(session, company, lang=lang, user_id=owner.id)
            print(f"  {company.name}: auditoría + ① → {res.outcome} ({res.reviewed} juzgados)")
            try:
                v = merchant_verifier.verify_flagged(session, company, user_id=owner.id)
                again = agents.review(session, company, lang=lang, user_id=owner.id,
                                      finding_ids=v.finding_ids) if v.finding_ids else None
                print(f"  {company.name}: ② {v.searched} búsquedas, {v.from_cache} de memoria"
                      + (f" → ① otra vez: {again.reviewed}" if again else ""))
            except merchant_verifier.TavilyUnavailable:
                print(f"  {company.name}: ② sin Tavily (TAVILY_API_KEY): queda sin evidencia de la web")


def draft_tour(session: Session, firm: Firm, owner: User, *, lang: str = "en") -> None:
    """El ④ redacta reglas para cada libro de la visita y se GUARDAN (`DemoTourProposal`): en cualquier otro
    lado las propuestas viven solo en pantalla hasta aprobarlas; la visita es de solo lectura y su recorrido las
    muestra. Reemplaza las que hubiera. En inglés (lo leen los jueces): el ④ escribe en un solo idioma."""
    import agent_calls
    import agents
    books = session.exec(select(Company).where(Company.firm_id == firm.id).order_by(Company.id)).all()
    for company in books:
        with agent_calls.exclusive(company):
            res = agents.draft(session, company, lang=lang, user_id=owner.id)
        for old in session.exec(select(DemoTourProposal).where(DemoTourProposal.company_id == company.id)).all():
            session.delete(old)
        for i, p in enumerate(res.proposals):
            session.add(DemoTourProposal(company_id=company.id, position=i, data=agents.proposal_dict(p)))
        session.commit()
        print(f"  {company.name}: ④ → {res.outcome}, {len(res.proposals)} reglas propuestas guardadas")


def tour_proposals(session: Session, company: Company) -> list[dict]:
    rows = session.exec(select(DemoTourProposal).where(DemoTourProposal.company_id == company.id)
                        .order_by(DemoTourProposal.position)).all()
    return [r.data for r in rows]


# ── El recorrido guiado (23-oct): la pantalla de inicio, quién juzgó cada hallazgo y la parada 5 ──

def _model_label(model: Optional[str]) -> Optional[str]:
    import agent_models
    if not model:
        return None
    return agent_models.LABELS.get(agent_models.short(model), model)


def intro(session: Session) -> dict:
    """La pantalla de inicio del recorrido: los agentes con su modelo (`agent_models`, lo que corre hoy) y el
    antes/después de RIVERA HVAC de la visita, todo contado por SQL (nada escrito a mano)."""
    import agent_models
    import book_review
    team = [{"agent": a, "model": agent_models.LABELS.get(agent_models.short(agent_models.get(a)[0]),
                                                           agent_models.get(a)[0])}
            for a in agent_models.DEFAULTS]
    tid = tour_firm_id(session)
    book = session.exec(select(Company).where(Company.firm_id == tid).order_by(Company.id)).first() if tid else None
    if book is None:
        return {"team": team, "book": None}
    count = lambda q: int(session.exec(q).one())   # noqa: E731
    findings = select(func.count(Finding.id)).where(Finding.company_id == book.id)
    return {
        "team": team,
        "book": book.name,
        "rows": count(select(func.count(Transaction.id)).where(Transaction.company_id == book.id)),
        "pending": count(select(func.count(Transaction.id)).where(Transaction.company_id == book.id,
                                                                  Transaction.account_type.is_(None))),
        "issues": count(findings.where(Finding.type.not_in(book_review.INFO_TYPES))),
        "explained": count(findings.where(Finding.agent_verdict.is_not(None))),
        "proposals": count(select(func.count(DemoTourProposal.id)).where(DemoTourProposal.company_id == book.id)),
    }


def judges(session: Session, company: Company) -> dict[int, dict]:
    """Quién juzgó DE VERDAD cada hallazgo, sacado de `agentcall`: {finding_id: {"by", "model"}}.
    - con veredicto del ① → la llamada del ① que lo dejó (`agent_reviewed_at` se fija justo antes de guardarla);
    - `note_check` → el ⑥ traductor de notas (no lo juzga el ①); `personal_use` → el ⑤;
    - lo demás (nómina, cobertura…) → "code": lo encontró el auditor, sin IA."""
    import book_review
    calls = session.exec(select(AgentCall).where(AgentCall.company_id == company.id, AgentCall.outcome == "ok")
                         .order_by(AgentCall.created_at)).all()
    reviewer = [c for c in calls if c.agent == "reviewer"]
    last = {c.agent: c for c in calls}   # la última de cada agente
    out: dict[int, dict] = {}
    for f in session.exec(select(Finding).where(Finding.company_id == company.id)).all():
        c = _reviewer_call(reviewer, f.agent_reviewed_at) if f.agent_reviewed_at is not None else None
        if c is not None:
            out[f.id] = {"by": "reviewer", "model": _model_label(c.model)}
            continue
        if f.type == "note_check" and "note_checker" in last:
            out[f.id] = {"by": "note_checker", "model": _model_label(last["note_checker"].model)}
        elif f.type == "personal_use" and "personal_judge" in last:
            out[f.id] = {"by": "personal_judge", "model": _model_label(last["personal_judge"].model)}
        elif f.type in book_review.UNJUDGED_TYPES or f.agent_reviewed_at is None:
            out[f.id] = {"by": "code", "model": None}
    return out


def _reviewer_call(reviewer: list[AgentCall], at: datetime) -> Optional[AgentCall]:
    """La llamada del ① que juzgó un hallazgo juzgado en `at`. El ① fija `agent_reviewed_at` (UTC sin zona) y
    guarda su `agentcall` milisegundos después — pero `agentcall.created_at` se guarda CON zona y Postgres lo
    pasa a su hora local (✅ 23-oct, base local: 7 h justas detrás; `DEUDA TECNICA.md` #26). Así que se empareja
    por lo que queda dentro de la hora (≤ 60 s), con hasta 14 h de corrimiento: vale en Neon (UTC) y en local."""
    best, gap = None, timedelta(seconds=60)
    for c in reviewer:
        d = (at - c.created_at).total_seconds()
        hours = round(d / 3600)
        residual = timedelta(seconds=abs(d - hours * 3600))
        if abs(hours) <= 14 and residual <= gap:
            best, gap = c, residual
    return best


_VACATION = re.compile(r"vacation|vacacion|europe|europa|\btrip\b|viaje", re.I)


def guide_pick(session: Session, company: Company) -> dict:
    """El hallazgo de las paradas 9, 11 y 12 del recorrido, MÁS `transaction_id`: la primera fila (por fecha) de ese
    hallazgo, para que la parada 11 abra su panel lateral. Ver `_guide_finding` para cómo se elige el hallazgo."""
    pick = _guide_finding(session, company)
    tx_id, f = None, None
    if pick["finding_id"] is not None:
        f = session.get(Finding, pick["finding_id"])
        ids = [int(i) for i in (f.transaction_ids or [])] if f else []
        if ids:
            first = session.exec(select(Transaction).where(Transaction.company_id == company.id, Transaction.id.in_(ids))
                                 .order_by(Transaction.date, Transaction.id)).first()
            tx_id = first.id if first else None
    # La nota de la parada 7: el hallazgo `note_check` lleva su id en la clave (`note:<id>:<comercio>`).
    note_id = None
    if pick["kind"] == "vacation" and pick["finding_id"] is not None:
        parts = (f.key or "").split(":") if f else []
        note_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    return {**pick, "transaction_id": tx_id, "note_id": note_id}


def _guide_finding(session: Session, company: Company) -> dict:
    """El hallazgo de la parada 5. El de la nota de las vacaciones SOLO si la revisión guardada está limpia: que
    el ① no ate Netflix a las vacaciones (#4 del plan del 22-oct) y que Starbucks no salga partido por ciudades
    (#5). Si no, "Nómina: el banco solo ve lo neto"; si tampoco, el primero con veredicto."""
    fs = session.exec(select(Finding).where(Finding.company_id == company.id, Finding.status == "open")
                      .order_by(Finding.id)).all()
    notes = {n.id: n.text or "" for n in session.exec(select(BookNote).where(BookNote.company_id == company.id)).all()}

    def note_text(f: Finding) -> str:
        parts = (f.key or "").split(":")
        return notes.get(int(parts[1]), "") if len(parts) > 1 and parts[1].isdigit() else ""

    netflix_tied = any("NETFLIX" in (f.merchant or "").upper()
                       and _VACATION.search(f"{f.agent_observation_en or ''} {f.agent_observation_es or ''}")
                       for f in fs)
    starbucks = {(f.merchant or "").upper() for f in fs if (f.merchant or "").upper().startswith("STARBUCKS")}
    vacation = [f for f in fs if f.type == "note_check" and _VACATION.search(note_text(f))]
    if vacation and not netflix_tied and len(starbucks) <= 1:
        return {"finding_id": vacation[0].id, "kind": "vacation"}
    payroll = next((f for f in fs if f.type == "payroll_net_only"), None)
    if payroll:
        return {"finding_id": payroll.id, "kind": "payroll",
                "why": "netflix" if netflix_tied else "starbucks" if len(starbucks) > 1 else "no_note"}
    first = next((f for f in fs if f.agent_verdict), fs[0] if fs else None)
    return {"finding_id": first.id if first else None, "kind": "finding"}


# ── Limpieza de las oficinas vencidas ───────────────────────────────────────────────────────────

# Referencias SUAVES (sin clave foránea) que se siguen por convención de nombre.
_SOFT_KEYS = {"company": "company_id", "transaction": "transaction_id", "firm": "firm_id"}


def _children(conn, table: str) -> list[tuple[str, str]]:
    """(tabla_hija, columna) de todo lo que apunta a `table`: claves foráneas + referencias suaves."""
    fk = conn.execute(text("""
        SELECT kcu.table_name, kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON kcu.constraint_name = tc.constraint_name AND kcu.table_schema = tc.table_schema
        JOIN information_schema.constraint_column_usage ccu
          ON ccu.constraint_name = tc.constraint_name AND ccu.table_schema = tc.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'
          AND ccu.table_name = :t AND ccu.column_name = 'id'
    """), {"t": table}).fetchall()
    found = {(r[0], r[1]) for r in fk}
    soft = _SOFT_KEYS.get(table)
    if soft:
        rows = conn.execute(text("""
            SELECT table_name FROM information_schema.columns
            WHERE table_schema = 'public' AND column_name = :c AND table_name <> :t
        """), {"c": soft, "t": table}).fetchall()
        found |= {(r[0], soft) for r in rows}
    return sorted(found)


def _has_id(conn, table: str) -> bool:
    return bool(conn.execute(text("""
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = :t AND column_name = 'id'
    """), {"t": table}).first())


def _delete(conn, table: str, ids: list[int], counts: dict[str, int]) -> None:
    """Borra `ids` de `table` y, ANTES, todo lo que cuelga de ellos (en profundidad)."""
    if not ids:
        return
    for child, col in _children(conn, table):
        if child == table:
            continue
        if _has_id(conn, child):
            child_ids = [r[0] for r in conn.execute(
                text(f'SELECT id FROM "{child}" WHERE "{col}" = ANY(:ids)'), {"ids": ids})]
            _delete(conn, child, child_ids, counts)
        else:
            n = conn.execute(text(f'DELETE FROM "{child}" WHERE "{col}" = ANY(:ids)'), {"ids": ids}).rowcount
            counts[child] = counts.get(child, 0) + n
    n = conn.execute(text(f'DELETE FROM "{table}" WHERE id = ANY(:ids)'), {"ids": ids}).rowcount
    counts[table] = counts.get(table, 0) + n


def _user_is_orphan(conn, user_id: int) -> bool:
    """¿Ya nada apunta a este usuario? (solo entonces se borra; un usuario con otra oficina se queda)."""
    for child, col in _children(conn, "user"):
        if conn.execute(text(f'SELECT 1 FROM "{child}" WHERE "{col}" = :u LIMIT 1'), {"u": user_id}).first():
            return False
    return True


def cleanup_expired_offices(session: Session, now: Optional[datetime] = None) -> dict[str, int]:
    """Borra las oficinas de demo VENCIDAS con todo lo que cuelga de ellas, y a sus dueños si quedan sin
    nada. Devuelve cuántas filas se borraron por tabla. Solo toca filas con `demo_expires_at` vencida:
    una oficina real (NULL) nunca entra, esté o no encendido `DEMO_MODE`."""
    now = now or _now()
    expired = session.exec(
        select(Firm).where(Firm.demo_expires_at != None).where(Firm.demo_expires_at < now)  # noqa: E711
    ).all()
    tour = set(_tour_ids(session))
    expired = [f for f in expired if f.id not in tour]   # la visita guiada no se borra por ningún camino
    counts: dict[str, int] = {}
    conn = session.connection()
    firebase_uids: list[str] = []
    if expired:
        firm_ids = [f.id for f in expired]
        owners = {f.owner_user_id for f in expired}
        _delete(conn, "firm", firm_ids, counts)
        orphans = [u for u in owners if _user_is_orphan(conn, u)]
        if orphans:
            firebase_uids = [r[0] for r in conn.execute(
                text('SELECT firebase_uid FROM "user" WHERE id = ANY(:ids)'), {"ids": orphans})]
            _delete(conn, "user", orphans, counts)
    firebase_uids += _drop_stale_visitors(conn, now, counts)
    n = conn.execute(text("DELETE FROM democodeattempt WHERE created_at < :t"),
                     {"t": now - timedelta(days=1)}).rowcount
    if n:
        counts["democodeattempt"] = n
    session.commit()   # la BASE primero: un fallo de Firebase nunca deja la limpieza a medias
    firebase_uids = [u for u in firebase_uids if not u.startswith("demo:")]   # los dueños de sistema
    if firebase_uids and DEMO_MODE:
        counts.update(delete_anonymous_firebase_users(firebase_uids))
    if counts:
        log.info("demo: limpieza (%d oficinas vencidas) → %s", len(expired), counts)
    return counts


def _tour_ids(session: Session) -> list[int]:
    return list(session.exec(select(DemoOffice.firm_id).where(DemoOffice.kind == "tour")).all())


def _drop_stale_visitors(conn, now: datetime, counts: dict[str, int], batch: int = 50) -> list[str]:
    """Los visitantes de la visita guiada no tienen oficina, pero sí su fila en `user` (la crea el login).
    Pasados `DEMO_TTL_DAYS`, se borran los que no tienen correo (sesiones anónimas: una cuenta real siempre
    lo tiene, así que esto no alcanza a nadie de verdad, ni en una base mal apuntada) y de los que ya nada
    cuelga. Devuelve sus uid de Firebase."""
    rows = conn.execute(text("""
        SELECT id, firebase_uid FROM "user"
        WHERE email IS NULL AND created_at < :t AND firebase_uid NOT LIKE 'demo:%'
        ORDER BY id LIMIT :n
    """), {"t": now - timedelta(days=DEMO_TTL_DAYS), "n": batch}).fetchall()
    stale = [(uid, fb) for uid, fb in rows if _user_is_orphan(conn, uid)]
    if stale:
        _delete(conn, "user", [u for u, _ in stale], counts)
    return [fb for _, fb in stale]


def _firebase():
    """`firebase_admin.auth` ya inicializado (de `auth.py`). Aparte para poder reemplazarlo en pruebas."""
    import auth
    return auth.firebase_auth()


def delete_anonymous_firebase_users(uids: list[str]) -> dict[str, int]:
    """DEUDA #18 (4-oct): borra en FIREBASE los usuarios de las oficinas de demo vencidas.

    🔴 Demo y producción comparten el proyecto de Firebase (`contabu-prod`). Un visitante que entró al
    demo con GOOGLE usa su cuenta real: borrarla le quitaría el acceso a producción. Por eso se borra un
    usuario de Firebase SOLO si no tiene NINGUNA identidad vinculada (`provider_data` vacío — "the list
    may be empty", doc oficial del Admin SDK), ni correo, ni teléfono. Un usuario así no puede volver a
    entrar por ningún camino: es una sesión anónima abandonada. La regla no depende de suponer cómo
    Firebase marca a los anónimos; depende de que borrarlo no le quite el acceso a nadie.

    Va DESPUÉS del commit de la base y cada usuario en su propio try: un fallo de Firebase (red, permiso
    de la cuenta de servicio, usuario ya borrado) se anota en el log y NO rompe la limpieza.
    """
    out = {"firebase_deleted": 0, "firebase_kept_not_anonymous": 0, "firebase_errors": 0}
    try:
        fb = _firebase()
    except Exception:
        log.exception("demo: no se pudo iniciar firebase-admin; usuarios de Firebase sin borrar")
        out["firebase_errors"] = len(uids)
        return out
    for uid in uids:
        try:
            u = fb.get_user(uid)
            if u.provider_data or u.email or u.phone_number:
                out["firebase_kept_not_anonymous"] += 1     # p. ej. entró al demo con Google
                continue
            fb.delete_user(uid)
            out["firebase_deleted"] += 1
        except Exception as e:
            if type(e).__name__ == "UserNotFoundError":    # ya no existe: nada que borrar
                continue
            log.warning("demo: no se pudo borrar el usuario de Firebase %s: %s", uid, e)
            out["firebase_errors"] += 1
    return out


def cleanup_quietly(session: Session) -> None:
    """La limpieza que corre sola en el demo, al nacer cada oficina nueva: el demo solo crece cuando
    alguien entra, y cada entrada barre lo vencido. Un error aquí jamás impide entrar al demo."""
    if not DEMO_MODE:
        return
    try:
        cleanup_expired_offices(session)
    except Exception:
        log.exception("demo: la limpieza falló (se reintenta en la próxima visita)")
        session.rollback()


def status(session: Session) -> dict:
    kinds = dict(session.exec(select(DemoOffice.kind, func.count(DemoOffice.id)).group_by(DemoOffice.kind)).all())
    codes = dict(session.exec(select(DemoAccess.code_name, func.count(DemoAccess.id))
                              .group_by(DemoAccess.code_name)).all())
    return {"tour_firm_id": tour_firm_id(session), "offices": kinds, "pool_ready": pool_ready(session),
            "pool_size": DEMO_POOL_SIZE, "users_with_code": codes}


_USAGE = """uso (siempre con DEMO_MODE=true y el DATABASE_URL de la base del DEMO):
  .venv/bin/python demo.py tour [--no-review]   crea la visita guiada y guarda la revisión y las reglas propuestas (UNA vez)
  .venv/bin/python demo.py tour-proposals       solo el ④ sobre la visita que ya existe (guarda sus reglas propuestas)
  .venv/bin/python demo.py pool                 rellena las oficinas en espera hasta DEMO_POOL_SIZE
  .venv/bin/python demo.py status               qué hay: la visita, la piscina, los códigos canjeados
  .venv/bin/python demo.py cleanup              borra lo vencido"""


if __name__ == "__main__":
    cmd, args = (sys.argv[1] if len(sys.argv) > 1 else ""), sys.argv[2:]
    if cmd not in {"tour", "tour-proposals", "pool", "status", "cleanup"} or (args and args != ["--no-review"]):
        sys.exit(_USAGE)
    if not DEMO_MODE:
        sys.exit("Se niega: DEMO_MODE no está encendido (esto es solo para la base del demo).")
    logging.basicConfig(level=logging.INFO)
    from db import engine
    with Session(engine) as s:
        if cmd == "tour":
            # La revisión de 3 libros pasa el tope POR OFICINA del demo (20 por defecto): aquí no aplica.
            os.environ.setdefault("AGENT_DAILY_CALLS", "200")
            f = build_tour(s, review="--no-review" not in args)
            print(f"visita guiada lista: oficina {f.id}")
        elif cmd == "tour-proposals":
            # Solo el ④ sobre una visita que ya existe (la creada antes del 23-oct no guardaba sus propuestas).
            os.environ.setdefault("AGENT_DAILY_CALLS", "200")
            tid = tour_firm_id(s)
            if tid is None:
                sys.exit("No hay visita guiada todavía: primero `demo.py tour`.")
            draft_tour(s, s.get(Firm, tid), _system_user(s, TOUR_UID))
        elif cmd == "pool":
            print(refill_pool(s))
        elif cmd == "status":
            print(status(s))
        else:
            print(cleanup_expired_offices(s) or "nada vencido")
