"""
ContaBu — el despliegue de DEMO del concurso (FASE 13, área `Firm / bookkeepers`).

Ver `docs/DEMO Y ENTREGA.md` → "🔑 Acceso". Cada visitante entra con **login anónimo de Firebase**, el
alta de siempre (`GET /api/me`) le crea **su propia oficina**, y en el demo se le **siembran los libros**
con el generador de datos de Movimientos. Nadie le rompe el demo a nadie: cada jurado trabaja su copia.

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
"""

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text
from sqlmodel import Session, select

from models import Company, Firm, User

log = logging.getLogger("contabu.demo")

DEMO_MODE = os.getenv("DEMO_MODE", "").lower() in {"1", "true", "yes"}
DEMO_TTL_DAYS = int(os.getenv("DEMO_TTL_DAYS", "7"))

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

def seed_visitor_books(session: Session, firm: Firm, owner: User) -> list[Company]:
    """Los libros con que arranca la oficina de un visitante del demo.

    Llama al generador de datos de Movimientos, con la firma acordada en el 📬 Buzón (3-oct):

        demo_data.seed_demo_books(session, firm, owner) -> list[Company]

    que crea los libros DENTRO de `firm` (firm_id=firm.id, user_id=owner.id) con sus transacciones,
    reglas, notas e historial, y hace `session.flush()` — NO `commit()`: el commit lo hace `/api/me`, así
    oficina y libros nacen juntos o no nace nada.

    Si el generador todavía no existe (o falla), el visitante recibe un libro vacío como cualquier
    cuenta nueva: el demo queda pobre, pero no roto. Se deja en el log para que se note.
    """
    try:
        import demo_data  # área Movimientos
    except ImportError:
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
            log.exception("demo_data.seed_demo_books falló; el visitante recibe un libro vacío")
    book = Company(name="My Business", sector="", user_id=owner.id, firm_id=firm.id)
    session.add(book)
    session.flush()
    return [book]


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
    if not expired:
        return {}
    firm_ids = [f.id for f in expired]
    owners = {f.owner_user_id for f in expired}
    counts: dict[str, int] = {}
    conn = session.connection()
    _delete(conn, "firm", firm_ids, counts)
    orphans = [u for u in owners if _user_is_orphan(conn, u)]
    firebase_uids: list[str] = []
    if orphans:
        firebase_uids = [r[0] for r in conn.execute(
            text('SELECT firebase_uid FROM "user" WHERE id = ANY(:ids)'), {"ids": orphans})]
        _delete(conn, "user", orphans, counts)
    session.commit()   # la BASE primero: un fallo de Firebase nunca deja la limpieza a medias
    if firebase_uids and DEMO_MODE:
        counts.update(delete_anonymous_firebase_users(firebase_uids))
    log.info("demo: limpieza de %d oficinas vencidas → %s", len(firm_ids), counts)
    return counts


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


if __name__ == "__main__":
    # Limpieza manual (o desde un cron): DEMO_MODE=true .venv/bin/python demo.py cleanup
    if sys.argv[1:] != ["cleanup"]:
        sys.exit("uso: DEMO_MODE=true .venv/bin/python demo.py cleanup")
    if not DEMO_MODE:
        sys.exit("Se niega: DEMO_MODE no está encendido (la limpieza es solo para la base del demo).")
    from db import engine
    with Session(engine) as s:
        print(cleanup_expired_offices(s) or "nada vencido")
