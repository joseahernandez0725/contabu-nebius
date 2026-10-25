"""
ContaBu — LA PRUEBA DEL ② (Tavily) SOBRE EL LIBRO REAL DE 2026 (FASE 11, Auditoría / IA · 24-oct-2026).

Lo que medía `JUECES - TAVILY.md` era solo del demo. Aquí, el libro real recortado a 2026 (`contabu_real_2026`) y la
clave que escribió el bookkeeper SIN ver ninguna búsqueda (`private/real_tavily/plantilla.csv`: el tipo de negocio de
cada comercio que buscaría el ②, de la lista cerrada del código, o `no_type` / `ambiguous`). El mismo método que la
prueba del demo del 16-oct (`web_matrix.py`):

- `accuracy`: cada comercio, búsqueda básica Y avanzada, 2 veces; el código del ② clasifica cada resultado
  (`merchant_verifier.classify`). Las tres políticas (solo básica · básica y, si no sale alta, avanzada — la de hoy ·
  solo avanzada) se calculan DESPUÉS sobre esos datos, sin buscar otra vez.
- `reviews`: "Revisar el libro" de verdad (auditar → ② con su tope de producción → ① otra vez), con la memoria de la oficina
  VACÍA y, en la misma transacción, una segunda revisión con la memoria llena: los créditos de cada una. 2 veces. Todo en una
  transacción que se deshace (el libro no cambia).
- `tables`: las tablas, desde `private/real_tavily/searches.jsonl`.

Privacidad, medida en lo que de verdad se mandó: consultas con un nombre protegido (deben ser 0) y consultas iguales a la
línea completa del banco. 🔴 Datos reales: todo a `private/real_tavily/` (600); en pantalla, solo conteos.

    U=$(grep ^DATABASE_URL .env.real | cut -d= -f2-)
    DATABASE_URL="${U%/*}/contabu_real_2026" .venv/bin/python real_tavily.py accuracy
    DATABASE_URL="${U%/*}/contabu_real_2026" .venv/bin/python real_tavily.py reviews
    .venv/bin/python real_tavily.py tables
"""

import argparse
import csv
import json
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import text
from sqlmodel import Session, select

HERE = Path(__file__).resolve().parent
DIR = HERE / "private" / "real_tavily"
KEY = DIR / "plantilla.csv"
OUT = DIR / "searches.jsonl"
DB = "contabu_real_2026"
COMPANY_ID = 1
CREDIT_CAP = 300     # tope de esta prueba (Jose tiene más; se comprueba el uso de Tavily antes y después)
RANK = {"high": 3, "medium": 2, "low": 1, None: 0}


def _write(row: dict) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(OUT, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.chmod(OUT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read() -> list[dict]:
    return [json.loads(x) for x in OUT.read_text().splitlines() if x.strip()] if OUT.exists() else []


def spent() -> int:
    rows = _read()
    return (sum(r.get("credits", 0) for r in rows if r["part"] == "accuracy")
            + sum(r.get("credits_first", 0) + r.get("credits_second", 0) for r in rows if r["part"] == "review"))


def key() -> dict[str, str]:
    lines = [l for l in KEY.read_text(encoding="utf-8-sig").splitlines() if not l.startswith("#")]
    return {r["comercio"]: r["tipo_jose"].strip() for r in csv.DictReader(lines)}


def _require_db(s: Session) -> None:
    got = s.connection().execute(text("select current_database()")).scalar()
    if got != DB:
        sys.exit(f"⛔ esta prueba corre sobre `{DB}` y la conexión es `{got}`")


def _book(s: Session):
    import merchant_verifier
    from models import Transaction
    txs = s.exec(select(Transaction).where(Transaction.company_id == COMPANY_ID)).all()
    return txs, [c for c in merchant_verifier.candidates(txs) if not c.skip]


def _guard(txs) -> tuple[set, set]:
    """(nombres protegidos, líneas completas del banco), calculados una vez, con la sesión abierta."""
    import merchant_verifier
    return merchant_verifier.protected_names(txs), {(t.description or "").strip().upper() for t in txs if t.description}


def _privacy(queries: list[str], guard: tuple[set, set]) -> dict:
    persons, full = guard
    return {"queries": len(queries), "with_person_name": sum(1 for q in queries if any(p.upper() in q.upper() for p in persons)),
            "equal_full_description": sum(1 for q in queries if q.strip().upper() in full)}


def accuracy(reps: int = 2) -> int:
    import merchant_verifier
    from db import engine
    truth = key()
    with Session(engine) as s:
        try:
            _require_db(s)
            txs, cands = _book(s)
            guard = _guard(txs)
        finally:
            s.rollback()
    missing = [c.query for c in cands if c.query not in truth]
    if missing:
        sys.exit(f"⛔ {len(missing)} comercios del ② no están en la clave: la clave es de otra versión del libro")
    done = {(r["query"], r["level"], r["rep"]) for r in _read() if r["part"] == "accuracy"}
    print(f"accuracy · {len(cands)} comercios × (básica + avanzada) × {reps}", flush=True)
    for rep in range(1, reps + 1):
        for c in cands:
            for level in ("basic", "advanced"):
                if (c.query, level, rep) in done:
                    continue
                if spent() + 2 > CREDIT_CAP:
                    print(f"⛔ TOPE de créditos: llevo {spent()}; me detengo.", flush=True)
                    return 2
                results, used, lat = merchant_verifier._search(c.query, level)
                btype, conf, url = merchant_verifier.classify(results, c.query)
                _write({"part": "accuracy", "rep": rep, "level": level, "query": c.query, "rows": c.rows,
                        "truth": truth[c.query], "business_type": btype, "confidence": conf,
                        "source_domain": (url or "").split("/")[2] if url and "//" in url else None,
                        "results": len(results), "credits": used, "latency_ms": lat,
                        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                        **_privacy([c.query], guard)})
        print(f"  repetición {rep} lista · créditos {spent()}", flush=True)
    return 0


def reviews(n: int = 2) -> int:
    """"Revisar el libro" de verdad (con el tope de producción del ②), memoria vacía y luego llena, n veces."""
    import agents
    import merchant_verifier
    from db import engine
    from models import Company, Finding, MerchantJudgment, MerchantLookup
    for i in range(1, n + 1):
        if spent() + 60 > CREDIT_CAP:
            print(f"⛔ TOPE de créditos: llevo {spent()}; me detengo.", flush=True)
            return 2
        merchant_verifier.ORACLE = None
        merchant_verifier.SENT_QUERIES.clear()
        with Session(engine) as s:
            try:
                _require_db(s)
                s.commit = s.flush  # type: ignore[method-assign]
                company = s.get(Company, COMPANY_ID)
                for model in (MerchantJudgment, Finding):
                    for x in s.exec(select(model).where(model.company_id == COMPANY_ID)).all():
                        s.delete(x)
                for x in s.exec(select(MerchantLookup)).all():
                    s.delete(x)          # la memoria de la oficina empieza vacía
                s.flush()
                out = {}
                for which in ("first", "second"):
                    agents.review(s, company, lang="en")
                    v = merchant_verifier.verify_flagged(s, company)          # el tope de producción
                    if v.finding_ids:
                        agents.review(s, company, lang="en", finding_ids=v.finding_ids)
                    out[which] = v
                txs, _ = _book(s)
                priv = _privacy(list(merchant_verifier.SENT_QUERIES), _guard(txs))
            finally:
                s.rollback()
        f, sec = out["first"], out["second"]
        _write({"part": "review", "run": i, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "merchants_first": len(f.items), "flagged_first": f.flagged, "pending_first": f.pending,
                "searched_first": f.searched, "credits_first": f.credits,
                "searched_second": sec.searched, "from_cache_second": sec.from_cache, "credits_second": sec.credits,
                "by_confidence_first": dict(Counter(x.get("confidence") or "none" for x in f.items)), **priv})
        print(f"revisión {i}: 1ª {f.searched} búsquedas, {f.credits} créditos ({f.flagged} marcados + {f.pending} "
              f"Pendientes) · 2ª {sec.searched} búsquedas, {sec.credits} créditos · privacidad {priv}", flush=True)
    return 0


def _verdict(truth, btype):
    """correcto / incorrecto / sin tipo; None si no se puede juzgar (ambiguous). Igual que `web_matrix._verdict`."""
    if truth in (None, "", "ambiguous"):
        return None
    if btype is None:
        return "correct" if truth == "no_type" else "no_type"
    return "correct" if btype == truth else "wrong"


def tables() -> int:
    rows = _read()
    acc = [r for r in rows if r["part"] == "accuracy"]
    print(f"Créditos gastados: {spent()} (tope {CREDIT_CAP})\n")
    print("■ ¿ACIERTA? por nivel y confianza (correcto ✓ · incorrecto ✗ · sin tipo ∅; los 'ambiguous' de la clave no cuentan)")
    for level in ("basic", "advanced"):
        c = defaultdict(Counter)
        for r in acc:
            if r["level"] != level:
                continue
            v = _verdict(r["truth"], r["business_type"])
            if v:
                c[r["confidence"] or "none"][v] += 1
        line = " · ".join(f"{conf}: {x['correct']}✓ {x['wrong']}✗ {x['no_type']}∅" for conf, x in
                          sorted(c.items(), key=lambda kv: -RANK.get(kv[0] if kv[0] != "none" else None, 0)))
        print(f"  {level}: {line}")
    print("\n■ LAS TRES POLÍTICAS (sobre los mismos datos; 'usable' = confianza alta o media, la que usan los agentes)")
    by = defaultdict(dict)
    for r in acc:
        by[(r["query"], r["rep"])][r["level"]] = r
    for name in ("solo básica", "básica → avanzada si no sale alta (la de hoy)", "solo avanzada"):
        c, credits = Counter(), 0
        for (_q, _rep), lv in by.items():
            if "basic" not in lv or "advanced" not in lv:
                continue
            b, a = lv["basic"], lv["advanced"]
            if name == "solo básica":
                pick, cost = b, b["credits"]
            elif name == "solo avanzada":
                pick, cost = a, a["credits"]
            else:
                pick, cost = (b, b["credits"]) if b["confidence"] == "high" else (a, b["credits"] + a["credits"])
            credits += cost
            v = _verdict(pick["truth"], pick["business_type"])
            if v is None:
                continue
            c[v] += 1
            c["total"] += 1
            usable = pick["confidence"] in ("high", "medium")
            c["usable_correct"] += usable and v == "correct"
            c["usable_wrong"] += usable and v == "wrong"
        print(f"  {name}: correctos {c['correct']}/{c['total']} · USABLES correctos {c['usable_correct']} · USABLES "
              f"incorrectos {c['usable_wrong']} · créditos {credits} (todas las repeticiones)")
    rep_stable = sum(1 for q in {r["query"] for r in acc} for level in ("basic", "advanced")
                     if len({(r["business_type"], r["confidence"]) for r in acc if r["query"] == q and r["level"] == level}) == 1)
    print(f"\n■ Repetibilidad: misma respuesta en las 2 repeticiones en {rep_stable} de {2 * len({r['query'] for r in acc})} "
          f"(comercio × nivel)")
    pr = Counter()
    for r in acc:
        pr["queries"] += 1
        pr["with_person_name"] += r["with_person_name"]
        pr["equal_full_description"] += r["equal_full_description"]
    print(f"■ Privacidad (búsquedas de la prueba): {dict(pr)}")
    rev = [r for r in rows if r["part"] == "review"]
    for r in rev:
        print(f"■ Revisar el libro #{r['run']}: 1ª revisión {r['searched_first']} comercios buscados ({r['flagged_first']} marcados + "
              f"{r['pending_first']} Pendientes), {r['credits_first']} créditos · 2ª revisión {r['searched_second']} buscados, "
              f"{r['credits_second']} créditos · privacidad: {r['queries']} consultas, {r['with_person_name']} con nombre, "
              f"{r['equal_full_description']} iguales a la línea del banco")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=("accuracy", "reviews", "tables"))
    ap.add_argument("--reps", type=int, default=2)
    a = ap.parse_args()
    return {"accuracy": lambda: accuracy(a.reps), "reviews": lambda: reviews(a.reps), "tables": tables}[a.what]()


if __name__ == "__main__":
    sys.exit(main())
