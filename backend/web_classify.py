"""
ContaBu — EL ② CON NEMOTRON: ¿un modelo lee mejor los resultados de la web que la tabla de palabras? (FASE 11 · 24-oct)

La regla está escrita en `FASE 11.A.md` (24-oct (6)) ANTES de correr. Tres pasos:

- `fetch`: los resultados CRUDOS de Tavily, una sola vez, básica y avanzada, guardados en `private/web_classify/raw.jsonl`
  (600). Dos conjuntos: `demo` (los comercios de `DEMO_TRUTH_MERCHANT_TYPES` de los libros limpios, Rivera y Casa Lupita,
  como `web_matrix.py` del 16-oct; se siembra el demo en una transacción que se deshace) y `real` (los 25 de la clave de
  Jose, `private/real_tavily/plantilla.csv`, sobre `contabu_real_2026`). Con las consultas de HOY (el "&" restituido, la
  ciudad). La privacidad se mide sobre lo que sale del libro: la consulta y la región.
- `classify`: la tabla de palabras y los 10 candidatos (5 modelos, con y sin razonar) × n sobre ESOS resultados, con la
  forma de buscar de hoy (básica; la avanzada solo si la básica no sale alta). Cero créditos.
- `score`: las tablas y la regla.

    .venv/bin/python web_classify.py fetch --set demo
    DATABASE_URL="${U%/*}/contabu_real_2026" .venv/bin/python web_classify.py fetch --set real
    .venv/bin/python web_classify.py classify --n 3
    .venv/bin/python web_classify.py score
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIR = HERE / "private" / "web_classify"
RAW = DIR / "raw.jsonl"
RUNS = DIR / "runs.jsonl"
JOSE_KEY = HERE / "private" / "real_tavily" / "plantilla.csv"
CLEAN = ("Rivera HVAC LLC", "Casa Lupita Taqueria")
MODELS = ("nano", "lightning", "super", "ultra", "vl")
CONFIGS = [(m, r) for m in MODELS for r in (False, True)]
USD_CAP = 6.0
# La reclasificación de Jose (24-oct), hecha DESPUÉS de ver los resultados: estos dos cuentan como correctos así.
JOSE_RECLASS = {"HOME DEPOT": "hardware_store", "7-ELEVEN": "convenience_store"}


def _append(path: Path, row: dict) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(DIR, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.chmod(path, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []


def _norm(q: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (q or "").upper())


def jose_key() -> dict[str, str]:
    lines = [l for l in JOSE_KEY.read_text(encoding="utf-8-sig").splitlines() if not l.startswith("#")]
    return {r["comercio"]: r["tipo_jose"].strip() for r in csv.DictReader(lines)}


# ── fetch ──────────────────────────────────────────────────────────────────────────────────────────

def _fetch_one(set_name: str, query: str, truth, region, extra: dict) -> None:
    import merchant_verifier as mv
    have = {(r["set"], r["query"], r["level"]) for r in _read(RAW)}
    for level in ("basic", "advanced"):
        if (set_name, query, level) in have:
            continue
        results, used, lat = mv._search(query, level)
        _append(RAW, {"set": set_name, "query": query, "level": level, "truth": truth, "region": region,
                      "results": [{k: r.get(k) for k in ("title", "content", "url", "score")} for r in results],
                      "credits": used, "latency_ms": lat, "ts": time.strftime("%Y-%m-%d %H:%M:%S"), **extra})


def fetch(set_name: str) -> int:
    import merchant_verifier as mv
    from db import engine
    from sqlmodel import Session, select
    from models import Transaction
    if set_name == "demo":
        import web_matrix
        table = web_matrix.truth_table()
        with Session(engine) as s:
            try:
                ms = web_matrix.merchants(s)
                from models import Company
                regions = {}
                for c in s.exec(select(Company).where(Company.name.in_(CLEAN))).all():
                    txs = s.exec(select(Transaction).where(Transaction.company_id == c.id)).all()
                    regions[c.name] = mv.book_region(txs)
            finally:
                s.rollback()
        todo = []
        for m in ms:
            book = next((b for b in CLEAN if b in m["books"]), None)
            if book is None:
                continue
            t = web_matrix.truth_for(table, book, m["books"][book][0])
            if t is not None:
                todo.append((m["query"], t, regions.get(book), {"book": book}))
    else:
        from sqlalchemy import text
        key = jose_key()
        with Session(engine) as s:
            try:
                if s.connection().execute(text("select current_database()")).scalar() != "contabu_real_2026":
                    sys.exit("⛔ el conjunto real corre sobre contabu_real_2026")
                txs = s.exec(select(Transaction).where(Transaction.company_id == 1)).all()
                cands = [c for c in mv.candidates(txs) if not c.skip]
                skipped_city = [c for c in mv.candidates(txs) if c.skip == "city"]
                region = mv.book_region(txs)
                persons = mv.protected_names(txs)
                full = {(t.description or "").strip().upper() for t in txs if t.description}
            finally:
                s.rollback()
        todo, unmatched = [], []
        for c in cands:
            old = next((k for k in key if _norm(k) == _norm(c.query)), None) \
                or next((k for k in key if _norm(c.query).startswith(_norm(k))), None)
            if old is None:
                unmatched.append(c.query)
                continue
            todo.append((c.query, key[old], region, {"key_query": old, "query_changed": old != c.query}))
        print(f"real: {len(todo)} con clave · sin clave {len(unmatched)} · consulta cambiada {sum(x[3]['query_changed'] for x in todo)} · "
              f"saltados por ciudad {len(skipped_city)} · privacidad: con nombre de persona "
              f"{sum(1 for q, *_ in todo if any(p.upper() in q.upper() for p in persons))}, iguales a la línea del banco "
              f"{sum(1 for q, *_ in todo if q.upper() in full)} · región {'sí' if region else 'no'}", flush=True)
    print(f"{set_name}: {len(todo)} comercios × (básica + avanzada)", flush=True)
    for q, t, reg, extra in todo:
        _fetch_one(set_name, q, t, reg, extra)
    print(f"  créditos de {set_name}: {sum(r['credits'] for r in _read(RAW) if r['set'] == set_name)}", flush=True)
    return 0


# ── classify ───────────────────────────────────────────────────────────────────────────────────────

def _merchants() -> dict[tuple, dict]:
    out: dict[tuple, dict] = {}
    for r in _read(RAW):
        m = out.setdefault((r["set"], r["query"]), {"set": r["set"], "query": r["query"], "truth": r["truth"],
                                                    "region": r["region"], "key_query": r.get("key_query")})
        m[r["level"]] = r["results"]
    return out


def _policy(classify_one, m) -> dict:
    """La forma de buscar de hoy: la básica; si no sale alta, la avanzada (y se queda la avanzada)."""
    b = classify_one(m.get("basic") or [], m)
    if b["confidence"] == "high":
        return {**b, "level": "basic", "calls": b.get("calls", 0)}
    a = classify_one(m.get("advanced") or [], m)
    return {**a, "level": "advanced", "calls": b.get("calls", 0) + a.get("calls", 0),
            "usd": b.get("usd", 0) + a.get("usd", 0), "latency_ms": b.get("latency_ms", 0) + a.get("latency_ms", 0),
            "fallback": b.get("fallback", False) or a.get("fallback", False)}


def _table_one(results, m) -> dict:
    import merchant_verifier as mv
    t, c, _u = mv.classify(results, m["query"])
    return {"type": t, "confidence": c, "usd": 0.0, "latency_ms": 0, "calls": 0}


def _llm_one(model, reasoning):
    import merchant_verifier as mv

    def one(results, m):
        if not results:
            return {"type": None, "confidence": None, "usd": 0.0, "latency_ms": 0, "calls": 0}
        t, c, _u, info = mv.classify_llm(results, m["query"], m["region"], model=mv_model(model), reasoning=reasoning)
        u = info.get("usage")
        return {"type": t, "confidence": c, "usd": (u.cost_usd or 0.0) if u else 0.0, "latency_ms": u.latency_ms if u else 0,
                "calls": 1, "fallback": info.get("fallback", False), "same_merchant": info.get("same_merchant")}
    return one


def mv_model(key: str) -> str:
    import agent_models
    return agent_models.MODEL_IDS[key]


def _spent() -> float:
    return sum(r.get("usd", 0) for r in _read(RUNS))


def classify(n: int) -> int:
    ms = list(_merchants().values())
    done = Counter((r["classifier"], r["run"]) for r in _read(RUNS))
    if not done[("table", 1)]:
        picks = {f"{m['set']}|{m['query']}": _policy(_table_one, m) for m in ms}
        _append(RUNS, {"classifier": "table", "run": 1, "picks": picks, "usd": 0.0})
        print("tabla de palabras: lista", flush=True)
    for model, reasoning in CONFIGS:
        name = f"{model}{'+r' if reasoning else ''}"
        for run in range(1, n + 1):
            if done[(name, run)]:
                continue
            if _spent() >= USD_CAP:
                print(f"⛔ TOPE: ${_spent():.4f}; me detengo.", flush=True)
                return 2
            one = _llm_one(model, reasoning)
            t0 = time.time()
            with ThreadPoolExecutor(max_workers=8) as ex:
                results = list(ex.map(lambda m: (f"{m['set']}|{m['query']}", _policy(one, m)), ms))
            picks = dict(results)
            usd = sum(p.get("usd", 0) for p in picks.values())
            _append(RUNS, {"classifier": name, "run": run, "picks": picks, "usd": usd, "wall_s": round(time.time() - t0, 1)})
            sc = score_run(picks)
            print(f"{name} #{run}: demo usables {sc['demo']['usable_correct']}✓ {sc['demo']['usable_wrong']}✗ · real (con la "
                  f"reclasificación) {sc['real_adj']['usable_correct']}✓ {sc['real_adj']['usable_wrong']}✗ · fallas→tabla "
                  f"{sum(1 for p in picks.values() if p.get('fallback'))} · ${usd:.4f}", flush=True)
    return 0


# ── score ──────────────────────────────────────────────────────────────────────────────────────────

def _verdict(truth, btype):
    if truth in (None, "", "ambiguous"):
        return None
    if btype is None:
        return "correct" if truth == "no_type" else "no_type"
    return "correct" if btype == truth else "wrong"


def score_run(picks: dict) -> dict:
    ms = _merchants()
    out = {}
    now = jose_key()  # la plantilla de HOY: con las 4 filas corregidas el 24-oct (columna corregido_24oct)
    for name, set_name, adjust in (("demo", "demo", False), ("real_raw", "real", False), ("real_adj", "real", True),
                                   ("real_fix", "real", "fix")):
        c = Counter()
        for (s, q), m in ms.items():
            if s != set_name:
                continue
            truth = m["truth"]
            if adjust == "fix":
                truth = now.get(m.get("key_query") or q, truth)
            if adjust and (m.get("key_query") or q) in JOSE_RECLASS:
                truth = JOSE_RECLASS[m.get("key_query") or q]
            p = picks.get(f"{s}|{q}")
            v = _verdict(truth, p["type"]) if p else None
            if v is None:
                continue
            c["judged"] += 1
            c[v] += 1
            usable = p["confidence"] in ("high", "medium")
            c["usable_correct"] += usable and v == "correct"
            c["usable_wrong"] += usable and v == "wrong"
            c["high"] += p["confidence"] == "high"
            c["high_correct"] += p["confidence"] == "high" and v == "correct"
            c["usd"] += p.get("usd", 0)
            c["latency_ms"] += p.get("latency_ms", 0)
            c["fallback"] += bool(p.get("fallback"))
        out[name] = c
    return out


def score() -> int:
    runs = _read(RUNS)
    raw = _read(RAW)
    print(f"Créditos de la bajada: {sum(r['credits'] for r in raw)} · Nebius: ${_spent():.4f}\n")
    by = defaultdict(list)
    for r in runs:
        by[r["classifier"]].append(score_run(r["picks"]))
    table = by.get("table", [None])[0]
    for key, label in (("demo", "DEMO (libros limpios)"),
                       ("real_fix", "REAL, la clave CORREGIDA (24-oct: 3 → government_tax, 1 → hardware_store) + la reclasificación"),
                       ("real_adj", "REAL, con la reclasificación de Jose"),
                       ("real_raw", "REAL, la clave tal cual")):
        n = table[key]["judged"] if table else 0
        print(f"■ {label} · {n} comercios juzgados (los 'ambiguous' no cuentan)")
        for name in ["table"] + [f"{m}{'+r' if r else ''}" for m, r in CONFIGS]:
            if name not in by:
                continue
            ss = [x[key] for x in by[name]]
            fmt = lambda k: " · ".join(str(x[k]) for x in ss)
            per = (sum(x["usd"] for x in ss) / len(ss) / max(1, n), sum(x["latency_ms"] for x in ss) / len(ss) / max(1, n) / 1000)
            print(f"  {name:12} correctos {fmt('correct'):12} · usables ✓ {fmt('usable_correct'):12} ✗ {fmt('usable_wrong'):10} · "
                  f"alta {fmt('high_correct')}/{fmt('high')} · fallas→tabla {fmt('fallback'):8} · ${per[0]:.5f} y {per[1]:.1f}s por comercio")
        print()
    # La regla
    if table:
        print("■ LA REGLA (en las claves demo, real con la reclasificación y real corregida: más usables ✓ que la tabla y no más usables ✗)")
        ok = []
        for name in [f"{m}{'+r' if r else ''}" for m, r in CONFIGS]:
            if name not in by:
                continue
            ss = by[name]
            beats = all(min(x[k]["usable_correct"] for x in ss) > table[k]["usable_correct"] and
                        max(x[k]["usable_wrong"] for x in ss) <= table[k]["usable_wrong"] for k in ("demo", "real_adj"))
            if beats:
                ok.append(name)
        print(f"  le ganan a la tabla en sus 3 corridas: {', '.join(ok) or 'ninguno'}")
        if ok:
            best = {k: max(min(x[k]["usable_correct"] for x in by[nm]) for nm in ok) for k in ("demo", "real_adj")}
            print(f"  (entre ellos, el desempate: la PEOR del candidato contra la MEJOR de los demás; si empatan, el más barato)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=("fetch", "classify", "score"))
    ap.add_argument("--set", choices=("demo", "real"))
    ap.add_argument("--n", type=int, default=3)
    a = ap.parse_args()
    if a.what == "fetch":
        return fetch(a.set)
    if a.what == "classify":
        return classify(a.n)
    return score()


if __name__ == "__main__":
    sys.exit(main())
