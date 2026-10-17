"""
ContaBu — LA MATRIZ DE MODELOS (FASE 11, Auditoría / IA · 15-oct-2026).

¿Qué modelo de Nemotron, y con o sin razonamiento, conviene a CADA agente? Se varía UN agente a la vez;
los demás quedan como corren hoy (Nano sin razonar; el ⑥ traductor de notas, razonando). Cada combinación
corre 2 veces sobre el PRIMER examen (`demo_data.DEMO_TRUTH_*`); las filas del examen ciego se esconden
en código, como en `measure.py`. La evidencia web es la del oráculo (`demo_web_evidence.json`, cargada de
antemano): 0 créditos de Tavily.

Agentes y su calidad (contada por el código, nunca por el modelo):
- ⑤ "¿personal para este negocio?" (`personal_use`): gastos personales plantados que toca + precisión.
- ⑥ traductor de notas (`note_checks`): errores que tocan sus chequeos + precisión.
- ① revisor: errores que quedan como alerta con el ① + precisión + errores VERDADEROS que llamó legítimos.
- ④ redactor: precisión y cobertura sobre lo Pendiente + el caso de las vacaciones (9 filas, 0 de más).
Y por llamada: tokens de entrada y salida, razonamiento estimado, USD (precio de catálogo), latencia y
cuántas se quedaron sin tokens (`out_of_tokens`).

Uso:
    .venv/bin/python model_matrix.py                       # todo (se detiene antes de pasar de $3)
    .venv/bin/python model_matrix.py --agents personal     # un agente
    .venv/bin/python model_matrix.py --models nano,super --runs 1
    .venv/bin/python model_matrix.py --table matrix.jsonl  # solo la tabla, de un archivo ya medido
Cada corrida siembra los 3 libros del demo en una transacción y la DESHACE: no queda nada en la base.
"""

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

from sqlmodel import Session, select

MODELS = {
    "nano": "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
    "lightning": "nvidia/Nemotron-3_5-Lightning",
    "super": "nvidia/nemotron-3-super-120b-a12b",
    "ultra": "nvidia/Nemotron-3-Ultra-550b-a55b",
}
AGENTS = ("personal", "notes", "reviewer", "drafter")
LABEL = {"reviewer": "① revisor", "drafter": "④ redactor", "personal": "⑤ ¿personal?", "notes": "⑥ traductor"}  # solo para las tablas
AGENT_CALL_NAME = {"personal": "personal_judge", "notes": "note_checker", "reviewer": "reviewer", "drafter": "drafter"}
DEFAULT = {"personal": ("nano", False), "notes": ("nano", True), "reviewer": ("nano", False), "drafter": ("nano", False)}
BUDGET_USD = 2.8   # el tope de Jose es $3: se para antes


def _seed(session):
    import demo_data
    import demo_web_evidence
    from models import Firm, User
    owner = User(firebase_uid=f"__matrix_{time.time_ns()}__", email="matrix@test.local")
    session.add(owner); session.flush()
    firm = Firm(name="MODEL MATRIX", owner_user_id=owner.id)
    session.add(firm); session.flush()
    books = demo_data.seed_demo_books(session, firm, owner)
    demo_web_evidence.seed_lookups(session, firm, include_live=True)   # la evidencia del oráculo, toda
    session.flush()
    return books


def run_once(agent: str, model_key: str, reasoning: bool) -> dict:
    """Una corrida: siembra, corre los agentes (el variado con su combinación), mide y deshace."""
    import agents
    import formula
    import measure
    import note_checks
    import personal_use
    from db import engine
    from models import AgentCall, Transaction
    model = MODELS[model_key]
    dm = {a: (MODELS[k], r) for a, (k, r) in DEFAULT.items()}
    dm[agent] = (model, reasoning)
    out = {"agent": agent, "model": model_key, "reasoning": reasoning, "books": {}}
    with Session(engine) as s:
        try:
            s.commit = s.flush  # type: ignore[method-assign]
            books = _seed(s)
            q = defaultdict(int)
            draft_usage = []
            for b in books:
                tp, te = measure._truth(b.name)
                txs = s.exec(select(Transaction).where(Transaction.company_id == b.id)).all()
                hide = frozenset(measure._blind_ids(txs))
                view = agents.build_view(s, b)
                # Los que NO se varían corren antes, en su configuración de hoy (solo lo que el agente medido necesita).
                if agent in ("notes", "reviewer"):
                    note_checks.refresh(s, b, view, model=dm["notes"][0], reasoning=dm["notes"][1])
                if agent in ("personal", "reviewer"):
                    personal_use.refresh(s, b, view, model=dm["personal"][0], reasoning=dm["personal"][1])
                if agent == "reviewer":
                    agents.review(s, b, lang="en", force=True, model=dm["reviewer"][0], reasoning=dm["reviewer"][1])
                if agent == "drafter" and tp:
                    p = measure.measure_pending(s, b, tp, model=model, reasoning=reasoning, record=True)
                    q["pend_known"] += p["known"]; q["pend_decided"] += p["decided"]; q["pend_right"] += p["right"]
                    q["pend_split"] += p["split_decided"]
                    nc = measure.note_case(formula.BookContext(s, b), p["rows"])
                    if nc:
                        q["note_hit"] += nc["hit"]; q["note_expected"] += nc["expected"]; q["note_extra"] += nc["extra"]
                    continue
                if te:
                    r = measure.measure_review(s, b, te, hide=hide, already_reviewed=True)
                    key = {"personal": "personal", "notes": "notes", "reviewer": "reviewer"}[agent]
                    sc = r[key]
                    q["planted"] += r["planted"]; q["found"] += sc["found"]
                    q["alerts"] += sc["alerts"]; q["true_alerts"] += sc["true_alerts"]
                    if agent == "personal":
                        q["personal_planted"] += sum(1 for e in te if e["type"] == "personal_expense")
                        q["personal_found"] += sum(1 for line in r["personal_seen"] if "=personal/high" in line
                                                   or "=personal/medium" in line)
                    if agent == "reviewer":
                        q["dismissed_true"] += len(r["dismissed_true"])
            # Costo y latencia: las llamadas del agente MEDIDO (las de los demás no cuentan).
            ids = [b.id for b in books]
            calls = s.exec(select(AgentCall).where(AgentCall.company_id.in_(ids),
                                                   AgentCall.agent == AGENT_CALL_NAME[agent])).all()
            out["quality"] = dict(q)
            out["calls"] = len(calls)
            out["prompt_tokens"] = sum(c.prompt_tokens for c in calls)
            out["completion_tokens"] = sum(c.completion_tokens for c in calls)
            out["reasoning_tokens_est"] = sum(c.reasoning_tokens_est for c in calls)
            out["usd"] = round(sum(c.cost_usd or 0 for c in calls), 6)
            out["latency_ms_max"] = max((c.latency_ms for c in calls), default=0)
            out["latency_ms_sum"] = sum(c.latency_ms for c in calls)
            out["out_of_tokens"] = sum(1 for c in calls if c.outcome == "out_of_tokens")
            out["errors"] = sum(1 for c in calls if c.outcome not in ("ok", "out_of_tokens"))
        finally:
            s.rollback()
    return out


def quality_line(r: dict) -> str:
    q = r.get("quality", {})
    pct = lambda a, b: f"{a}/{b}" + (f" ({100 * a / b:.0f}%)" if b else "")
    if r["agent"] == "drafter":
        return (f"Pendiente: precisión {pct(q.get('pend_right', 0), q.get('pend_decided', 0))}, cobertura "
                f"{pct(q.get('pend_decided', 0) - q.get('pend_split', 0), q.get('pend_known', 0))} · vacaciones "
                f"{q.get('note_hit', 0)}/{q.get('note_expected', 0)}, {q.get('note_extra', 0)} de más")
    s = f"encuentra {pct(q.get('found', 0), q.get('planted', 0))} · precisión {pct(q.get('true_alerts', 0), q.get('alerts', 0))}"
    if r["agent"] == "personal":
        s += f" · gastos personales que marca {pct(q.get('personal_found', 0), q.get('personal_planted', 0))}"
    if r["agent"] == "reviewer":
        s += f" · errores verdaderos que llamó legítimos: {q.get('dismissed_true', 0)}"
    return s


def table(rows: list[dict]) -> None:
    by = defaultdict(list)
    for r in rows:
        by[(r["agent"], r["model"], r["reasoning"])].append(r)
    for agent in AGENTS:
        keys = [k for k in by if k[0] == agent]
        if not keys:
            continue
        print(f"\n■ {LABEL[agent]} ({agent})")
        print("  modelo · razona | calidad (corrida 1 | corrida 2) | tokens ent/sal (razon.) por corrida | USD por corrida | latencia máx | sin tokens")
        for k in sorted(keys, key=lambda k: (list(MODELS).index(k[1]), k[2])):
            rs = by[k]
            n = len(rs)
            avg = lambda f: sum(x[f] for x in rs) / n
            print(f"  {k[1]:9} · {'sí' if k[2] else 'no'} | {' | '.join(quality_line(x) for x in rs)} | "
                  f"{avg('prompt_tokens'):.0f}/{avg('completion_tokens'):.0f} ({avg('reasoning_tokens_est'):.0f}) | "
                  f"${avg('usd'):.5f} | {max(x['latency_ms_max'] for x in rs) / 1000:.1f}s | "
                  f"{sum(x['out_of_tokens'] for x in rs)}{' · errores ' + str(sum(x['errors'] for x in rs)) if any(x['errors'] for x in rs) else ''}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agents", default=",".join(AGENTS))
    ap.add_argument("--models", default=",".join(MODELS))
    ap.add_argument("--reasoning", default="no,yes")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--out", default="private/model_matrix.jsonl",
                    help="una línea JSON por corrida (se agrega; `private/` no entra a git)")
    ap.add_argument("--table", help="solo imprimir la tabla de un .jsonl ya medido")
    args = ap.parse_args()
    if args.table:
        table([json.loads(x) for x in Path(args.table).read_text().splitlines() if x.strip()])
        return 0
    out = Path(args.out)
    done = [json.loads(x) for x in out.read_text().splitlines() if x.strip()] if out.exists() else []
    spent = sum(r.get("usd", 0) for r in done)
    combos = [(a, m, r == "yes") for a in args.agents.split(",") for m in args.models.split(",")
              for r in args.reasoning.split(",")]
    for agent, model, reasoning in combos:
        have = sum(1 for r in done if (r["agent"], r["model"], r["reasoning"]) == (agent, model, reasoning))
        for i in range(have, args.runs):
            if spent >= BUDGET_USD:
                print(f"⛔ TOPE: llevo ${spent:.4f}; me detengo antes de pasar de $3.", flush=True)
                table(done)
                return 2
            t0 = time.time()
            try:
                r = run_once(agent, model, reasoning)
            except Exception as e:   # un modelo que no acepta algo no tumba la matriz
                r = {"agent": agent, "model": model, "reasoning": reasoning, "failed": f"{type(e).__name__}: {e}"[:300],
                     "usd": 0, "calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens_est": 0,
                     "latency_ms_max": 0, "latency_ms_sum": 0, "out_of_tokens": 0, "errors": 1, "quality": {}}
            r["wall_s"] = round(time.time() - t0, 1)
            done.append(r)
            spent += r.get("usd", 0)
            with out.open("a") as fh:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            print(f"{agent:8} {model:9} razona={'sí' if reasoning else 'no'} #{i + 1} · {quality_line(r)} · "
                  f"${r.get('usd', 0):.5f} · {r['wall_s']}s · acumulado ${spent:.4f}"
                  + (f" · FALLÓ {r['failed']}" if r.get("failed") else ""), flush=True)
    table(done)
    print(f"\nTOTAL gastado en Nebius por la matriz: ${spent:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
