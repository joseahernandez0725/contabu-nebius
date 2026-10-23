"""
ContaBu — LA PRUEBA DE CAMPO sobre un libro real (FASE 11, Auditoría / IA · 20-oct-2026).

El 20-oct "Revisar el libro" corrió por primera vez sobre un libro REAL, sin ajustar ningún prompt. Este
script saca lo que hace falta para medir esa corrida, sin tocar la base:

- `label`: el CSV para que Jose ETIQUETE a mano lo que dijo la IA — los hallazgos abiertos (tipo, comercio,
  filas, veredicto y observación) y los juicios del ⑤ "¿personal para este negocio?" (comercio, juicio,
  confianza y razón). Cada fila con una columna vacía `jose` (acertó / se equivocó / no sé) y otra de
  comentario. Gratis: solo lee.
- `drafter`: POR QUÉ el validador rechazó las filas del ④. Vuelve a correr el redactor sobre el libro
  (1–2 llamadas REALES a Nebius) dentro de una transacción que se DESHACE al final, como `measure.py`:
  no registra la llamada, no guarda nada. El motivo de cada rechazo (el primero que la para, igual que
  `agents.draft`) y un diagnóstico de lo que cazaría cada fila, al archivo privado; en pantalla, solo
  los conteos. 🔄 20-oct (2): `--verify live` corre antes el paso Verificar (lo marcado + los Pendientes más
  frecuentes; Tavily de verdad, créditos reales) y GUARDA la evidencia en el archivo privado; `--verify saved`
  la reusa como oráculo (0 créditos), así la remedición se repite con la misma evidencia.
- `grade` (20-oct (2), DISEÑADO PARA CORRERSE CUANDO JOSE TERMINE DE CATEGORIZAR): califica el ④ contra lo que
  decidió un bookkeeper real. Dentro de la transacción que se deshace, devuelve el libro al "antes" (`--since` = la
  hora del respaldo): cada movimiento que cambió desde entonces vuelve a como estaba en su PRIMER cambio
  posterior (el historial `categorizationchange` guarda el antes y el después, también lo que hizo una regla), se
  borran esos cambios del historial (el ④ no puede aprender de la respuesta), las reglas y las notas creadas
  después. Luego Verificar (evidencia guardada) → el ④ → el motor real decide cada movimiento que estaba Pendiente
  y Jose categorizó, y se compara con lo que Jose puso.

🔴 Datos reales: TODO lo que escribe va a `backend/private/field_test/` (git lo ignora) con permisos
600. La pantalla solo muestra conteos y códigos de motivo: nunca descripciones, montos ni comercios.

Lee la base de `DATABASE_URL` (la del libro real está en `.env.real`):
    DATABASE_URL=$(grep ^DATABASE_URL .env.real | cut -d= -f2-) .venv/bin/python field_test.py label --company 1
    DATABASE_URL=$(grep ^DATABASE_URL .env.real | cut -d= -f2-) .venv/bin/python field_test.py drafter --company 1
    … field_test.py drafter --company 1 --verify live      (Tavily de verdad; guarda la evidencia)
    … field_test.py drafter --company 1 --verify saved     (la misma evidencia, 0 créditos)
    … field_test.py grade --company 1 --since "2026-10-20 08:00" --verify saved   (hora del respaldo, en el reloj de la base)
"""

import argparse
import csv
import json
import os
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Optional

from sqlmodel import Session, select

from models import Company, Finding, MerchantJudgment, Transaction

OUT_DIR = Path(__file__).resolve().parent / "private" / "field_test"
JOSE_OPTIONS = "acertó / se equivocó / no sé"


def _private_open(path: Path, mode: str = "w"):
    """Abre un archivo en la carpeta privada con permisos 600 desde el primer byte (no después)."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.chmod(path, 0o600)   # por si el archivo ya existía con otros permisos
    return os.fdopen(fd, mode, encoding="utf-8-sig" if path.suffix == ".csv" else "utf-8", newline="")


# ── label: el CSV para etiquetar ─────────────────────────────────────────────────────────────────

LABEL_COLUMNS = ["seccion", "id", "tipo", "comercio", "filas", "veredicto_ia", "confianza",
                 "observacion_ia_es", "observacion_ia_en", "detalle", "jose", "comentario_jose"]


def _finding_rows(session: Session, company: Company) -> list[dict]:
    import book_review
    rows = []
    found = session.exec(select(Finding).where(Finding.company_id == company.id, Finding.status == "open")
                         .order_by(Finding.type, Finding.id)).all()
    for f in found:
        p = f.payload or {}
        # El ① no juzga las observaciones ni el ⑤ (`UNJUDGED_TYPES`): su "observación" es la del detector.
        obs_es = book_review.observation_text(f, "es") or p.get("reason_es") or ""
        obs_en = book_review.observation_text(f, "en") or p.get("reason_en") or ""
        verdict = f.agent_verdict or ("(no lo juzga el ①)" if f.type in book_review.UNJUDGED_TYPES else "(sin veredicto)")
        if f.type == "personal_use":
            verdict = f"⑤ {p.get('verdict')}"
        rows.append({"seccion": "hallazgo", "id": f.id, "tipo": f.type, "comercio": f.merchant or "",
                     "filas": len(f.transaction_ids or []), "veredicto_ia": verdict,
                     "confianza": p.get("confidence", ""), "observacion_ia_es": obs_es, "observacion_ia_en": obs_en,
                     "detalle": json.dumps({"severity": f.severity, "payload": p, "transaction_ids": f.transaction_ids},
                                           ensure_ascii=False, default=str),
                     "jose": "", "comentario_jose": ""})
    return rows


def _judgment_rows(session: Session, company: Company) -> list[dict]:
    import personal_use
    txs = session.exec(select(Transaction).where(Transaction.company_id == company.id)).all()
    groups = personal_use.candidates(txs)
    order = {"personal": 0, "mixed": 1, "business": 2}
    js = session.exec(select(MerchantJudgment).where(MerchantJudgment.company_id == company.id)).all()
    rows = []
    for j in sorted(js, key=lambda j: (order.get(j.verdict, 9), j.keyword)):
        group = groups.get(j.merchant_key, [])
        rows.append({"seccion": "juicio_personal", "id": j.id, "tipo": "personal_judge", "comercio": j.keyword,
                     "filas": len(group), "veredicto_ia": j.verdict, "confianza": j.confidence,
                     "observacion_ia_es": j.reason_es or "", "observacion_ia_en": j.reason_en or "",
                     "detalle": json.dumps({"merchant_key": j.merchant_key, "model": j.model,
                                            "categories": sorted({f"{t.account_type.value}/{t.category}" for t in group})},
                                           ensure_ascii=False),
                     "jose": "", "comentario_jose": ""})
    return rows


def label(session: Session, company: Company, day: str) -> int:
    findings, judgments = _finding_rows(session, company), _judgment_rows(session, company)
    path = OUT_DIR / f"etiquetar_{day}.csv"
    with _private_open(path) as fh:
        w = csv.DictWriter(fh, fieldnames=LABEL_COLUMNS)
        w.writeheader()
        w.writerows(findings + judgments)
    print(f"Escrito {path.relative_to(Path(__file__).resolve().parent)} (permisos 600)")
    print(f"  hallazgos abiertos: {len(findings)} · por tipo: {dict(Counter(r['tipo'] for r in findings))}")
    print(f"  juicios del ⑤: {len(judgments)} · por juicio y confianza: "
          f"{dict(Counter((r['veredicto_ia'], r['confianza']) for r in judgments))}")
    print(f"  columna `jose`: {JOSE_OPTIONS}")
    return 0


# ── drafter: por qué se rechazaron las filas del ④ ──────────────────────────────────────────────────

# Los motivos de `agents.draft`, agrupados como los lee una persona.
REASON_GROUP = {
    "not_an_object": "esquema", "unknown_correction": "esquema", "unresolved_person": "esquema",
    "keyword_bad_chars": "esquema", "keyword_too_generic": "esquema", "subcategory_bad_chars": "esquema",
    "date_out_of_range": "esquema", "bad_window": "esquema", "bad_amount_range": "esquema",
    "unknown_note": "esquema", "unknown_change": "esquema",
    "category_not_in_chart": "categoría fuera del plan", "category_wrong_section": "categoría fuera del plan",
    "duplicate_rule": "repetida (ya existe esa regla)", "repeated_in_proposal": "repetida (en la misma propuesta)",
    "matches_nothing": "sin efecto (no caza nada)", "no_effect": "sin efecto (no cambia nada)",
    "dismissed_before": "ya descartada",
    "contradicts_corrections": "contradice correcciones",
    "unrelated_evidence": "sin respaldo (correcciones de otras filas)", "weak_evidence": "sin respaldo (1 corrección)",
    "no_evidence": "sin respaldo (ni nota, ni corrección, ni internet)",
    "person_without_evidence": "pago a persona sin respaldo",
    "outside_note": "fuera de la nota", "outside_focus": "fuera del comercio pedido",
}


def _reason_group(code: str) -> str:
    base = code.split(":")[0]
    if base in ("unknown_field", "bad_value"):
        return "esquema"
    return REASON_GROUP.get(base, f"otro ({base})")


def _diagnose(view, raw) -> dict:
    """Qué cazaría la fila en el libro y quién decide hoy esas filas — para leer el motivo con contexto.
    Solo para filas que pasan el esquema; nunca cambia nada."""
    import agents
    import formula
    candidate, errors = agents._from_model(raw, view)
    if errors:
        return {"schema_ok": False}
    try:   # el esquema solo: una fila "repetida" o "fuera del plan" también se diagnostica
        row = formula.FormulaRow.model_validate(candidate)
    except Exception:
        return {"schema_ok": False}
    rule = formula.to_rule(row, view.company.id)
    hit = [t for t in view.ctx.txs
           if formula.match_rule([rule], formula.matchable_text(t.description, t.description_extra), t.date, t.amount)]
    target = (row.account_type.value, row.category)
    test = agents.history_test(view, row)
    return {
        "schema_ok": True, "hits": len(hit),
        "hits_by_source": dict(Counter(t.source or "pending" for t in hit)),
        "hits_already_at_target": sum(1 for t in hit if (t.account_type.value if t.account_type else None,
                                                          t.category) == target),
        "web_evidence": sum(1 for t in hit if t.id in view.web),
        "web_high_or_medium": sum(1 for t in hit if t.id in view.web and view.web[t.id].confidence in ("high", "medium")),
        "in_chart": row.category in view.ctx.chart.get(row.account_type.value, []),
        "from_notes": row.from_notes, "from_changes": len(row.from_changes),
        "history_test": {k: test[k] for k in ("changes", "protected_manual", "realigned",
                                              "agrees_with_corrections", "disagrees_with_corrections")},
    }


def _oracle_path(company: Company) -> Path:
    return OUT_DIR / f"web_evidencia_libro{company.id}.json"


def run_verify(session: Session, company: Company, mode: str) -> dict:
    """El paso Verificar como en "Revisar el libro" (lo marcado + los Pendientes más frecuentes), SIN registrar:
    `live` = Tavily de verdad y la evidencia queda guardada en privado; `saved` = esa evidencia como oráculo."""
    import merchant_verifier
    if mode == "saved":
        merchant_verifier.ORACLE = {r["fingerprint"]: r for r in json.load(open(_oracle_path(company), encoding="utf-8"))}
    merchant_verifier.SENT_QUERIES.clear()
    before = set(merchant_verifier.cached(session, company))
    txs = session.exec(select(Transaction).where(Transaction.company_id == company.id)).all()
    _flagged, pend, _memory = merchant_verifier.review_candidates(session, company, txs)
    pending_fps = {c.fingerprint for c in pend}
    res = merchant_verifier.verify_flagged(session, company, record=False)
    persons = merchant_verifier.protected_names(txs)
    if mode == "live":
        # 22-oct: se SUMA a lo guardado (antes lo pisaba): la evidencia de una corrida anterior no se pierde.
        saved = {r["fingerprint"]: r for r in json.load(open(_oracle_path(company), encoding="utf-8"))} \
            if _oracle_path(company).exists() else {}
        for fp, m in merchant_verifier.cached(session, company).items():
            if fp not in before:
                saved[fp] = {"fingerprint": m.fingerprint, "business_type": m.business_type, "confidence": m.confidence,
                             "source_url": m.source_url, "results_count": m.results_count, "search_depth": m.search_depth}
        with _private_open(_oracle_path(company)) as fh:
            json.dump(list(saved.values()), fh, ensure_ascii=False, indent=2)
    return {"merchants": len(res.items), "flagged": res.flagged, "pending": res.pending, "searched": res.searched,
            "from_cache": res.from_cache, "credits": res.credits if mode == "live" else 0,
            "by_confidence": dict(Counter(i.get("confidence") or "none" for i in res.items)),
            "pending_high_or_medium": sum(1 for i in res.items if i["fingerprint"] in pending_fps
                                          and i.get("confidence") in ("high", "medium")),
            "skipped": dict(res.skipped),
            "queries_with_protected_name": sum(1 for q in merchant_verifier.SENT_QUERIES
                                               if any(p.upper() in q.upper() for p in persons))}


def drafter(session: Session, company: Company, day: str, lang: str, verify: str = "none") -> int:
    import agents
    import nemotron
    # Como `measure.py --audit`: lo que el código guarde (la sincronización de hallazgos) queda en la
    # transacción, que se deshace al final. `record=False`: la llamada no entra a `agentcall` ni al cupo.
    session.commit = session.flush  # type: ignore[method-assign]
    nemotron.SENT.clear()
    web = run_verify(session, company, verify) if verify != "none" else None
    if web:
        print(f"  verificar ({verify}): {web}")
    res = agents.draft(session, company, lang=lang, record=False)
    view = agents.build_view(session, company)
    detail = []
    for r in res.rejected:
        detail.append({"errors": r["errors"], "groups": sorted({_reason_group(e) for e in r["errors"]}),
                       "raw": r["raw"], "diagnosis": _diagnose(view, r["raw"])})
    accepted = [{**agents.proposal_dict(p), "diagnosis": _diagnose(view, {
        **p.row.model_dump(mode="json", exclude={"from_changes"}), "from_corrections": []})} for p in res.proposals]
    from merchant_verifier import protected_names
    privacy = agents.privacy_scan(list(nemotron.SENT), view.ctx.txs, protected_names(view.ctx.txs))
    path = OUT_DIR / f"drafter_rechazos_{day}{'' if verify == 'none' else '_verify_' + verify}.json"
    with _private_open(path) as fh:
        json.dump({"fecha": day, "libro": company.id, "lang": lang, "verify": web, "outcome": res.outcome, "calls": res.calls,
                   "usage": None if res.usage is None else {
                       "model": res.usage.model, "prompt_tokens": res.usage.prompt_tokens,
                       "completion_tokens": res.usage.completion_tokens, "cost_usd": res.usage.cost_usd},
                   "privacy": privacy, "rejected": detail, "accepted": accepted},
                  fh, ensure_ascii=False, indent=2, default=str)
    total = len(res.rejected) + len(res.proposals)
    print(f"Escrito {path.relative_to(Path(__file__).resolve().parent)} (permisos 600) · nada guardado en la base")
    print(f"  llamadas: {res.calls} · resultado: {res.outcome} · costo: "
          f"${(res.usage.cost_usd or 0) if res.usage else 0:.5f}")
    print(f"  filas del modelo: {total} · pasan: {len(res.proposals)} · rechazadas: {len(res.rejected)}")
    print("  motivo (el primero que la para):")
    for code, n in Counter(e for r in res.rejected for e in r["errors"][:1]).most_common():
        print(f"    {n} × {code}  [{_reason_group(code)}]")
    diag = [d["diagnosis"] for d in detail if d["diagnosis"].get("schema_ok")]
    if diag:
        print(f"  de las rechazadas que pasan el esquema ({len(diag)}): "
              f"cazan 0 filas: {sum(1 for d in diag if d['hits'] == 0)} · "
              f"todo lo que cazan YA está en esa categoría: {sum(1 for d in diag if d['hits'] and d['hits_already_at_target'] == d['hits'])} · "
              f"citan correcciones: {sum(1 for d in diag if d['from_changes'])} · "
              f"con evidencia web alta/media: {sum(1 for d in diag if d['web_high_or_medium'])}")
        print(f"    quién decide hoy lo que cazan: "
              f"{dict(sum((Counter(d['hits_by_source']) for d in diag), Counter()))}")
    print(f"  privacidad de lo enviado: {privacy}")
    if res.proposals:
        test = Counter()
        for p in res.proposals:
            test["changes"] += p.test["changes"]
            test["with_web"] += bool(p.web)
            test["from_notes"] += bool(p.row.from_notes)
            test["from_corrections"] += bool(p.row.from_changes)
        print(f"  propuestas que pasan: {len(res.proposals)} · categorizarían {test['changes']} movimientos · "
              f"respaldo: web {test['with_web']} · notas {test['from_notes']} · correcciones {test['from_corrections']}")
    return 0


# ── grade: el ④ contra lo que decidió un bookkeeper real ───────────────────────────────────────────

def rewind(session: Session, company: Company, since, keep_note_ids: frozenset = frozenset()) -> dict:
    """Devuelve el libro a como estaba en `since`, DENTRO de la transacción (que se deshace). Devuelve la clave de
    respuestas: movimiento → (sección, categoría) que puso el bookkeeper, para los que estaban Pendientes."""
    from models import BookNote, CategorizationChange, CategoryRule
    from models import AccountType
    changes = session.exec(select(CategorizationChange).where(
        CategorizationChange.company_id == company.id, CategorizationChange.changed_at >= since)
        .order_by(CategorizationChange.changed_at, CategorizationChange.id)).all()
    first: dict[int, CategorizationChange] = {}
    for ch in changes:
        first.setdefault(ch.transaction_id, ch)
    key, reverted = {}, Counter()
    for tx_id, ch in first.items():
        t = session.get(Transaction, tx_id)
        if t is None:
            continue
        final = (t.account_type.value if t.account_type else None, t.category)
        was_pending = not ch.category_before or not ch.account_type_before
        if was_pending and final[0] and final[1]:
            key[t.id] = final
            reverted[f"pending_then_{t.source or 'pending'}"] += 1
        else:
            reverted["was_categorized"] += 1
        t.account_type = AccountType(ch.account_type_before) if ch.account_type_before else None
        t.category, t.subcategory = ch.category_before, ch.subcategory_before
        t.source, t.matched_rule_id = ch.source_before, ch.matched_rule_id_before
        session.add(t)
    for ch in changes:
        session.delete(ch)                   # el ④ no aprende de la respuesta
    rules = session.exec(select(CategoryRule).where(CategoryRule.company_id == company.id,
                                                    CategoryRule.created_at >= since)).all()
    for r in rules:
        session.delete(r)
    # 22-oct (2): `keep_note_ids` — notas que se CONSERVAN aunque sean posteriores (medir el ④ con la nota del bookkeeper).
    notes = [n for n in session.exec(select(BookNote).where(BookNote.company_id == company.id, BookNote.created_at >= since)).all()
             if n.id not in keep_note_ids]
    for n in notes:
        session.delete(n)
    session.flush()
    return {"key": key, "reverted": dict(reverted), "changes_removed": len(changes), "rules_removed": len(rules),
            "notes_removed": len(notes)}


def grade_once(session: Session, company: Company, since, verify: str, lang: str = "es",
               model: Optional[str] = None, reasoning: Optional[bool] = None,
               keep_note_ids: frozenset = frozenset()) -> dict:
    """UNA calificación del ④ (22-oct: devuelve las métricas; `real_matrix.py` la repite por modelo). Dentro de la
    transacción de quien llama, que se deshace: vuelve al "antes", Verificar, el ④ con ese modelo, el motor decide.
    Métricas por año (2026 aparte del resto) y en total; el detalle por movimiento va en `detail` (privado)."""
    import agents
    import formula
    import nemotron
    session.commit = session.flush  # type: ignore[method-assign]
    nemotron.SENT.clear()
    back = rewind(session, company, since, keep_note_ids)
    key = back["key"]
    web = run_verify(session, company, verify) if verify != "none" else None
    res = agents.draft(session, company, lang=lang, record=False, model=model, reasoning=reasoning)
    ctx = formula.BookContext(session, company)
    rules = formula.with_rows(ctx.rules, [p.row for p in res.proposals], company.id)
    c, detail = Counter(), []
    for t in ctx.txs:
        if t.id not in key:
            continue
        span = "2026" if t.date.year == 2026 else "antes"
        d = formula.decide(rules, t)
        if d.source != "rule":
            c[f"{span}:left_pending"] += 1
            continue
        ok = (d.account_type, d.category) == key[t.id]
        c[f"{span}:{'right' if ok else 'wrong'}"] += 1
        detail.append({"tx": t.id, "year": t.date.year, "agent": [d.account_type, d.category],
                       "bookkeeper": list(key[t.id]), "ok": ok})
    spans = {}
    for span in ("2026", "antes"):
        right, wrong, left = c[f"{span}:right"], c[f"{span}:wrong"], c[f"{span}:left_pending"]
        spans[span] = {"key": right + wrong + left, "decided": right + wrong, "right": right}
    spans["total"] = {k: spans["2026"][k] + spans["antes"][k] for k in ("key", "decided", "right")}
    u = res.usage
    privacy = agents.privacy_scan(list(nemotron.SENT), ctx.txs,
                                  __import__("merchant_verifier").protected_names(ctx.txs))
    return {"rewind": {k: v for k, v in back.items() if k != "key"}, "verify": web, "outcome": res.outcome,
            "calls": res.calls, "proposals": len(res.proposals), "rejection_counts": res.rejection_counts,
            "spans": spans, "cost_usd": (u.cost_usd if u else 0.0) or 0.0, "latency_ms": u.latency_ms if u else 0,
            # Cada fila que pasa, SIN su palabra clave: sección/cuenta, montos, si cita una nota y cuánto cambia.
            "rows": [{"account_type": p.row.account_type.value, "category": p.row.category,
                      "amount_min": float(p.row.amount_min) if p.row.amount_min is not None else None,
                      "amount_max": float(p.row.amount_max) if p.row.amount_max is not None else None,
                      "from_notes": list(p.row.from_notes), "from_changes": len(p.row.from_changes),
                      "web": bool(p.web), "changes": p.test["changes"]} for p in res.proposals],
            # Lo que el modelo escribió con monto aunque el validador lo rechazara (¿inventó un umbral?).
            "rejected_amounts": [{"amount_min": r["raw"].get("amount_min"), "amount_max": r["raw"].get("amount_max"),
                                  "from_notes": r["raw"].get("from_notes"), "error": (r["errors"] or [None])[0]}
                                 for r in res.rejected if isinstance(r.get("raw"), dict)
                                 and (r["raw"].get("amount_min") is not None or r["raw"].get("amount_max") is not None)],
            "completion_tokens": u.completion_tokens if u else 0, "privacy": privacy,
            "_private": {"proposals": [agents.proposal_dict(p) for p in res.proposals], "detail": detail}}


def grade(session: Session, company: Company, day: str, since, verify: str, lang: str) -> int:
    from sqlalchemy import text
    print(f"  reloj de la base ahora: {session.connection().execute(text('SELECT LOCALTIMESTAMP')).scalar()}")
    g = grade_once(session, company, since, verify, lang)
    back = g["rewind"]
    print(f"  al 'antes' ({since}): {back['reverted']} · cambios del historial quitados: {back['changes_removed']} · "
          f"reglas quitadas: {back['rules_removed']} · notas quitadas: {back['notes_removed']}")
    if g["verify"]:
        print(f"  verificar ({verify}): {g['verify']}")
    path = OUT_DIR / f"grade_{day}.json"
    with _private_open(path) as fh:
        json.dump({"since": str(since), **g}, fh, ensure_ascii=False, indent=2, default=str)
    print(f"Escrito {path.relative_to(Path(__file__).resolve().parent)} (permisos 600) · nada guardado en la base")
    print(f"  ④: {g['proposals']} filas pasan · rechazos: {g['rejection_counts']} · resultado: {g['outcome']}")
    for span, m in g["spans"].items():
        print(f"  {span}: decide {m['decided']}/{m['key']} (cobertura) · acierta {m['right']}/{m['decided']} (precisión)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=("label", "drafter", "grade"))
    ap.add_argument("--company", type=int, required=True, help="id del libro")
    ap.add_argument("--date", default=date.today().isoformat(), help="fecha para el nombre del archivo")
    ap.add_argument("--lang", default="es", help="drafter: idioma de las explicaciones (no cambia el validador)")
    ap.add_argument("--verify", choices=("none", "live", "saved"), default="none",
                    help="drafter/grade: correr antes el paso Verificar (live = Tavily de verdad; saved = la evidencia guardada)")
    ap.add_argument("--since", help="grade: la hora del respaldo en el reloj de la BASE ('AAAA-MM-DD HH:MM'): las fechas sin zona del "
                         "historial quedan en la hora de la sesión de Postgres (ver agent_calls.team), compárala con SELECT LOCALTIMESTAMP")
    args = ap.parse_args()
    from db import engine
    with Session(engine) as session:
        try:
            company = session.get(Company, args.company)
            if company is None:
                print(f"No existe el libro {args.company}", file=sys.stderr)
                return 1
            if args.what == "label":
                return label(session, company, args.date)
            if args.what == "grade":
                if not args.since:
                    ap.error("grade necesita --since (la hora del respaldo)")
                from datetime import datetime
                return grade(session, company, args.date, datetime.fromisoformat(args.since), args.verify, args.lang)
            return drafter(session, company, args.date, args.lang, args.verify)
        finally:
            session.rollback()   # `label` solo lee; `drafter` deshace todo lo que el código haya escrito


if __name__ == "__main__":
    sys.exit(main())
