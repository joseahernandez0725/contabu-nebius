"""
ContaBu — las verificaciones del ② de los LIBROS DEL DEMO, guardadas una vez (FASE 11, Auditoría / IA ·
13-oct-2026). Deuda #24.

Los 3 libros del demo (`demo_data.py`) son inventados y siempre iguales, así que lo que el ② verificador
encuentra en internet sobre sus comercios también. Se buscó UNA vez con Tavily y se guardó en
`demo_web_evidence.json`. Cada visitante recibe esas verificaciones en la caché de SU oficina al nacer:
el botón "Verificar comercios" sale de la caché, sin gastar créditos.

No cruza nada entre oficinas reales: es un dato sintético que se COPIA a cada oficina del demo (la caché
sigue siendo por oficina). Solo se guarda el dato estructurado (tipo de la lista cerrada, confianza,
enlace), como en cualquier búsqueda.

Completar (busca SOLO los comercios que faltan en el archivo: 1 crédito de Tavily por comercio):
    .venv/bin/python demo_web_evidence.py --refresh
Rehacer todo (~40 créditos; solo si cambia el clasificador de `merchant_verifier`):
    .venv/bin/python demo_web_evidence.py --refresh --all
Comprobar que el archivo cubre todos los comercios buscables del demo (sin red):
    .venv/bin/python demo_web_evidence.py --check
"""

import json
import pathlib
import sys

from sqlmodel import Session, select

from models import Firm, MerchantLookup

DATA = pathlib.Path(__file__).with_name("demo_web_evidence.json")
_FIELDS = ("fingerprint", "query", "status", "business_type", "confidence", "source_url", "source_domain",
           "results_count")


def load() -> list[dict]:
    return json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else []


# TAVILY EN VIVO EN EL DEMO (decisión de Jose, 15-oct; fila de Entregables en el Buzón): estos 3 comercios
# NO se siembran, para que la primera "Revisar el libro" de cada visitante haga búsquedas REALES. Elegidos
# porque el ⑤ los marca como posible gasto personal en todas las corridas medidas (14-oct) y porque Tavily
# los identifica con confianza ALTA en la búsqueda básica (5 resultados): ~1 crédito cada uno, ~3 por
# visitante (hasta 9 si alguno pidiera la avanzada). Netflix (Rivera), 24 Hour Fitness y Safeway (Sparkle).
LIVE_IN_DEMO = {"NETFLIX", "24 HOUR FITNESS", "SAFEWAY"}


def seed_lookups(session: Session, firm: Firm, include_live: bool = False) -> int:
    """Copia las verificaciones del demo a la caché de ESTA oficina (sin las que ya tenga). Solo `add`:
    el `flush`/`commit` es de quien llama (el generador del demo). Devuelve cuántas sembró. Sin
    `include_live`, deja fuera `LIVE_IN_DEMO` (se buscan en vivo); la medición y el `--refresh` las piden."""
    have = set(session.exec(select(MerchantLookup.fingerprint).where(MerchantLookup.firm_id == firm.id)).all())
    n = 0
    for row in load():
        if row["fingerprint"] in have or (not include_live and row["fingerprint"] in LIVE_IN_DEMO):
            continue
        session.add(MerchantLookup(firm_id=firm.id, credits=0, **{k: row.get(k) for k in _FIELDS}))
        n += 1
    return n


def _seed_books(session: Session):
    import demo_data
    from models import User
    owner = User(firebase_uid="__demo_web_evidence__", email="demo-web@test.local")
    session.add(owner); session.flush()
    firm = Firm(name="DEMO WEB EVIDENCE", owner_user_id=owner.id)
    session.add(firm); session.flush()
    return firm, demo_data.seed_demo_books(session, firm, owner)


def refresh(everything: bool = False) -> None:
    """Busca de verdad (Tavily) los comercios de los 3 libros y guarda el resultado. Sin `everything`,
    lo que ya está en el archivo se siembra primero y no se vuelve a buscar. Todo dentro de una
    transacción que se DESHACE: en la base no queda nada; solo el archivo."""
    import merchant_verifier
    from db import engine
    with Session(engine) as s:
        try:
            firm, books = _seed_books(s)
            if not everything:
                seed_lookups(s, firm, include_live=True)
                s.flush()
            rows, credits = {}, 0
            for b in books:
                r = merchant_verifier.verify(s, b, record=False, limit=50)
                credits += r.credits
            for m in s.exec(select(MerchantLookup).where(MerchantLookup.firm_id == firm.id)).all():
                rows[m.fingerprint] = {k: getattr(m, k) for k in _FIELDS}
        finally:
            s.rollback()
    DATA.write_text(json.dumps(sorted(rows.values(), key=lambda r: r["fingerprint"]), ensure_ascii=False, indent=1)
                    + "\n", encoding="utf-8")
    print(f"{len(rows)} comercios guardados en {DATA.name} · {credits} créditos de Tavily")


def check() -> int:
    """Sin red: ¿el archivo cubre cada comercio que el ② buscaría en los libros del demo?"""
    import merchant_verifier
    from db import engine
    from models import Transaction
    have = {r["fingerprint"] for r in load()}
    missing = []
    with Session(engine) as s:
        try:
            _firm, books = _seed_books(s)
            for b in books:
                txs = s.exec(select(Transaction).where(Transaction.company_id == b.id)).all()
                missing += [f"{b.name}: {c.query}" for c in merchant_verifier.candidates(txs)
                            if not c.skip and c.fingerprint not in have]
        finally:
            s.rollback()
    print(f"{len(have)} comercios en el archivo · faltan {len(missing)}")
    for m in missing:
        print("   falta:", m)
    return 1 if missing else 0


if __name__ == "__main__":
    if "--refresh" in sys.argv:
        refresh(everything="--all" in sys.argv)
    elif "--check" in sys.argv:
        sys.exit(check())
    else:
        print(__doc__)
