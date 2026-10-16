"""
ContaBu — LA MEDICIÓN de la fórmula del libro y su LÍNEA BASE SIN IA (FASE 11, Auditoría / IA ·
4-oct-2026). Ver `docs/FASE 11.md` → Actualización 3-oct → "📏 La medición".

La pregunta (la que hace el jurado de Nebius): ¿las filas que propone el agente reproducen lo que el
bookkeeper corrigió a mano, sin romper lo que estaba bien?

- **Separación por tiempo:** se aprende de las correcciones de movimientos ANTERIORES a la fecha de
  corte y se mide con las POSTERIORES. Así no se mide memorización.
- **La línea base (sin IA):** por cada huella de comercio corregida a mano 2 o más veces hacia la
  misma categoría, una fila "palabra clave = ese comercio → esa categoría". Es lo que haría alguien
  contando a mano. **El agente se gana su lugar solo si reproduce MÁS correcciones que esto, con
  IGUAL o MENOS filas cambiadas mal.**
- Todo se cuenta aquí, con el motor real (`formula.decide`), nunca con el modelo.

Uso:
    .venv/bin/python measure.py --demo                 # siembra los 3 libros del demo en una
                                                       # transacción, mide y la DESHACE (no deja nada)
    .venv/bin/python measure.py --company 12           # un libro existente (solo lectura)
    .venv/bin/python measure.py --demo --cutoff 2026-08-15
    .venv/bin/python measure.py --demo --agent         # + el agente ④ REAL (Nemotron), contra la misma
                                                       # línea base; mide tokens, costo y privacidad
    .venv/bin/python measure.py --demo --agent --reasoning   # el mismo, dejándolo razonar
    .venv/bin/python measure.py --demo --audit         # donde la IA APORTA (13-oct): el ④ sobre lo Pendiente
                                                       # y el ① sobre errores plantados, contra respuestas
                                                       # escritas ANTES (demo_data.DEMO_TRUTH_*, Movimientos)
    .venv/bin/python measure.py --demo --audit --web oracle   # Tavily REDISEÑADO (14-oct): la memoria de la
                                                       # oficina empieza VACÍA y el ② busca solo lo que la
                                                       # auditoría marcó; las búsquedas las contesta lo
                                                       # guardado del demo (0 créditos reales, cuenta los que
                                                       # costarían). `--web live` = Tavily de verdad.
                                                       # Sin `--web` (seeded) = antes: todo sembrado.
    .venv/bin/python measure.py --demo --audit --blind # lo mismo contra el SEGUNDO examen, a ciegas (14-oct):
                                                       # DEMO_TRUTH_ERRORS_BLIND, que la F11 no abre antes de
                                                       # medir. Sin --blind, las filas de ese examen se
                                                       # ESCONDEN en código (no cuentan ni se imprimen).
"""

import argparse
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from sqlmodel import Session, select

import formula
from formula import BookContext, FormulaRow
from models import CategorizationChange, Company, Transaction
from rules import merchant_signature, suggest_keyword

DEFAULT_CUTOFF = date(2026, 9, 1)   # demo: julio–agosto para aprender, septiembre para medir
MIN_REPEATS = 2                      # la línea base pide al menos 2 correcciones iguales

# Casos de NOTA plantados en los datos del demo (`demo_data.DEMO_NOTES`). Lo esperado se escribe
# AQUÍ, antes de correr al agente: qué movimientos tienen que terminar dónde. La línea base, por
# construcción, no puede resolverlos (no sabe de vacaciones): ahí está el valor propio del agente.
NOTE_CASES = {
    "Rivera HVAC LLC": {
        "note": "vacation in Europe Aug 3–17",
        "contains": "STARBUCKS",
        "from": date(2026, 8, 3), "to": date(2026, 8, 17),
        "expect": ("NON_PL", "Owner Draw"),
    },
}


# El ② VERIFICADOR: el tipo de negocio que DEBE salir para cada comercio de Sparkle (el caso del demo:
# descripciones crípticas SQ*/TST*/PAYPAL* y el gimnasio camuflado). Escrito el 8-oct ANTES de la
# primera búsqueda. Las personas (Zelle) no están: no se buscan nunca.
VERIFIER_EXPECTED = {
    "Sparkle Cleaning Co.": {
        "SHELL OIL": "gas_station", "EL FAROLITO": "restaurant", "COSTCO WHSE": "warehouse_club",
        "JANITEX SUPPLY": "cleaning_supplies", "NEXT INSURANCE": "insurance", "ULINE": "packaging_shipping",
        "T-MOBILE PCS": "telecom", "24 HOUR FITNESS": "gym_fitness", "BAYCITY UNIFORMS": "uniforms_apparel",
        "ZOROTOOLS": "industrial_supplies",
    },
}


@dataclass
class Correction:
    """Un movimiento corregido a mano: la VERDAD es como lo dejó el bookkeeper."""
    tx: Transaction
    truth: tuple                      # (account_type, category)
    changes: int                      # filas `via=manual` del historial sobre este movimiento


@dataclass
class Score:
    corrected: int = 0                # movimientos corregidos a mano en el periodo de medición
    correction_rows: int = 0          # filas del historial de esos movimientos
    reproduced: int = 0               # movimientos que la fórmula deja directo en la verdad
    reproduced_rows: int = 0          # correcciones que se habrían evitado
    wrong: int = 0                    # filas que la fórmula mueve a una categoría equivocada
    newly_categorized: int = 0        # Pendientes que la fórmula categorizaría (sin verdad: informativo)
    wrong_examples: list = field(default_factory=list)


def corrections(session: Session, company_id: int) -> list[Correction]:
    """Los movimientos que el bookkeeper corrigió a mano y siguen así (`source="manual"`)."""
    rows = session.exec(select(CategorizationChange).where(
        CategorizationChange.company_id == company_id, CategorizationChange.via == "manual")).all()
    per_tx = Counter(r.transaction_id for r in rows)
    # Una corrección TRAMPA (`reason="planted_mistake"`, Movimientos 8-oct: el gimnasio camuflado) no es
    # verdad: si el agente la "reproduce", aprendió el error. No cuenta para ninguno de los dos.
    trap = {r.transaction_id for r in rows if (r.reason or "") == "planted_mistake"}
    out = []
    for tx_id, n in per_tx.items():
        tx = session.get(Transaction, tx_id)
        if tx is None or tx.source != "manual" or tx.account_type is None or not tx.category or tx_id in trap:
            continue
        out.append(Correction(tx=tx, truth=(tx.account_type.value, tx.category), changes=n))
    return out


def baseline_rows(learn: list[Correction]) -> list[FormulaRow]:
    """LA LÍNEA BASE SIN IA: una fila por huella de comercio corregida ≥ MIN_REPEATS veces hacia la
    MISMA categoría (y sin empate con otra). Sin ventana, sin prioridad: lo más simple que funciona."""
    by_merchant: dict[str, list[Correction]] = defaultdict(list)
    for c in learn:
        sig = merchant_signature(c.tx.description)
        if sig:
            by_merchant[sig].append(c)
    rows = []
    for sig, group in sorted(by_merchant.items()):
        counts = Counter(c.truth for c in group).most_common()
        (truth, n), runner_up = counts[0], (counts[1][1] if len(counts) > 1 else 0)
        if n < MIN_REPEATS or n == runner_up:
            continue
        example = next(c for c in group if c.truth == truth).tx
        keyword = suggest_keyword(example.description, example.description_extra) or sig
        rows.append(FormulaRow(
            keyword=keyword, account_type=truth[0], category=truth[1],
            explanation=f"baseline: corrected {n}x to {truth[1]}"))
    return rows


def score(ctx: BookContext, rows: list[FormulaRow], test: list[Correction], cutoff: date) -> Score:
    """Examina `rows` contra el periodo de medición con el motor real."""
    rules = formula.with_rows(ctx.rules, rows, ctx.company.id)
    s = Score(corrected=len(test), correction_rows=sum(c.changes for c in test))
    for c in test:
        d = formula.decide(rules, c.tx)
        if (d.account_type, d.category) == c.truth:
            s.reproduced += 1
            s.reproduced_rows += c.changes
        elif d.source == "rule" and formula.decide(ctx.rules, c.tx) != d:
            # La fórmula nueva lo tomó y lo mandó a otro lado: habría que corregirlo igual (o peor).
            s.wrong += 1
            s.wrong_examples.append(f"{c.tx.description} → {d.category} (era {c.truth[1]})")
    corrected_ids = {c.tx.id for c in test}
    for t in ctx.txs:
        if t.date < cutoff or t.id in corrected_ids or t.source == "manual":
            continue
        before, after = formula.decide(ctx.rules, t), formula.decide(rules, t)
        if before == after:
            continue
        if before.source is None:
            s.newly_categorized += 1           # era Pendiente: no hay verdad contra qué comparar
        else:
            s.wrong += 1                       # estaba decidido y aceptado; la fórmula lo movería
            if len(s.wrong_examples) < 5:
                s.wrong_examples.append(f"{t.description} → {after.category} (era {before.category})")
    return s


def note_case(ctx: BookContext, rows: list[FormulaRow]) -> dict | None:
    """¿Las filas resuelven el caso de nota plantado del libro? 100 % de las esperadas y 0 de más."""
    case = NOTE_CASES.get(ctx.company.name)
    if case is None:
        return None
    rules = formula.with_rows(ctx.rules, rows, ctx.company.id)
    expected = {t.id for t in ctx.txs
                if case["contains"] in (t.description or "").upper() and case["from"] <= t.date <= case["to"]}
    hit = extra = 0
    extras: list[str] = []
    for t in ctx.txs:
        if t.source == "manual":
            continue
        d = formula.decide(rules, t)
        goes = (d.account_type, d.category) == case["expect"]
        if t.id in expected and goes:
            hit += 1
        elif t.id not in expected and goes and formula.decide(ctx.rules, t) != d:
            extra += 1
            extras.append(f"{t.date} {t.description}")
    return {"note": case["note"], "expected": len(expected), "hit": hit, "extra": extra, "extra_examples": extras[:6]}


def measure_book(session: Session, company: Company, cutoff: date) -> dict:
    ctx = BookContext(session, company)
    all_corr = corrections(session, company.id)
    learn = [c for c in all_corr if c.tx.date < cutoff]
    test = [c for c in all_corr if c.tx.date >= cutoff]
    rows = baseline_rows(learn)
    checked = formula.check_rows([r.model_dump(mode="json") for r in rows], ctx)
    valid = [c.row for c in checked if c.ok]
    return {
        "book": company.name, "learn": len(learn), "test": len(test),
        "rows": [(r.keyword, r.category) for r in valid],
        "rejected": [(c.raw.get("keyword"), c.errors) for c in checked if not c.ok],
        "score": score(ctx, valid, test, cutoff),
        "note_case": note_case(ctx, valid),
    }


def measure_agent(session: Session, company: Company, cutoff: date, *, reasoning: bool, model=None) -> dict:
    """El agente ④ REAL sobre el mismo libro y el mismo corte: solo ve el pasado (`cutoff`), propone,
    el validador filtra y se puntúa igual que la línea base. No se guarda nada (`record=False`)."""
    import agents
    ctx = BookContext(session, company)
    test = [c for c in corrections(session, company.id) if c.tx.date >= cutoff]
    res = agents.draft(session, company, cutoff=cutoff, record=False, reasoning=reasoning, model=model)
    rows = [p.row for p in res.proposals]
    return {
        "book": company.name, "outcome": res.outcome, "usage": res.usage,
        "rows": [(r.keyword, r.valid_from, r.valid_to, r.category) for r in rows],
        "rejected": [(rj["raw"].get("keyword") if isinstance(rj["raw"], dict) else rj["raw"], rj["errors"])
                     for rj in res.rejected],
        "score": score(ctx, rows, test, cutoff), "note_case": note_case(ctx, rows),
    }


def _print_agent(results: list[dict], base: list[dict], privacy: dict) -> None:
    print("AGENTE ④ (Nemotron) · mismo corte, mismo puntaje\n")
    tot, btot = Score(), Score()
    cost = 0.0
    p_tok = c_tok = r_tok = 0
    proposed = rejected = 0
    for r, b in zip(results, base):
        s, u = r["score"], r["usage"]
        print(f"■ {r['book']}  — resultado: {r['outcome']}")
        for kw, vf, vt, cat in r["rows"]:
            window = f" [{vf} → {vt}]" if vf or vt else ""
            print(f"    fila propuesta: {kw!r}{window} → {cat}")
        for kw, errs in r["rejected"]:
            print(f"    fila RECHAZADA: {kw!r} {errs}")
        print(f"    reproduce {s.reproduced}/{s.corrected} ({s.reproduced_rows}/{s.correction_rows} filas) · "
              f"cambiadas mal: {s.wrong} · Pendientes que categorizaría: {s.newly_categorized}"
              f"   [línea base: {b['score'].reproduced}/{b['score'].corrected}, mal {b['score'].wrong}]")
        for ex in s.wrong_examples:
            print(f"      ✗ {ex}")
        if r["note_case"]:
            n = r["note_case"]
            print(f"    caso de nota ({n['note']}): {n['hit']}/{n['expected']} filas esperadas · {n['extra']} de más")
            for ex in n.get("extra_examples", []):
                print(f"      + de más: {ex}")
        if u:
            print(f"    costo: {u.prompt_tokens} tokens de entrada / {u.completion_tokens} de salida "
                  f"(razonamiento≈{u.reasoning_tokens_est}) · ${u.cost_usd or 0:.6f} · {u.latency_ms} ms")
            cost += u.cost_usd or 0
            p_tok += u.prompt_tokens; c_tok += u.completion_tokens; r_tok += u.reasoning_tokens_est
        proposed += len(r["rows"]) + len(r["rejected"]); rejected += len(r["rejected"])
        for k in ("corrected", "reproduced", "wrong", "newly_categorized"):
            setattr(tot, k, getattr(tot, k) + getattr(s, k))
            setattr(btot, k, getattr(btot, k) + getattr(b["score"], k))
    beats = tot.reproduced > btot.reproduced and tot.wrong <= btot.wrong
    print(f"\nTOTAL AGENTE · reproduce {tot.reproduced}/{tot.corrected} · cambiadas mal {tot.wrong} · "
          f"filas rechazadas {rejected}/{proposed}"
          f"\nTOTAL LÍNEA BASE · reproduce {btot.reproduced}/{btot.corrected} · cambiadas mal {btot.wrong}"
          f"\n→ {'✅ SUPERA' if beats else '❌ NO supera'} a la línea base (más reproducidas, igual o menos mal)")
    print(f"COSTO TOTAL · {p_tok} entrada / {c_tok} salida (razonamiento≈{r_tok}) · ${cost:.6f}")
    print(f"PRIVACIDAD de lo enviado a Nebius · descripciones completas: {privacy['descriptions']} · "
          f"montos: {privacy['amounts']} · nombres de persona: {privacy['person_names']}\n")


# ── Donde la IA aporta (13-oct): respuestas escritas ANTES por OTRA área ──────────────────────────
# El examen lo escribió Movimientos en `demo_data.py` (no este chat: así no lo arma quien lo rinde). Este
# lector se adapta a SU formato; el contenido del examen no se toca.
#   DEMO_TRUTH_PENDING = {libro: {comienzo_de_la_descripción: {account_type, category, status, why}}}
#     status "known"     → hay respuesta: cuenta en precisión y cobertura.
#     status "ambiguous" → "no se sabe sin el cliente": NO cuenta ni a favor ni en contra (fuera de
#                          precisión y de cobertura). Se informa aparte cuántas categorizó el agente.
#     status "split"     → "la fila mezcla dos cosas y hay que partirla": ninguna regla de UNA categoría
#                          acierta. Si el agente la categoriza, cuenta EN CONTRA de la precisión; si no la
#                          toca, no entra en la cobertura (no hay respuesta de una sola categoría).
#   DEMO_TRUTH_ERRORS = [{book, type, detail, story, expected{account_type, category},
#                         rows[{description, date, amount, is_inflow}]}]
#     Un error está "encontrado" si alguna alerta toca alguna de SUS filas (descripción + fecha + monto +
#     dirección exactos).

def _truth(name: str, blind: bool = False):
    import demo_data
    pending = (getattr(demo_data, "DEMO_TRUTH_PENDING", {}) or {}).get(name)
    key = "DEMO_TRUTH_ERRORS_BLIND" if blind else "DEMO_TRUTH_ERRORS"
    errors = [e for e in (getattr(demo_data, key, []) or []) if e.get("book") == name]
    return pending, (errors or None)


def _exam_ids(txs: list[Transaction], key: str) -> set[int]:
    """Los movimientos que un examen (`DEMO_TRUTH_ERRORS` o `_BLIND`) marca como error."""
    import demo_data
    keys = {((r["description"] or "").strip().upper(), str(r["date"]))
            for e in (getattr(demo_data, key, []) or []) for r in e.get("rows", [])}
    return {t.id for t in txs if ((t.description or "").strip().upper(), str(t.date)) in keys}


def _blind_ids(txs: list[Transaction]) -> set[int]:
    """Los movimientos del SEGUNDO examen (las filas que Movimientos agregó a ciegas, 14-oct), para
    ESCONDERLOS al medir contra el primero: sin esto, sus comercios saldrían impresos como "falsas
    alarmas" mientras se ajusta al agente, y el segundo examen dejaría de ser ciego. Se leen en código;
    nadie los imprime. Llave: (descripción, fecha), sin el monto."""
    import demo_data
    keys = {((desc or "").strip().upper(), str(day))
            for rows in (getattr(demo_data, "_BLIND_ROWS", {}) or {}).values() for day, desc, *_ in rows}
    # Y las filas que el segundo examen marca como error aunque ya existieran en el libro (✅ 14-oct: sin
    # esto salían como "falsas alarmas" del primero).
    keys |= {((r["description"] or "").strip().upper(), str(r["date"]))
             for e in (getattr(demo_data, "DEMO_TRUTH_ERRORS_BLIND", []) or []) for r in e.get("rows", [])}
    return {t.id for t in txs if ((t.description or "").strip().upper(), str(t.date)) in keys}


def _pending_entry(truth: dict, t: Transaction):
    """La entrada del examen de este movimiento: la llave es el COMIENZO de la descripción (la más larga
    que calce, por si dos llaves empiezan igual)."""
    desc = (t.description or "").upper()
    keys = [k for k in truth if desc.startswith(k.upper())]
    return truth[max(keys, key=len)] if keys else None


def _row_key(description, day, amount, inflow) -> tuple:
    return ((description or "").strip().upper(), str(day), f"{abs(float(amount)):.2f}", bool(inflow))


def measure_pending(session: Session, company: Company, truth: dict, *, model=None, reasoning: bool = False,
                    record: bool = False) -> dict:
    """(a) El ④ sobre lo PENDIENTE: precisión de lo que categorizaría y cobertura de lo que tiene
    respuesta. Con el motor real (`formula.decide`) y TODAS las filas propuestas juntas."""
    import agents
    ctx = BookContext(session, company)
    res = agents.draft(session, company, record=record, model=model, reasoning=reasoning)
    rules = formula.with_rows(ctx.rules, [p.row for p in res.proposals], company.id)
    pending = [t for t in ctx.txs if not t.category or t.account_type is None]
    c = Counter()
    wrong: list[str] = []
    for t in pending:
        e = _pending_entry(truth, t)
        status = e["status"] if e else "unanswered"
        if status == "known":
            c["known"] += 1
        d = formula.decide(rules, t)
        if d.source != "rule":
            continue
        if status == "known":
            c["decided"] += 1
            if (d.account_type, d.category) == (e["account_type"], e["category"]):
                c["right"] += 1
            elif len(wrong) < 6:
                wrong.append(f"{t.description[:38]} → {d.category} (respuesta: {e['category']})")
        elif status == "split":
            c["decided"] += 1          # cuenta en contra: una sola categoría no acierta una fila mezclada
            c["split_decided"] += 1
            if len(wrong) < 6:
                wrong.append(f"{t.description[:38]} → {d.category} (era una fila MEZCLADA: hay que partirla)")
        elif status == "ambiguous":
            c["ambiguous_decided"] += 1
        else:
            c["unanswered_decided"] += 1
    return {"book": company.name, "known": c["known"], "decided": c["decided"], "right": c["right"],
            "split_decided": c["split_decided"], "ambiguous_decided": c["ambiguous_decided"],
            "decided_unanswered": c["unanswered_decided"], "wrong": wrong, "usage": res.usage,
            "proposals": len(res.proposals), "rows": [p.row for p in res.proposals]}


def drafter_alerts(session: Session, company: Company, rows: list, hide: frozenset = frozenset()) -> list[tuple[set, dict]]:
    """Las propuestas del ④ que CAMBIARÍAN filas ya categorizadas (no las manuales: esas no las toca
    ninguna regla) también son alertas: la pestaña las muestra con su prueba sobre el historial. Una alerta
    por propuesta: (filas que cambia, {id → (account_type, category) que les pondría})."""
    ctx = BookContext(session, company)
    out = []
    for row in rows:
        rules = formula.with_rows(ctx.rules, [row], company.id)
        changed = {}
        for t in ctx.txs:
            if t.source == "manual" or t.account_type is None:
                continue
            d = formula.decide(rules, t)
            if d.source == "rule" and (d.account_type, d.category) != (t.account_type.value, t.category) \
                    and d != formula.decide(ctx.rules, t):
                changed[t.id] = (d.account_type, d.category)
        if changed and not (set(changed) & hide):
            out.append((set(changed), changed))
    return out


def measure_review(session: Session, company: Company, truth: list[dict], *, model=None,
                   draft_rows: Optional[list] = None, hide: frozenset = frozenset(),
                   other_exam: frozenset = frozenset(), already_reviewed: bool = False) -> dict:
    """(b) El ① sobre ERRORES plantados. Alerta del auditor SOLO = todo hallazgo abierto (incluidas las
    observaciones, como la nómina neta: el examen las pide; sin los chequeos de las notas, que son IA).
    Alerta CON el ① = los hallazgos que el ① NO llama "legítimo" (problema o inseguro) + lo que el ① no
    juzga (observaciones y chequeos de las notas). Cobertura = errores que toca alguna alerta.
    Precisión = alertas que tocan una fila de algún error. Se mide también al auditor solo."""
    import agents
    import book_review
    txs = session.exec(select(Transaction).where(Transaction.company_id == company.id)).all()
    by_key: dict[tuple, list[int]] = {}
    for t in txs:
        by_key.setdefault(_row_key(t.description, t.date, t.amount, t.is_inflow), []).append(t.id)
    planted = {}
    for i, e in enumerate(truth):
        ids = {tid for r in e.get("rows", []) for tid in by_key.get(
            _row_key(r["description"], r["date"], r["amount"], r["is_inflow"]), [])}
        planted[f"{e['type']}#{i + 1}"] = ids
    unmatched = [k for k, ids in planted.items() if not ids]
    all_planted = set().union(*planted.values()) if planted else set()
    # El ① primero: antes de revisar traduce las notas 📌 a chequeos, y `sync` ya los aplica.
    # Con el flujo nuevo (`--web oracle|live`) el ① ya corrió en `web_flow` (auditar → ② → ① otra vez): no
    # se paga una tercera vez.
    rev = None if already_reviewed else agents.review(session, company, lang="en", force=True, model=model)
    findings = book_review.sync(session, company)
    # Sigue alertando lo que el ① NO despacha: "problema" e "inseguro" piden mirar; solo "legítimo" dice
    # "se puede saltar" (decidido el 13-oct ANTES de medir el cambio; en la línea base "inseguro" era 0).
    # Un hallazgo que toca ALGUNA fila escondida (`hide`: las del otro examen) sale entero: ni cuenta ni se
    # imprime. (✅ 14-oct: quitar solo esas filas dejaba ver hallazgos que ellas causaban en filas normales,
    # p. ej. un comercio "con dos categorías" por una fila del otro examen.)
    findings = [f for f in findings if not (set(f.transaction_ids or []) & hide)]
    ids_of = {f.id: set(f.transaction_ids or []) for f in findings}
    flagged = [f for f in findings if f.type in book_review.UNJUDGED_TYPES or book_review.ai_cannot_dismiss(f)
               or f.agent_verdict in ("problem", "unsure")]

    # Una alerta que no toca este examen pero SÍ un error del OTRO examen (sus errores siguen en los libros)
    # no cuenta ni a favor ni en contra de la precisión. (✅ 14-oct: sin esto, en la corrida a ciegas los
    # errores del primer examen salían como "falsas alarmas"; el conteo se arregló DESPUÉS de esa corrida.)
    neutral = {f.id for f in findings if not (ids_of[f.id] & all_planted) and ids_of[f.id] & other_exam}

    def score_of(alerts):
        alerts = [f for f in alerts if f.id not in neutral]
        touching = [f for f in alerts if ids_of[f.id] & all_planted]
        found = [k for k, ids in planted.items() if ids and any(ids_of[f.id] & ids for f in alerts)]
        return {"alerts": len(alerts), "true_alerts": len(touching), "found": len(found),
                "missed": [k for k in planted if k not in found]}
    # Cómo JUZGA el ① cada hallazgo, según toque o no un error plantado (las observaciones no las juzga).
    judged = {}
    for f in findings:
        if f.type in book_review.UNJUDGED_TYPES:
            continue
        if f.id in neutral:
            continue
        side = "true" if ids_of[f.id] & all_planted else "false"
        k = f"{side}:{f.agent_verdict or 'none'}"
        judged[k] = judged.get(k, 0) + 1
    # ④ — sus propuestas sobre filas YA categorizadas (del mismo `draft` que se midió en lo Pendiente).
    d_alerts = drafter_alerts(session, company, draft_rows or [], hide)
    d_found = [k for k, ids in planted.items() if ids and any(a & ids for a, _ in d_alerts)]
    # ¿Y con la corrección que el examen espera? (solo los errores que traen "expected")
    d_fixed = [k for i, (k, ids) in enumerate(planted.items()) if ids and truth[i].get("expected")
               and any(fix.get(tid) == (truth[i]["expected"]["account_type"], truth[i]["expected"]["category"])
                       for _, fix in d_alerts for tid in ids)]
    drafter = {"alerts": len(d_alerts), "true_alerts": sum(1 for a, _ in d_alerts if a & all_planted),
               "found": len(d_found), "fixed": len(d_fixed), "missed": []}
    together = set(d_found) | {k for k, ids in planted.items() if ids and any(
        ids_of[f.id] & ids for f in flagged)}
    # ¿Personal para ESTE negocio? — qué opinó el modelo de cada comercio con un gasto personal plantado
    # (para ver POR QUÉ se escapa uno; solo errores de este examen).
    import personal_use
    saved = personal_use.current(session, company)
    by_id = {t.id: t for t in txs}
    personal_seen = []
    for i, (k, ids) in enumerate(planted.items()):
        if truth[i]["type"] != "personal_expense" or not ids:
            continue
        keys = {personal_use.merchant_key(by_id[x]) for x in ids if x in by_id} - {None}
        js = [saved[x] for x in keys if x in saved]
        personal_seen.append(f"{k} " + (", ".join(f"{j.keyword}={j.verdict}/{j.confidence}" for j in js) or
                                        "sin opinión (no es gasto del negocio, o no se juzgó)"))
    return {"book": company.name, "planted": len(planted), "unmatched": unmatched, "judged": judged,
            "drafter": drafter, "together": len(together),
            # El auditor SOLO es sin IA: los chequeos de las notas los tradujo Nemotron (van con la IA).
            "auditor": score_of([f for f in findings if f.type not in AI_FINDING_TYPES]), "reviewer": score_of(flagged),
            "notes": score_of([f for f in findings if f.type == "note_check"]), "usage": rev.usage if rev else None,
            "personal": score_of([f for f in findings if f.type == "personal_use"]), "personal_seen": personal_seen,
            "note_usage": rev.note_usage if rev else None, "personal_usage": rev.personal_usage if rev else None,
            "by_type": dict(Counter(f.type for f in findings)),
            "dismissed_true": [f"{f.type}:{f.merchant}" for f in findings if f.agent_verdict == "legitimate"
                               and ids_of[f.id] & all_planted],
            "false_alerts": [f"{f.type}:{f.merchant}" for f in flagged
                             if not (ids_of[f.id] & all_planted) and f.id not in neutral][:12],
            "neutral": len(neutral)}


def web_flow(session: Session, company: Company, *, model=None) -> dict:
    """El flujo REDISEÑADO (14-oct), como lo corre "Revisar el libro": auditar (código + notas + ¿personal?
    + ①) → el ② busca SOLO los comercios marcados → el ① vuelve a juzgar los hallazgos con evidencia nueva.
    Devuelve las búsquedas y los créditos."""
    import agents
    import merchant_verifier
    agents.review(session, company, lang="en", model=model)
    res = merchant_verifier.verify_flagged(session, company, limit=50)
    if res.finding_ids:
        agents.review(session, company, lang="en", model=model, finding_ids=res.finding_ids)
    # ¿Sirvió la búsqueda a fondo? (cuántas veces la básica no dio confianza alta, y con qué terminó)
    deep = Counter(f"{i.get('search_depth')}:{i.get('confidence') or 'none'}" for i in res.items if i.get("search_depth"))
    return {"searched": res.searched, "credits": res.credits, "skipped": dict(res.skipped), "deep": dict(deep),
            "searched_merchants": sorted(i["merchant"] for i in res.items), "rejudged": len(res.finding_ids)}


def old_flow_credits(session: Session, company: Company) -> int:
    """Lo que el flujo VIEJO gastaba en una oficina nueva: buscaba TODOS los comercios buscables del libro
    (1 crédito cada uno, con su tope de 12 por corrida)."""
    import merchant_verifier
    txs = session.exec(select(Transaction).where(Transaction.company_id == company.id)).all()
    return sum(1 for c in merchant_verifier.candidates(txs) if not c.skip)


def run_costs(session: Session, books: list) -> dict:
    """Costo de Nebius (las llamadas registradas en `agentcall`) y créditos de Tavily de la corrida."""
    from models import AgentCall
    ids = [b.id for b in books]
    rows = session.exec(select(AgentCall).where(AgentCall.company_id.in_(ids))).all()
    return {"nebius_usd": sum(r.cost_usd or 0 for r in rows if r.agent != "verifier"),
            "nebius_calls": sum(1 for r in rows if r.agent != "verifier"),
            "credits": sum(r.credits or 0 for r in rows if r.agent == "verifier"),
            "searches": sum(1 for r in rows if r.agent == "verifier")}


# Hallazgos que salen de una llamada a Nemotron: el "auditor solo" (sin IA) no los cuenta.
AI_FINDING_TYPES = {"note_check", "personal_use"}


def _pct(a: int, b: int) -> str:
    return f"{a}/{b} ({100 * a / b:.0f} %)" if b else f"{a}/0"


def _print_audit(pend: list[dict], revs: list[dict], privacy: dict, blind: bool = False,
                 costs: Optional[dict] = None, web: Optional[dict] = None) -> None:
    which = "el SEGUNDO examen, A CIEGAS (DEMO_TRUTH_ERRORS_BLIND)" if blind else "el primer examen (DEMO_TRUTH_ERRORS)"
    print(f"\nDONDE LA IA APORTA · {which} · respuestas escritas ANTES por Movimientos\n")
    cost = 0.0
    P = {"known": 0, "decided": 0, "right": 0, "split_decided": 0, "ambiguous_decided": 0, "decided_unanswered": 0}
    for r in pend:
        covered = r["decided"] - r["split_decided"]
        print(f"■ ④ {r['book']}: {r['proposals']} filas · precisión {_pct(r['right'], r['decided'])} · cobertura "
              f"{_pct(covered, r['known'])} de los Pendientes con respuesta · categorizaría además "
              f"{r['ambiguous_decided']} ambiguos (no cuentan) y {r['decided_unanswered']} sin respuesta")
        for w in r["wrong"]:
            print(f"      ✗ {w}")
        for k in P:
            P[k] += r[k]
        cost += (r["usage"].cost_usd or 0) if r["usage"] else 0
    A = {"planted": 0, "a_alerts": 0, "a_true": 0, "a_found": 0, "r_alerts": 0, "r_true": 0, "r_found": 0,
         "d_alerts": 0, "d_true": 0, "d_found": 0, "d_fixed": 0, "together": 0}
    for r in revs:
        a, v = r["auditor"], r["reviewer"]
        print(f"■ ① {r['book']}: {r['planted']} errores plantados · AUDITOR SOLO: encuentra {_pct(a['found'], r['planted'])}"
              f", precisión {_pct(a['true_alerts'], a['alerts'])} · CON EL ①: encuentra {_pct(v['found'], r['planted'])}"
              f", precisión {_pct(v['true_alerts'], v['alerts'])}")
        nc = r["notes"]
        print(f"      📌 notas → chequeos (Nemotron traduce, el código aplica): {nc['alerts']} alertas, tocan "
              f"{_pct(nc['found'], r['planted'])} de los errores, precisión {_pct(nc['true_alerts'], nc['alerts'])}")
        pu = r["personal"]
        print(f"      🧭 ¿personal para este negocio? (Nemotron opina, el código aplica): {pu['alerts']} alertas, tocan "
              f"{_pct(pu['found'], r['planted'])} de los errores, precisión {_pct(pu['true_alerts'], pu['alerts'])}")
        for s in r["personal_seen"]:
            print(f"         · {s}")
        d = r["drafter"]
        print(f"      ④ sus propuestas sobre lo ya categorizado: {d['alerts']} alertas, tocan {_pct(d['found'], r['planted'])}"
              f" de los errores ({d['fixed']} con la corrección esperada), precisión {_pct(d['true_alerts'], d['alerts'])}"
              f" · AUDITOR + ① + ④: encuentra {_pct(r['together'], r['planted'])}")
        if r.get("neutral"):
            print(f"      ({r['neutral']} alertas tocan solo errores del OTRO examen: no cuentan ni a favor ni en contra)")
        if r["unmatched"]:
            print(f"      ⚠️ errores del examen sin filas en el libro (no se pueden encontrar): {', '.join(r['unmatched'])}")
        if v["missed"]:
            print(f"      no encontró: {', '.join(v['missed'])}")
        if r["dismissed_true"]:
            print(f"      el ① llamó legítimo a un error: {', '.join(r['dismissed_true'])}")
        for fa in r["false_alerts"]:
            print(f"      falsa alarma: {fa}")
        A["planted"] += r["planted"]
        for pre, key in (("a", "auditor"), ("r", "reviewer")):
            A[f"{pre}_alerts"] += r[key]["alerts"]; A[f"{pre}_true"] += r[key]["true_alerts"]; A[f"{pre}_found"] += r[key]["found"]
        cost += (r["note_usage"].cost_usd or 0) if r.get("note_usage") else 0
        cost += (r["personal_usage"].cost_usd or 0) if r.get("personal_usage") else 0
        A["d_alerts"] += r["drafter"]["alerts"]; A["d_true"] += r["drafter"]["true_alerts"]
        A["d_found"] += r["drafter"]["found"]; A["d_fixed"] += r["drafter"]["fixed"]; A["together"] += r["together"]
        cost += (r["usage"].cost_usd or 0) if r["usage"] else 0
    print(f"\nTOTAL ④ PENDIENTES · precisión {_pct(P['right'], P['decided'])} · cobertura "
          f"{_pct(P['decided'] - P['split_decided'], P['known'])} · ambiguos que categorizaría (no cuentan): "
          f"{P['ambiguous_decided']}")
    print(f"TOTAL ① ERRORES · auditor solo: encuentra {_pct(A['a_found'], A['planted'])}, precisión "
          f"{_pct(A['a_true'], A['a_alerts'])} · con el ①: encuentra {_pct(A['r_found'], A['planted'])}, precisión "
          f"{_pct(A['r_true'], A['r_alerts'])}")
    print(f"TOTAL ④ SOBRE LO CATEGORIZADO · toca {_pct(A['d_found'], A['planted'])} de los errores ({A['d_fixed']} con "
          f"la corrección esperada), precisión {_pct(A['d_true'], A['d_alerts'])} · AUDITOR + ① + ④: encuentra "
          f"{_pct(A['together'], A['planted'])}")
    J = {}
    for r in revs:
        for k, n in r["judged"].items():
            J[k] = J.get(k, 0) + n
    row = lambda side: " · ".join(f"{v} {J.get(f'{side}:{v}', 0)}" for v in ("problem", "unsure", "legitimate", "none"))
    print(f"JUICIO DEL ① · sobre hallazgos que SÍ tocan un error: {row('true')}")
    print(f"               sobre hallazgos que NO tocan ninguno: {row('false')}")
    if costs is not None:
        # Nebius = lo registrado en `agentcall` (auditor de notas, ¿personal?, ①) + el ④ (medido sin registrar).
        draft_usd = sum((r["usage"].cost_usd or 0) for r in pend if r.get("usage"))
        print(f"TAVILY ({web['mode']}) · {costs['searches']} búsquedas · {costs['credits']} créditos"
              + (f" · el flujo viejo gastaba {web['old_credits']} en una oficina nueva" if web.get("old_credits") is not None else ""))
        for b, w in web.get("per_book", {}).items():
            print(f"      {b}: buscó {w['searched']} ({w['credits']} créditos) · {w.get('deep')} · el ① volvió a juzgar {w['rejudged']}"
                  f" · {', '.join(w['searched_merchants'][:12])}")
        print(f"NEBIUS · ${costs['nebius_usd'] + draft_usd:.6f} por corrida ({costs['nebius_calls']} llamadas registradas + el ④)")
    if costs is not None:
        cost = costs["nebius_usd"] + sum((r["usage"].cost_usd or 0) for r in pend if r.get("usage"))
    print(f"COSTO · ${cost:.6f} · PRIVACIDAD de lo enviado a Nebius · descripciones completas: "
          f"{privacy['descriptions']} · montos: {privacy['amounts']} · nombres de persona: {privacy['person_names']}\n")


def _print(results: list[dict], cutoff: date) -> None:
    print(f"\nLÍNEA BASE SIN IA · corte {cutoff.isoformat()} (aprende antes, se mide desde ahí)\n")
    tot = Score()
    for r in results:
        s = r["score"]
        print(f"■ {r['book']}  — correcciones a mano: {r['learn']} para aprender · {r['test']} para medir")
        for kw, cat in r["rows"]:
            print(f"    fila de la línea base: {kw!r} → {cat}")
        for kw, errs in r["rejected"]:
            print(f"    fila RECHAZADA por el validador: {kw!r} {errs}")
        print(f"    reproduce {s.reproduced}/{s.corrected} movimientos corregidos "
              f"({s.reproduced_rows}/{s.correction_rows} filas del historial) · cambiadas mal: {s.wrong} · "
              f"Pendientes que categorizaría: {s.newly_categorized}")
        for ex in s.wrong_examples:
            print(f"      ✗ {ex}")
        if r["note_case"]:
            n = r["note_case"]
            print(f"    caso de nota ({n['note']}): {n['hit']}/{n['expected']} filas esperadas · {n['extra']} de más")
        for k in ("corrected", "correction_rows", "reproduced", "reproduced_rows", "wrong", "newly_categorized"):
            setattr(tot, k, getattr(tot, k) + getattr(s, k))
    print(f"\nTOTAL · reproduce {tot.reproduced}/{tot.corrected} movimientos corregidos "
          f"({tot.reproduced_rows}/{tot.correction_rows} filas del historial) · cambiadas mal: {tot.wrong} · "
          f"Pendientes que categorizaría: {tot.newly_categorized}")
    print("→ Es el número que el agente tiene que SUPERAR: más reproducidas, igual o menos cambiadas mal.\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", action="store_true", help="siembra el demo en una transacción y la deshace")
    ap.add_argument("--company", type=int, help="id de un libro existente (solo lectura)")
    ap.add_argument("--cutoff", type=date.fromisoformat, default=DEFAULT_CUTOFF)
    ap.add_argument("--agent", action="store_true", help="corre también el agente ④ real (Nemotron)")
    ap.add_argument("--reasoning", action="store_true", help="deja razonar al modelo (más tokens)")
    ap.add_argument("--model", help="otro modelo de Nebius (por defecto NEBIUS_MODEL)")
    ap.add_argument("--web", choices=("seeded", "oracle", "live"), default="seeded",
                    help="con --audit: seeded = antes (todo sembrado); oracle/live = Tavily rediseñado (14-oct)")
    ap.add_argument("--blind", action="store_true",
                    help="con --audit: mide contra el SEGUNDO examen (DEMO_TRUTH_ERRORS_BLIND), a ciegas")
    ap.add_argument("--audit", action="store_true",
                    help="donde la IA aporta: ④ sobre lo Pendiente y ① sobre errores plantados (DEMO_TRUTH_*)")
    args = ap.parse_args()
    if not args.demo and args.company is None:
        ap.error("usa --demo o --company ID")

    from db import engine
    with Session(engine) as session:
        try:
            if args.demo:
                import book_notes
                import demo_data
                from models import Firm, User
                owner = User(firebase_uid="__measure_demo__", email="measure@test.local")
                session.add(owner); session.flush()
                firm = Firm(name="MEASURE DEMO", owner_user_id=owner.id)
                session.add(firm); session.flush()
                books = demo_data.seed_demo_books(session, firm, owner)
                # Las notas ya las siembra `demo_data.seed_demo_books` (Movimientos, 8-oct); sembrarlas aquí
                # otra vez las DUPLICABA (✅ 13-oct: la nota de las vacaciones salía dos veces).
                # Las verificaciones del ② del demo, como las recibe cada visitante (sin red ni créditos).
                import demo_web_evidence
                from models import MerchantLookup
                if args.web == "seeded":
                    demo_web_evidence.seed_lookups(session, firm, include_live=True)   # "antes": TODO sembrado
                else:
                    # Tavily rediseñado: la memoria de la oficina empieza VACÍA (`seed_demo_books` la siembra
                    # sola; aquí se borra dentro de la transacción, que se deshace al final).
                    for m in session.exec(select(MerchantLookup).where(MerchantLookup.firm_id == firm.id)).all():
                        session.delete(m)
                    if args.web == "oracle":
                        import merchant_verifier
                        merchant_verifier.ORACLE = {r["fingerprint"]: r for r in demo_web_evidence.load()}
                session.flush()
            else:
                book = session.get(Company, args.company)
                if book is None:
                    print(f"No existe el libro {args.company}", file=sys.stderr)
                    return 1
                books = [book]
            if args.audit:
                if not args.demo:
                    ap.error("--audit necesita --demo (las respuestas son de los libros del demo)")
                import agents
                import merchant_verifier
                import nemotron
                # El ① guarda su veredicto y hace commit: aquí todo se DESHACE al final (nada queda en la base).
                session.commit = session.flush  # type: ignore[method-assign]
                nemotron.SENT.clear()
                pend, revs = [], []
                web = {"mode": args.web, "per_book": {}}
                if args.web == "seeded":
                    # El flujo viejo, en una oficina NUEVA (sin la siembra del demo): todos los comercios.
                    web["old_credits"] = None
                for b in books:
                    tp, te = _truth(b.name, blind=args.blind)
                    book_txs = session.exec(select(Transaction).where(Transaction.company_id == b.id)).all()
                    hide = frozenset() if args.blind else frozenset(_blind_ids(book_txs))
                    if tp is None and te is None:
                        print(f"⏸️  {b.name}: todavía no hay DEMO_TRUTH_* en demo_data.py (Movimientos)")
                        continue
                    rows = None
                    if args.web != "seeded":
                        web["per_book"][b.name] = web_flow(session, b, model=args.model)
                    if tp:
                        pend.append(measure_pending(session, b, tp, model=args.model))
                        rows = pend[-1]["rows"]
                    if te:
                        other = frozenset(_exam_ids(book_txs, "DEMO_TRUTH_ERRORS")) if args.blind else frozenset()
                        revs.append(measure_review(session, b, te, model=args.model, draft_rows=rows, hide=hide,
                                                   other_exam=other, already_reviewed=args.web != "seeded"))
                txs = [t for b in books for t in session.exec(
                    select(Transaction).where(Transaction.company_id == b.id)).all()]
                if args.web == "seeded":
                    web["old_credits"] = sum(old_flow_credits(session, b) for b in books)
                _print_audit(pend, revs, agents.privacy_scan(list(nemotron.SENT), txs,
                                                             merchant_verifier.protected_names(txs)), blind=args.blind,
                             costs=run_costs(session, books), web=web)
                if merchant_verifier.SENT_QUERIES:
                    persons = merchant_verifier.protected_names(txs)
                    bad = [q for q in merchant_verifier.SENT_QUERIES if any(p.upper() in q.upper() for p in persons)]
                    print(f"PRIVACIDAD de lo enviado a Tavily · {len(merchant_verifier.SENT_QUERIES)} consultas · "
                          f"con un nombre protegido: {len(bad)}")
                return 0
            base = [measure_book(session, b, args.cutoff) for b in books]
            _print(base, args.cutoff)
            if args.agent:
                import agents
                import nemotron
                from company_profile import p2p_counterparty
                nemotron.SENT.clear()
                results = [measure_agent(session, b, args.cutoff, reasoning=args.reasoning, model=args.model)
                           for b in books]
                txs = [t for b in books for t in session.exec(
                    select(Transaction).where(Transaction.company_id == b.id)).all()]
                import merchant_verifier
                persons = merchant_verifier.protected_names(txs)
                _print_agent(results, base, agents.privacy_scan(list(nemotron.SENT), txs, persons))
        finally:
            session.rollback()   # --demo no deja NADA en la base; --company nunca escribió
    return 0


if __name__ == "__main__":
    sys.exit(main())
