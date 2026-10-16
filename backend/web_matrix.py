"""
ContaBu — EL EXPERIMENTO DEL ② VERIFICADOR (Tavily) (FASE 11, Auditoría / IA · 16-oct-2026).

¿El ② acierta? ¿Cuándo vale la búsqueda avanzada? ¿Cuánto cuesta un libro? ¿Ayuda al equipo? El ② NO se
cambia mientras se mide. Las respuestas (`demo_data.DEMO_TRUTH_MERCHANT_TYPES`) las escribe Movimientos
ANTES de buscar: sin ellas, este script no busca nada en vivo.

⚠️ La tabla de palabras del ② se ajustó el 8-oct mirando resultados de Sparkle: ese libro NO es una prueba
limpia. Rivera y Casa Lupita sí. Las tablas los muestran por separado.

Partes:
  --accuracy   (1 y 2) Cada comercio de gasto de los 3 libros: búsqueda básica Y avanzada, 2 veces cada una.
               Las 3 políticas (solo básica · básica y avanzada si no sale alta · solo avanzada) se calculan
               después sobre ESOS datos, sin buscar otra vez. ~6 créditos por comercio.
  --team       (3, 4 y 5) El equipo ganador (`agent_models` por defecto) con el ② EN VIVO y por ORÁCULO, 2
               corridas cada uno, con una segunda revisión (memoria de la oficina) para contar sus créditos.
  --tables     Todo, desde el .jsonl.
Uso (el resto lo corre Jose en su terminal):
    nohup .venv/bin/python web_matrix.py --accuracy --team > private/web_matrix.log 2>&1 &
    .venv/bin/python web_matrix.py --tables
Topes: 480 créditos de Tavily y $2.8 de Nebius (los de Jose: 500 y $3): se para antes y lo dice.
Todo en `backend/private/web_matrix.jsonl` (una línea por búsqueda o por corrida).
"""

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

OUT = Path(__file__).with_name("private") / "web_matrix.jsonl"
CREDIT_CAP, USD_CAP = 480, 2.8
CLEAN_BOOKS = {"Rivera HVAC LLC", "Casa Lupita Taqueria"}   # Sparkle: la tabla de palabras se ajustó con él
RANK = {"high": 3, "medium": 2, "low": 1, None: 0}


def _read() -> list[dict]:
    return [json.loads(x) for x in OUT.read_text().splitlines() if x.strip()] if OUT.exists() else []


def _write(row: dict) -> None:
    with OUT.open("a") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def spent() -> tuple[int, float]:
    rows = _read()
    credits = sum(r.get("credits", 0) for r in rows if r.get("part") == "accuracy" and not r.get("oracle"))
    credits += sum(r["total_credits"] for r in rows if r.get("part") == "team" and r.get("web") == "live")
    usd = sum(r.get("total", {}).get("usd", 0) for r in rows if r.get("part") == "team")
    return credits, usd


def truth_table() -> dict:
    import demo_data
    t = getattr(demo_data, "DEMO_TRUTH_MERCHANT_TYPES", None)
    if not t:
        raise SystemExit("⛔ Todavía no está DEMO_TRUTH_MERCHANT_TYPES en demo_data.py (lo escribe Movimientos ANTES "
                         "de buscar). No se busca nada.")
    return t


def truth_for(table: dict, book: str, description: str):
    d = (description or "").upper()
    keys = [k for k in table.get(book, {}) if d.startswith(k.upper())]
    return table[book][max(keys, key=len)] if keys else None


def merchants(session) -> list[dict]:
    """Los comercios de GASTO que el ② buscaría en los 3 libros (los que su filtro no salta), uno por consulta."""
    import demo_data
    import merchant_verifier
    from models import Firm, Transaction, User
    from sqlmodel import select
    owner = User(firebase_uid=f"__web_{time.time_ns()}__", email="web@test.local")
    session.add(owner); session.flush()
    firm = Firm(name="WEB MATRIX", owner_user_id=owner.id)
    session.add(firm); session.flush()
    out: dict[str, dict] = {}
    for b in demo_data.seed_demo_books(session, firm, owner):
        txs = session.exec(select(Transaction).where(Transaction.company_id == b.id)).all()
        by_fp = defaultdict(list)
        full = {(t.description or "").strip().upper() for t in txs}
        for t in txs:
            by_fp[merchant_verifier.query_for(t.description, full).upper()].append(t)
        for c in merchant_verifier.candidates(txs):
            if c.skip:
                continue
            m = out.setdefault(c.fingerprint, {"fingerprint": c.fingerprint, "query": c.query, "books": {}})
            m["books"][b.name] = sorted({t.description for t in by_fp.get(c.fingerprint, [])})[:3]
    return list(out.values())


def accuracy(oracle: bool = False, limit: int = 0) -> int:
    import demo_web_evidence
    import merchant_verifier
    from db import engine
    from sqlmodel import Session
    table = None if oracle else truth_table()
    orc = {r["fingerprint"]: r for r in demo_web_evidence.load()} if oracle else None
    with Session(engine) as s:
        try:
            ms = merchants(s)
        finally:
            s.rollback()
    if limit:
        ms = ms[:limit]
    done = {(r["fingerprint"], r["level"], r["rep"]) for r in _read() if r.get("part") == "accuracy" and bool(r.get("oracle")) == oracle}
    print(f"PARTE 1 · {len(ms)} comercios de gasto × (básica + avanzada) × 2", flush=True)
    for rep in (1, 2):
        for m in ms:
            for level in ("basic", "advanced"):
                if (m["fingerprint"], level, rep) in done:
                    continue
                credits, _usd = spent()
                if not oracle and credits + 2 > CREDIT_CAP:
                    print(f"⛔ TOPE de créditos: llevo {credits}; me detengo.", flush=True)
                    return 2
                if oracle:
                    o = orc.get(m["fingerprint"]) or {}
                    btype, conf, url, n, used, lat = o.get("business_type"), o.get("confidence"), o.get("source_url"), int(o.get("results_count") or 0), (1 if level == "basic" else 2), 0
                else:
                    merchant_verifier.SENT_QUERIES.append(m["query"])
                    results, used, lat = merchant_verifier._search(m["query"], level)
                    btype, conf, url = merchant_verifier.classify(results, m["query"])
                    n = len(results)
                truth = {b: (truth_for(table, b, descs[0]) if table else None) for b, descs in m["books"].items()}
                _write({"part": "accuracy", "oracle": oracle, "rep": rep, "level": level, "fingerprint": m["fingerprint"],
                        "query": m["query"], "books": sorted(m["books"]), "truth": truth, "business_type": btype,
                        "confidence": conf, "source_url": url, "results": n, "credits": used, "latency_ms": lat,
                        "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
            print(f"  rep {rep} · {m['query']}", flush=True)
    return 0


def team(runs: int = 2) -> int:
    import agent_models
    import team_matrix
    config = agent_models.current()
    for web in ("oracle", "live"):
        have = sum(1 for r in _read() if r.get("part") == "team" and r.get("web") == web)
        for i in range(have, runs):
            credits, usd = spent()
            if credits + 60 > CREDIT_CAP or usd + 0.1 > USD_CAP:
                print(f"⛔ TOPE: llevo {credits} créditos y ${usd:.3f}; me detengo.", flush=True)
                return 2
            r = team_matrix.run_once(config, f"web_{web}", web=web, second_review=True)
            r["part"] = "team"
            r["total_credits"] = sum(b["web"]["credits"] + (b["web"]["second_credits"] or 0) for b in r["books"].values())
            _write(r)
            t = r["total"]
            print(f"PARTE 4 · ② {web} #{i + 1} · errores {t['errors_found']}/{t['errors_planted']} · precisión "
                  f"{100 * t['precision']:.0f}% · Pendiente {100 * t['pending_coverage']:.0f}% · K {t['correct_fix']} · "
                  f"créditos {r['total_credits']} · ${t['usd']:.4f} · privacidad {r['privacy']}", flush=True)
    return 0


# ── Las tablas ───────────────────────────────────────────────────────────────────────────────────

def _verdict(truth, btype) -> str:
    """correcto / incorrecto / sin tipo / (None = no se puede juzgar: ambiguo o sin respuesta)."""
    if truth in (None, "ambiguous"):
        return None
    if btype is None:
        return "correct" if truth == "no_type" else "no_type"
    return "correct" if btype == truth else "wrong"


def tables() -> int:
    rows = _read()
    acc = [r for r in rows if r.get("part") == "accuracy" and not r.get("oracle")]
    if acc:
        print("■ PARTE 1 · ¿ACIERTA? (por nivel de búsqueda y de confianza; correcto / incorrecto / sin tipo)")
        for group, books in (("limpios: Rivera + Casa Lupita", CLEAN_BOOKS), ("Sparkle (no es prueba limpia)", {"Sparkle Cleaning Co."})):
            for level in ("basic", "advanced"):
                c = defaultdict(Counter)
                for r in acc:
                    if r["level"] != level:
                        continue
                    for b in r["books"]:
                        if b in books:
                            v = _verdict(r["truth"].get(b), r["business_type"])
                            if v:
                                c[r["confidence"] or "none"][v] += 1
                line = " · ".join(f"{conf}: {x['correct']}✓ {x['wrong']}✗ {x['no_type']}∅" for conf, x in sorted(c.items(), key=lambda kv: -RANK.get(kv[0] if kv[0] != 'none' else None, 0)))
                hi = c.get("high", Counter())
                print(f"  {group} · {level}: {line}  →  cuando dice ALTA acierta {hi['correct']}/{sum(hi.values())}")
        print("\n■ PARTE 2 · ¿CUÁNDO VALE LA AVANZADA? (los mismos datos; 'usable' = confianza alta o media, la que usan los agentes)")
        by = defaultdict(dict)
        for r in acc:
            by[(r["fingerprint"], r["rep"])][r["level"]] = r
        for name in ("solo básica", "básica + avanzada si no sale alta (hoy)", "solo avanzada"):
            c, credits, total = Counter(), 0, 0
            for (fp, rep), lv in by.items():
                if "basic" not in lv or "advanced" not in lv:
                    continue
                bsc, adv = lv["basic"], lv["advanced"]
                if name.startswith("solo básica"):
                    pick, cost = bsc, bsc["credits"]
                elif name.startswith("solo avanzada"):
                    pick, cost = adv, adv["credits"]
                else:
                    pick = bsc if bsc["confidence"] == "high" or RANK[bsc["confidence"]] >= RANK[adv["confidence"]] else adv
                    cost = bsc["credits"] + (0 if bsc["confidence"] == "high" else adv["credits"])
                credits += cost
                for b in pick["books"]:
                    if b not in CLEAN_BOOKS:
                        continue
                    v = _verdict(pick["truth"].get(b), pick["business_type"])
                    if not v:
                        continue
                    total += 1
                    usable = pick["confidence"] in ("high", "medium")
                    c[v] += 1
                    c["usable_correct"] += usable and v == "correct"
                    c["usable_wrong"] += usable and v == "wrong"
            print(f"  {name}: correctos {c['correct']}/{total} · USABLES correctos {c['usable_correct']} · USABLES "
                  f"incorrectos {c['usable_wrong']} · créditos {credits} (2 repeticiones, los 3 libros)")
    tm = [r for r in rows if r.get("part") == "team"]
    if tm:
        print("\n■ PARTE 3 · ¿CUÁNTO CUESTA UN LIBRO? (política de hoy; oficina nueva → segunda revisión con memoria)")
        for r in tm:
            if r["web"] != "live":
                continue
            print("  " + " · ".join(f"{b}: {x['web']['credits']} → {x['web']['second_credits']} créditos "
                                      f"({x['web']['searched']} buscados; {x['web'].get('deep')})" for b, x in r["books"].items()))
        print("\n■ PARTE 4 · ¿AYUDA AL EQUIPO? (② en vivo contra ② por oráculo)")
        for web in ("oracle", "live"):
            for r in [x for x in tm if x["web"] == web]:
                t = r["total"]
                print(f"  ② {web}: errores {t['errors_found']}/{t['errors_planted']} · precisión {100 * t['precision']:.0f}% · "
                      f"Pendiente {100 * t['pending_coverage']:.0f}% ({100 * t['pending_precision']:.0f}%) · K {t['correct_fix']}/"
                      f"{t['correct_fix_found_with_expected']} · créditos {r['total_credits']} · ${t['usd']:.4f}")
    print("\n■ PARTE 5 · PRIVACIDAD (consultas con un nombre de persona · consultas iguales a una descripción completa)")
    for r in tm:
        print(f"  equipo ② {r['web']}: {r['privacy']}")
    if acc:
        print(f"  parte 1: {len(acc)} consultas · todas son la frase del comercio que arma el código (las mismas que el ② en producción)")
    c, u = spent()
    print(f"\nGASTADO: {c} créditos de Tavily · ${u:.4f} de Nebius")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--accuracy", action="store_true")
    ap.add_argument("--team", action="store_true")
    ap.add_argument("--tables", action="store_true")
    ap.add_argument("--oracle", action="store_true", help="prueba sin red ni créditos (no cuenta como medición)")
    ap.add_argument("--limit", type=int, default=0, help="solo los primeros N comercios (prueba)")
    args = ap.parse_args()
    OUT.parent.mkdir(exist_ok=True)
    rc = 0
    if args.accuracy:
        rc = accuracy(oracle=args.oracle, limit=args.limit) or rc
    if args.team and rc == 0:
        rc = team() or rc
    if args.tables or not (args.accuracy or args.team):
        tables()
    return rc


if __name__ == "__main__":
    sys.exit(main())
