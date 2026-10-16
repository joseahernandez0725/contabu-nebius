"""
ContaBu — EL MEJOR MODELO PARA CADA AGENTE, EN EQUIPO (FASE 11, Auditoría / IA · 15-oct-2026).

La matriz de la mañana (`model_matrix.py`) comparó cada agente contra un ContaBu "todo Nano". Esta lo compara
contra LA MEJOR COMBINACIÓN: cada corrida es el flujo real de "Revisar el libro" (auditar = código + notas
+ ⑤ + ① → el ② busca solo lo marcado → el ① vuelve a juzgar → el ④ propone), sobre el PRIMER examen y lo
Pendiente, con Tavily por el ORÁCULO (`demo_web_evidence.json`: 0 créditos; la memoria de la oficina empieza
vacía). Las filas del examen ciego se esconden en código. La REGLA del "mejor" está en `FASE 11.A.md`
(escrita antes de medir) y aquí, en `beats()`.

Pasos (pedido de Jose del 15-oct):
  1. El ④ con Lightning y con Ultra (sin razonar), traductor en Ultra, ① y ⑤ en Nano: 3 corridas cada uno.
  2. La ganadora es la BASE (sus 3 corridas del paso 1 son las de la base: misma configuración).
  3. Un agente a la vez (①, ④, ⑤, traductor): Nano/Lightning/Super/Ultra × razonar sí/no, 2 corridas.
  4. Si una variación le gana a la base, pasa a ser la base y se corre 3 veces.
  5. Con lo final congelado, el examen ciego UNA vez (`--blind-final`).

Uso:
    .venv/bin/python team_matrix.py --plan              # los pasos 1-4 (se detiene antes de pasar de $5)
    .venv/bin/python team_matrix.py --blind-final       # el paso 5 (UNA vez, con la base final)
    .venv/bin/python team_matrix.py --tables            # las tablas, desde el .jsonl
Todo queda en `backend/private/team_matrix.jsonl` (una línea por corrida; qué es cada campo: FASE 11.A).
Cada corrida siembra los 3 libros en una transacción y la DESHACE: no queda nada en la base.
"""

import argparse
import fcntl
import json
import subprocess
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

OUT = Path(__file__).with_name("private") / "team_matrix.jsonl"
STATE = Path(__file__).with_name("private") / "team_matrix_state.json"
BUDGET_USD = 4.8          # el tope de Jose es $5: se para antes (hay corridas en vuelo)
PARALLEL = 3
AGENTS = ("reviewer", "drafter", "personal", "notes")
MODELS = ("nano", "lightning", "super", "ultra")
CALL_NAME = {"reviewer": "reviewer", "drafter": "drafter", "personal": "personal_judge", "notes": "note_checker"}
BOOK_SHORT = {"Rivera HVAC LLC": "Rivera", "Sparkle Cleaning Co.": "Sparkle", "Casa Lupita Taqueria": "Lupita"}


# ── Una corrida ──────────────────────────────────────────────────────────────────────────────────

def run_once(config: dict, tag: str, blind: bool = False) -> dict:
    import agent_models
    import demo_data
    import demo_web_evidence
    import formula
    import measure
    import merchant_verifier
    from db import engine
    from models import AgentCall, Firm, MerchantLookup, Transaction, User
    from sqlmodel import Session, select
    agent_models.OVERRIDES.clear()
    agent_models.OVERRIDES.update({a: (c["model"], c["reasoning"]) for a, c in config.items()})
    merchant_verifier.ORACLE = {r["fingerprint"]: r for r in demo_web_evidence.load()}
    t0 = time.time()
    out = {"tag": tag, "exam": "blind" if blind else "first", "config": config, "books": {}, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    with Session(engine) as s:
        try:
            s.commit = s.flush  # type: ignore[method-assign]
            owner = User(firebase_uid=f"__team_{time.time_ns()}__", email="team@test.local")
            s.add(owner); s.flush()
            firm = Firm(name="TEAM MATRIX", owner_user_id=owner.id)
            s.add(firm); s.flush()
            books = demo_data.seed_demo_books(s, firm, owner)
            for m in s.exec(select(MerchantLookup).where(MerchantLookup.firm_id == firm.id)).all():
                s.delete(m)          # la memoria de la oficina empieza vacía: el ② "busca" (oráculo)
            s.flush()
            for b in books:
                tp, te = measure._truth(b.name, blind=blind)
                txs = s.exec(select(Transaction).where(Transaction.company_id == b.id)).all()
                hide = frozenset() if blind else frozenset(measure._blind_ids(txs))
                other = frozenset(measure._exam_ids(txs, "DEMO_TRUTH_ERRORS")) if blind else frozenset()
                web = measure.web_flow(s, b)
                pend = measure.measure_pending(s, b, tp, record=True)
                nc = measure.note_case(formula.BookContext(s, b), pend["rows"]) or {}
                rev = measure.measure_review(s, b, te, draft_rows=pend["rows"], hide=hide, other_exam=other,
                                             already_reviewed=True)
                calls = s.exec(select(AgentCall).where(AgentCall.company_id == b.id)).all()
                agents = {}
                for a, name in CALL_NAME.items():
                    cs = [c for c in calls if c.agent == name]
                    agents[a] = {"calls": len(cs), "models": sorted({agent_models.short(c.model) for c in cs}),
                                 "prompt_tokens": sum(c.prompt_tokens for c in cs),
                                 "completion_tokens": sum(c.completion_tokens for c in cs),
                                 "reasoning_tokens_est": sum(c.reasoning_tokens_est for c in cs),
                                 "usd": round(sum(c.cost_usd or 0 for c in cs), 6),
                                 "latency_ms_max": max((c.latency_ms for c in cs), default=0),
                                 "latency_ms_sum": sum(c.latency_ms for c in cs),
                                 "out_of_tokens": sum(1 for c in cs if c.outcome == "out_of_tokens"),
                                 "errors": sum(1 for c in cs if c.outcome not in ("ok", "out_of_tokens"))}
                cf = rev["correct_fix"]
                out["books"][BOOK_SHORT.get(b.name, b.name)] = {
                    "errors_planted": rev["team"]["planted"], "errors_found": rev["team"]["found"],
                    "alerts": rev["team"]["alerts"], "true_alerts": rev["team"]["true_alerts"],
                    "found_keys": rev["found_keys"],
                    "pending_known": pend["known"], "pending_decided": pend["decided"] - pend["split_decided"],
                    "pending_decided_all": pend["decided"], "pending_right": pend["right"],
                    "vacation_expected": nc.get("expected", 0), "vacation_hit": nc.get("hit", 0),
                    "vacation_extra": nc.get("extra", 0),
                    "correct_fix_found_with_expected": cf["found_with_expected"], "correct_fix": cf["correct"],
                    "correct_fix_by_source": cf["by_source"],
                    "own": {"reviewer": {**rev["reviewer"], "dismissed_true": len(rev["dismissed_true"])},
                            "personal": {**rev["personal"], "seen": rev["personal_seen"]},
                            "notes": rev["notes"], "drafter_categorized": rev["drafter"]},
                    "auditor_alone": rev["auditor"],
                    "web": {"searched": web["searched"], "credits_oracle": web["credits"]},
                    "agents": agents,
                    "usd": round(sum(x["usd"] for x in agents.values()), 6),
                }
        finally:
            s.rollback()
    out["total"] = total(out["books"])
    out["wall_s"] = round(time.time() - t0, 1)
    return out


def total(books: dict) -> dict:
    keys = ("errors_planted", "errors_found", "alerts", "true_alerts", "pending_known", "pending_decided",
            "pending_decided_all", "pending_right", "vacation_expected", "vacation_hit", "vacation_extra",
            "correct_fix_found_with_expected", "correct_fix", "usd")
    t = {k: sum(b[k] for b in books.values()) for k in keys}
    t["usd"] = round(t["usd"], 6)
    t["precision"] = t["true_alerts"] / t["alerts"] if t["alerts"] else 0
    t["pending_coverage"] = t["pending_decided"] / t["pending_known"] if t["pending_known"] else 0
    t["pending_precision"] = t["pending_right"] / t["pending_decided_all"] if t["pending_decided_all"] else 1
    agg = defaultdict(lambda: Counter())
    for b in books.values():
        for a, x in b["agents"].items():
            agg[a].update({k: v for k, v in x.items() if isinstance(v, (int, float))})
    t["agents"] = {a: {**dict(c), "latency_ms_max": max(b["agents"][a]["latency_ms_max"] for b in books.values())}
                   for a, c in agg.items()}
    return t


# ── La regla del "mejor" (FASE 11.A, escrita antes de medir) ────────────────────────────────────

def stats(runs: list[dict]) -> dict:
    n = len(runs)
    T = [r["total"] for r in runs]
    return {"n": n, "E": sum(t["errors_found"] for t in T) / n, "P": sum(t["precision"] for t in T) / n,
            "C": 100 * sum(t["pending_coverage"] for t in T) / n, "PP": sum(t["pending_precision"] for t in T) / n,
            "K": sum(t["correct_fix"] for t in T) / n, "USD": sum(t["usd"] for t in T) / n,
            "vac_ok": all(t["vacation_hit"] == t["vacation_expected"] and t["vacation_extra"] <= 3 for t in T)}


def eligible(s: dict) -> bool:
    return s["P"] >= 0.85 and s["PP"] >= 0.95 and s["vac_ok"]


def beats(a: dict, b: dict) -> tuple[bool, str]:
    """¿La configuración `a` le gana a `b`? (con la razón)."""
    if not eligible(a):
        return False, "no cumple los requisitos"
    if not eligible(b):
        return True, "la otra no cumple los requisitos"
    if abs(a["E"] - b["E"]) > 1:
        return a["E"] > b["E"], f"errores {a['E']:.1f} vs {b['E']:.1f}"
    if abs(a["C"] - b["C"]) > 3:
        return a["C"] > b["C"], f"cobertura de Pendientes {a['C']:.0f} vs {b['C']:.0f} puntos"
    if abs(a["K"] - b["K"]) > 1:
        return a["K"] > b["K"], f"corrección correcta {a['K']:.1f} vs {b['K']:.1f}"
    if a["USD"] <= 0.9 * b["USD"]:
        return True, f"empate en calidad; ${a['USD']:.4f} vs ${b['USD']:.4f} (≥10 % más barata)"
    return False, f"empate en calidad y no es ≥10 % más barata (${a['USD']:.4f} vs ${b['USD']:.4f})"


# ── El plan ──────────────────────────────────────────────────────────────────────────────────────

def _read() -> list[dict]:
    return [json.loads(x) for x in OUT.read_text().splitlines() if x.strip()] if OUT.exists() else []


def _key(config: dict) -> str:
    return " · ".join(f"{a}={config[a]['model']}{'+r' if config[a]['reasoning'] else ''}" for a in AGENTS)


def _spent() -> float:
    return sum(r["total"]["usd"] for r in _read() if "total" in r)


def _launch(config: dict, tag: str, n: int, blind: bool = False) -> None:
    _launch_many([(config, tag, n)], blind=blind)


def _launch_many(items: list[tuple], blind: bool = False) -> None:
    """Corridas que faltan de varias configuraciones [(config, tag, n)], cada una en un proceso aparte, de a
    `PARALLEL` a la vez. Lo que ya está en el .jsonl no se repite (se puede retomar)."""
    done = _read()
    jobs = []
    for config, tag, n in items:
        have = sum(1 for r in done if r.get("tag") == tag and _key(r["config"]) == _key(config))
        jobs += [(config, tag)] * max(0, n - have)

    def one(job):
        config, tag = job
        if _spent() >= BUDGET_USD:
            return "budget"
        p = subprocess.run([sys.executable, __file__, "--one", json.dumps(config), "--tag", tag]
                           + (["--blind"] if blind else []), capture_output=True, text=True)
        if p.returncode != 0:
            print(f"   ⚠️ corrida falló: {p.stderr[-400:]}", flush=True)
        return p.returncode
    with ThreadPoolExecutor(PARALLEL) as ex:
        list(ex.map(one, jobs))


def _runs(config: dict, tags: tuple) -> list[dict]:
    return [r for r in _read() if r.get("exam") == "first" and r.get("tag") in tags and _key(r["config"]) == _key(config)]


def plan() -> int:
    def cfg(**over):
        c = {"reviewer": {"model": "nano", "reasoning": False}, "drafter": {"model": "lightning", "reasoning": False},
             "personal": {"model": "nano", "reasoning": False}, "notes": {"model": "ultra", "reasoning": False}}
        for a, (m, r) in over.items():
            c[a] = {"model": m, "reasoning": r}
        return c
    # Paso 1
    A, B = cfg(drafter=("lightning", False)), cfg(drafter=("ultra", False))
    print("PASO 1 · ④ Lightning vs Ultra (3 corridas cada uno)", flush=True)
    _launch_many([(A, "step1", 3), (B, "step1", 3)])
    sa, sb = stats(_runs(A, ("step1",))), stats(_runs(B, ("step1",)))
    win, why = beats(sb, sa)
    base = B if win else A
    print(f"   → base: ④ {'Ultra' if win else 'Lightning'} ({why})", flush=True)
    history = [{"step": 1, "base": _key(base), "why": why}]
    base_tags = ("step1", "base")
    # Pasos 3-4
    for agent in AGENTS:
        if _spent() >= BUDGET_USD:
            break
        print(f"PASO 3 · varía {agent} (2 corridas por variación) · base = {_key(base)}", flush=True)
        variations = []
        for m in MODELS:
            for r in (False, True):
                c = json.loads(json.dumps(base)); c[agent] = {"model": m, "reasoning": r}
                if _key(c) != _key(base):
                    variations.append(c)
        _launch_many([(c, f"vary_{agent}", 2) for c in variations])
        sbase = stats(_runs(base, base_tags))
        best, best_s, best_why = None, sbase, ""
        for c in variations:
            sc = stats(_runs(c, (f"vary_{agent}",)))
            if not sc["n"]:
                continue
            ok, why = beats(sc, best_s)
            if ok:
                best, best_s, best_why = c, sc, why
        if best is not None:
            print(f"   → cambia {agent}: {best[agent]} ({best_why}); paso 4: la nueva base, 3 corridas", flush=True)
            base = best
            base_tags = ("base",)
            _launch(base, "base", 3)
            history.append({"step": f"3:{agent}", "base": _key(base), "why": best_why})
        else:
            print(f"   → {agent} se queda como está", flush=True)
            history.append({"step": f"3:{agent}", "base": _key(base), "why": "ninguna variación le ganó a la base"})
    STATE.write_text(json.dumps({"final": base, "history": history, "spent": _spent()}, ensure_ascii=False, indent=1))
    print(f"\nBASE FINAL: {_key(base)} · gastado ${_spent():.4f}", flush=True)
    return 0


def blind_final() -> int:
    st = json.loads(STATE.read_text())
    if any(r.get("exam") == "blind" for r in _read()):
        print("⛔ El examen ciego ya se corrió una vez: no se repite.")
        return 2
    _launch(st["final"], "blind_final", 1, blind=True)
    tables()
    return 0


# ── Las tablas ───────────────────────────────────────────────────────────────────────────────────

def tables() -> int:
    rows = _read()
    by = defaultdict(list)
    for r in rows:
        by[(r["exam"], r["tag"], _key(r["config"]))].append(r)
    pct = lambda x: f"{100 * x:.0f}%"
    for (exam, tag, key), rs in sorted(by.items(), key=lambda kv: (kv[0][0] != "first", kv[0][1], kv[0][2])):
        s = stats(rs)
        print(f"\n■ [{exam}] {tag} · {key} · {s['n']} corrida(s)")
        print(f"  EQUIPO: errores {s['E']:.1f}/{rs[0]['total']['errors_planted']} · precisión {pct(s['P'])} · Pendiente: "
              f"cobertura {s['C']:.0f}% precisión {pct(s['PP'])} · vacaciones {'ok' if s['vac_ok'] else 'NO'} · "
              f"corrección correcta {s['K']:.1f} · ${s['USD']:.4f}/corrida · {'cumple' if eligible(s) else 'NO CUMPLE'}")
        for r in rs:
            t = r["total"]
            per = " | ".join(f"{b}: {x['errors_found']}/{x['errors_planted']}, {x['true_alerts']}/{x['alerts']}, "
                             f"pend {x['pending_decided']}/{x['pending_known']}, K {x['correct_fix']}/{x['correct_fix_found_with_expected']}"
                             for b, x in r["books"].items())
            ag = " ".join(f"{a}:{v.get('calls', 0)}ll/${v.get('usd', 0):.4f}/{v.get('latency_ms_max', 0) / 1000:.0f}s"
                          f"{'/OOT' + str(v['out_of_tokens']) if v.get('out_of_tokens') else ''}" for a, v in t["agents"].items())
            print(f"    · {per} · vac {t['vacation_hit']}/{t['vacation_expected']}+{t['vacation_extra']} · {ag} · {r['wall_s']}s")
    if STATE.exists():
        st = json.loads(STATE.read_text())
        print("\nHISTORIA DE LA BASE:")
        for h in st["history"]:
            print(f"  paso {h['step']}: {h['base']} — {h['why']}")
    print(f"\nGASTADO: ${_spent():.4f}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--blind-final", action="store_true")
    ap.add_argument("--tables", action="store_true")
    ap.add_argument("--one", help=argparse.SUPPRESS)
    ap.add_argument("--tag", default="manual")
    ap.add_argument("--blind", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    OUT.parent.mkdir(exist_ok=True)
    if args.one:
        r = run_once(json.loads(args.one), args.tag, blind=args.blind)
        with OUT.open("a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        t = r["total"]
        print(f"{args.tag} · {_key(r['config'])} · errores {t['errors_found']}/{t['errors_planted']} · "
              f"precisión {100 * t['precision']:.0f}% · pend {100 * t['pending_coverage']:.0f}% · ${t['usd']:.4f} · {r['wall_s']}s")
        return 0
    if args.plan:
        return plan()
    if args.blind_final:
        return blind_final()
    return tables()


if __name__ == "__main__":
    sys.exit(main())
