"""
ContaBu — LOS MODELOS POR ASIENTO, EN EL LIBRO REAL (FASE 11, Auditoría / IA · 22-oct-2026).

¿La elección de modelos hecha con libros sintéticos (`team_matrix.py`, 130 corridas) se sostiene en un libro
REAL, categorizado por un bookkeeper y nunca visto por un modelo? Cada asiento con los mismos candidatos que
`team_matrix` (nano · lightning · super · ultra · vl, sin y con razonar), N corridas por candidato:

- `drafter`  (④, libro real): `field_test.grade_once` — el libro vuelve al "antes" (`--since`), Verificar con la
  evidencia GUARDADA (0 créditos; todos los modelos ven la misma) y el ④ con ese modelo; cobertura y precisión
  contra lo que decidió el bookkeeper, 2026 aparte.
- `personal` (⑤, libro real): la misma llamada que `personal_use.refresh`, con una VERDAD APROXIMADA sacada de las
  categorías del bookkeeper: un comercio cuyas salidas están TODAS en una cuenta personal o en *Shareholder
  distributions* es "personal"; uno con TODAS en gastos del negocio no lo es; los demás no se califican.
- `exam`     (①/⑤, la COPIA `contabu_real_examen`): la revisión completa como `measure.py --audit` (auditar → ②
  con evidencia guardada → ① otra vez → ④), contra la clave de `private/real_exam/key.json`. 🔴 A CIEGAS: el
  script lee la clave para puntuar y solo devuelve conteos; su sha1 se compara antes y después de cada corrida.
- `check`: sin IA y sin créditos — tamaño de la clave tras volver al "antes" y cuánta evidencia guardada hay.
- `table`: las tablas, de `private/field_test/real_matrix.jsonl`.

🔴 Nada se escribe en las bases: cada corrida es UNA transacción que se deshace (`session.commit` = `flush`; los
agentes no registran o registran dentro de ella). El script comprueba en qué base está (`drafter`/`personal` en
`contabu_real`, `exam` en `contabu_real_examen`). Datos reales: a `private/` con permisos 600; en pantalla, solo
conteos y porcentajes — ningún comercio, descripción ni monto. Ningún número de aquí va al README sin el
consentimiento del dueño del libro.

    U=$(grep ^DATABASE_URL .env.real | cut -d= -f2-)
    DATABASE_URL="$U" .venv/bin/python real_matrix.py check
    DATABASE_URL="$U" .venv/bin/python real_matrix.py drafter --n 3
    DATABASE_URL="$U" .venv/bin/python real_matrix.py personal --n 3
    DATABASE_URL="${U%/*}/contabu_real_examen" .venv/bin/python real_matrix.py exam --n 3
    .venv/bin/python real_matrix.py table
"""

import argparse
import hashlib
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from sqlalchemy import text
from sqlmodel import Session, select

HERE = Path(__file__).resolve().parent
OUT = HERE / "private" / "field_test" / "real_matrix.jsonl"
KEY = HERE / "private" / "real_exam" / "key.json"
KEY_SHA1 = HERE / "private" / "real_exam" / "key_frozen_sha1.txt"
COMPANY_ID = 1
SINCE = datetime(2026, 10, 20, 20, 50)          # la prueba de Jose: reloj de la base
MODELS = ("nano", "lightning", "super", "ultra", "vl")
CONFIGS = [(m, r) for m in MODELS for r in (False, True)]
USD_CAP = 4.00                                   # tope de cada tanda (Nebius); ver `_spent`
# Los exámenes ciegos sembrados (23-oct): base, clave, prefijo del `kind`, cuántos comercios NUEVOS busca el ② por
# revisión y un archivo de evidencia propio (lo de un examen no se mezcla con la evidencia del libro real).
EXAMS = {
    "1": {"db": "contabu_real_examen", "key": "real_exam", "prefix": "exam", "web_limit": 50, "evidence": None},
    "1_2026": {"db": "contabu_real_examen1_2026", "key": "real_exam", "prefix": "exam1_2026", "web_limit": 50,
               "evidence": None},
    # El examen 2 trae filas INSERTADAS (comercios que el ② no conoce): en vivo la primera vez, con tope de 10 comercios
    # nuevos (≤ 30 créditos: 1 básica + 2 avanzada cada uno), y su evidencia en un archivo aparte.
    "2": {"db": "contabu_real_examen2", "key": "real_exam2", "prefix": "exam2", "web_limit": 10,
          "evidence": "web_evidencia_examen2.json"},
}
TAVILY_CAP = 30
SEAT = {"drafter": "drafter", "drafter_note": "drafter", "drafter_note_fix": "drafter",
        **{f"{e['prefix']}{x}_{k}": seat for e in EXAMS.values() for x in ("", "_nonote")
           for k, seat in (("reviewer", "reviewer"), ("personal", "personal"))}, "personal": "personal", "exam_reviewer": "reviewer",
        "exam_personal": "personal"}
KEY_SIZE = (386, 118)    # la clave del ④ tras volver al "antes" (total, 2026): si cambia, el libro ya no es el de la medición


def _append(row: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(OUT, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.chmod(OUT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _read() -> list[dict]:
    return [json.loads(x) for x in open(OUT, encoding="utf-8")] if OUT.exists() else []


SPENT_SINCE = os.getenv("REAL_MATRIX_SINCE", "2026-10-22T20:38")   # tope por tanda: su inicio (reloj del sistema, `ts`)


def _spent() -> float:
    return sum(r.get("cost_usd") or 0.0 for r in _read() if r.get("ts", "") >= SPENT_SINCE)


def _db(session: Session) -> str:
    return session.connection().execute(text("select current_database()")).scalar()


def _require_db(session: Session, name: str) -> None:
    got = _db(session)
    if got != name:
        sys.exit(f"⛔ esta orden corre sobre `{name}` y la conexión es `{got}`")


def _key_sha1() -> str:
    return hashlib.sha1(KEY.read_bytes()).hexdigest()


def _ok(r: dict) -> bool:
    """Una corrida que terminó (un error de red o de la llamada no cuenta: se repite)."""
    return not r.get("error") and str(r.get("outcome", "ok")) in ("ok", "nothing_qualifies")


def _retry(r: dict) -> bool:
    """Solo una falla de RED se repite; un `out_of_tokens` o una respuesta inválida es un resultado del modelo."""
    return "network" in str(r.get("error") or r.get("outcome") or "")


def _done(kind: str, model: str, reasoning: bool) -> int:
    return sum(1 for r in _read() if r["kind"] == kind and r["model"] == model and r["reasoning"] == reasoning
               and not _retry(r))


def _cfg_runs(kind: str, n: int):
    """(modelo, razona) pendientes, en orden: primero la configuración de hoy (el control)."""
    import agent_models
    today = agent_models.DEFAULTS[_seat(kind)]
    order = [today] + [c for c in CONFIGS if c != today]
    for m, r in order:
        for _ in range(max(0, n - _done(kind, m, r))):
            yield m, r


def _seat(kind: str) -> str:
    """El asiento de un `kind` (también los que llevan etiqueta, `--tag`)."""
    return SEAT.get(kind) or ("reviewer" if kind.endswith("_reviewer") else "personal")


def _over_cap() -> bool:
    if _spent() >= USD_CAP:
        print(f"⛔ TOPE: ${_spent():.4f} ≥ ${USD_CAP}; me detengo.", flush=True)
        return True
    return False


# ── check ───────────────────────────────────────────────────────────────────────────────────────

def check() -> int:
    import field_test
    import merchant_verifier
    from db import engine
    from models import Company, Transaction
    with Session(engine) as s:
        try:
            _require_db(s, "contabu_real")
            s.commit = s.flush  # type: ignore[method-assign]
            company = s.get(Company, COMPANY_ID)
            back = field_test.rewind(s, company, SINCE)
            key = back["key"]
            txs = s.exec(select(Transaction).where(Transaction.company_id == company.id)).all()
            by_year = Counter("2026" if t.date.year == 2026 else "antes" for t in txs if t.id in key)
            saved = {r["fingerprint"] for r in json.load(open(field_test._oracle_path(company), encoding="utf-8"))}
            flagged, pending, _mem = merchant_verifier.review_candidates(s, company, txs)
            full = {(t.description or "").strip().upper() for t in txs}
            key_fps = {merchant_verifier.query_for(t.description, full).upper() for t in txs if t.id in key}
            searchable = {c.fingerprint for c in merchant_verifier.candidates(txs) if not c.skip}
            print(f"vuelta al 'antes': {back['reverted']} · cambios quitados {back['changes_removed']} · "
                  f"reglas quitadas {back['rules_removed']}")
            print(f"clave: {len(key)} movimientos (2026: {by_year['2026']} · antes: {by_year['antes']})")
            print(f"comercios de la clave que se pueden buscar: {len(key_fps & searchable)} · con evidencia guardada: "
                  f"{len(key_fps & searchable & saved)}")
            print(f"lo que buscaría Verificar: {len(flagged)} marcados + {len(pending)} Pendientes · con evidencia "
                  f"guardada: {sum(c.fingerprint in saved for c in flagged + pending)}")
        finally:
            s.rollback()
    return 0


# ── ④ en el libro real ─────────────────────────────────────────────────────────────────────────

def drafter(n: int, live_first: bool = False, keep_note: int | None = None, kind: str | None = None) -> int:
    import agent_models
    import field_test
    import merchant_verifier
    from db import engine
    from models import Company
    kind = kind or ("drafter_note" if keep_note is not None else "drafter")
    keep = frozenset({keep_note}) if keep_note is not None else frozenset()
    first = True
    for m, r in _cfg_runs(kind, n):
        if _over_cap():
            break
        mode, first = ("live" if live_first and first else "saved"), False
        with Session(engine) as s:
            try:
                _require_db(s, "contabu_real")
                company = s.get(Company, COMPANY_ID)
                agent_models.OVERRIDES["drafter"] = (m, r)
                merchant_verifier.ORACLE = None
                t0 = time.time()
                try:
                    g = field_test.grade_once(s, company, SINCE, mode, keep_note_ids=keep)
                    err = None
                    size = (g["spans"]["total"]["key"], g["spans"]["2026"]["key"])
                    if size != KEY_SIZE:
                        sys.exit(f"⛔ la clave dio {size}, no {KEY_SIZE}: el libro cambió; me detengo")
                    if keep and g["rewind"]["notes_removed"]:
                        sys.exit("⛔ se borró una nota que debía quedarse; me detengo")
                except Exception as e:   # un modelo que falla cuenta como corrida fallida, no detiene el resto
                    g, err = None, type(e).__name__
            finally:
                s.rollback()
                agent_models.OVERRIDES.pop("drafter", None)
        row = {"kind": kind, "model": m, "reasoning": r, "keep_note": keep_note, "ts": datetime.now().isoformat(timespec="seconds"),
               "wall_s": round(time.time() - t0, 1), "error": err, "web": mode}
        if g:
            row.update({k: g[k] for k in ("outcome", "calls", "proposals", "rejection_counts", "spans", "cost_usd",
                                          "latency_ms", "completion_tokens", "privacy", "rows", "rejected_amounts")})
            row["verify"] = {k: g["verify"][k] for k in ("merchants", "pending", "pending_high_or_medium", "credits")} if g["verify"] else None
        _append(row)
        sp = row.get("spans", {}).get("total", {})
        print(f"{'④+nota' if keep else '④'} {m}{'+r' if r else ''}: {row.get('outcome', err)} · {row.get('proposals', 0)} filas · decide "
              f"{sp.get('decided', 0)}/{sp.get('key', 0)} · acierta {sp.get('right', 0)} · ${row.get('cost_usd', 0):.5f} · "
              f"{(row.get('latency_ms') or 0) / 1000:.1f}s", flush=True)
    return 0


# ── ⑤ en el libro real, con la verdad aproximada ──────────────────────────────────────────────

def _personal_truth(session: Session, company) -> tuple[dict, dict]:
    """llave del comercio → "personal" | "business" (o fuera si se mezcla), y llave → movimientos. Personal = la
    cuenta del libro que dice "personal" o *Shareholder distributions*; negocio = las secciones de gasto."""
    import personal_use
    from models import Transaction
    txs = session.exec(select(Transaction).where(Transaction.company_id == company.id)).all()
    groups: dict[str, list] = {}
    for t in txs:
        if t.is_inflow or not t.category or t.account_type is None:
            continue
        k = personal_use.merchant_key(t)
        if k:
            groups.setdefault(k, []).append(t)
    truth = {}
    for k, g in groups.items():
        kinds = set()
        for t in g:
            cat = t.category.lower()
            if "personal" in cat or "shareholder distribution" in cat:
                kinds.add("personal")
            elif t.account_type.value in personal_use.BUSINESS_SECTIONS:
                kinds.add("business")
            else:
                kinds.add("other")
        if kinds == {"personal"}:
            truth[k] = "personal"
        elif kinds == {"business"}:
            truth[k] = "business"
    return truth, groups


def personal(n: int) -> int:
    import agent_models
    import agents
    import nemotron
    import personal_use
    from db import engine
    from models import Company
    for m, r in _cfg_runs("personal", n):
        if _over_cap():
            break
        with Session(engine) as s:
            try:
                _require_db(s, "contabu_real")
                company = s.get(Company, COMPANY_ID)
                truth, groups = _personal_truth(s, company)
                view = agents.build_view(s, company)
                # Lo que vería el ⑤ si TODO estuviera como gasto del negocio: lo personal primero (son pocos), luego
                # los comercios del negocio por cantidad de movimientos, hasta el mismo tope de producción.
                graded = sorted(truth, key=lambda k: (truth[k] != "personal", -len(groups[k]), k))
                todo = [(k, groups[k]) for k in graded][:personal_use.MAX_MERCHANTS_SENT]
                user, refs = personal_use._user(company, view, todo)
                model_id, reasoning = agent_models.resolve("personal", agent_models.MODEL_IDS[m], r)
                nemotron.SENT.clear()
                t0, err, verdicts, usage = time.time(), None, {}, None
                try:
                    reply = nemotron.chat_json(personal_use._SYSTEM, user, personal_use.JudgmentsOut, model=model_id,
                                               max_tokens=4000, reasoning=reasoning)
                    usage = reply.usage
                    for raw in (reply.data.get("judgments") if isinstance(reply.data, dict) else []) or []:
                        try:
                            j = personal_use.Judgment.model_validate(raw)
                        except Exception:
                            continue
                        if j.ref in refs:
                            verdicts[refs[j.ref][0]] = (j.verdict, j.confidence)
                except nemotron.NemotronError as e:
                    err, usage = e.code, e.usage
                privacy = agents.privacy_scan(list(nemotron.SENT), view.ctx.txs,
                                              __import__("merchant_verifier").protected_names(view.ctx.txs))
            finally:
                s.rollback()
        sent = [k for k, _ in todo]
        alert = {k for k, (v, c) in verdicts.items() if v in personal_use.ALERT_VERDICTS and c in personal_use.ALERT_CONFIDENCE}
        pers = [k for k in sent if truth[k] == "personal"]
        bus = [k for k in sent if truth[k] == "business"]
        row = {"kind": "personal", "model": m, "reasoning": r, "ts": datetime.now().isoformat(timespec="seconds"),
               "wall_s": round(time.time() - t0, 1), "error": err, "sent": len(sent), "answered": len(verdicts),
               "personal_truth": len(pers), "business_truth": len(bus),
               "personal_alerted": sum(k in alert for k in pers), "business_alerted": sum(k in alert for k in bus),
               "verdicts": dict(Counter(v for v, _c in verdicts.values())),
               "cost_usd": (usage.cost_usd if usage else 0.0) or 0.0, "latency_ms": usage.latency_ms if usage else 0,
               "privacy": privacy}
        _append(row)
        print(f"⑤ {m}{'+r' if r else ''}: {err or 'ok'} · contestó {len(verdicts)}/{len(sent)} · personales alertados "
              f"{row['personal_alerted']}/{len(pers)} · del negocio alertados {row['business_alerted']}/{len(bus)} · "
              f"{row['verdicts']} · ${row['cost_usd']:.5f}", flush=True)
    return 0


# ── el examen ciego en la COPIA ─────────────────────────────────────────────────────────────────

def _key_path(exam: str) -> Path:
    return HERE / "private" / EXAMS[exam]["key"] / "key.json"


def _exam_truth(session: Session, company, exam: str = "1") -> list[dict]:
    """La clave → la forma que puntúa `measure.measure_review` (filas por descripción, fecha, monto y dirección),
    con cada fila tomada de la COPIA por su id. `blind` (examen 2): los errores con `blind: false` se puntúan APARTE.
    No se imprime nada de la clave."""
    from models import Transaction
    raw = json.loads(_key_path(exam).read_text(encoding="utf-8"))
    errors = raw if isinstance(raw, list) else next(v for v in raw.values() if isinstance(v, list))
    by_id = {t.id: t for t in session.exec(select(Transaction).where(Transaction.company_id == company.id)).all()}
    out = []
    for e in errors:
        rows = []
        for r in e.get("rows", []):
            t = by_id.get(r.get("id"))
            if t is not None:
                rows.append({"description": t.description, "date": t.date, "amount": t.amount, "is_inflow": t.is_inflow})
        out.append({"type": e.get("type"), "rows": rows, "expected": e.get("expected"),
                    "blind": e.get("blind", True) is not False, "id_rows": len(e.get("rows", []))})
    return out


def _evidence_paths(company, exam: str) -> list[Path]:
    import field_test
    extra = EXAMS[exam]["evidence"]
    return [field_test._oracle_path(company)] + ([field_test.OUT_DIR / extra] if extra else [])


def exam_once(session: Session, company, *, oracle: bool, exam: str = "1", drop_note: bool = False) -> dict:
    """UNA revisión completa sobre la copia, como `measure.py --audit --web oracle`: los veredictos y las opiniones
    del ⑤ de antes se borran DENTRO de la transacción, para que cada modelo juzgue desde cero."""
    import agents
    import field_test
    import measure
    import merchant_verifier
    import nemotron
    from models import BookNote, Finding, MerchantJudgment, Transaction
    session.commit = session.flush  # type: ignore[method-assign]
    if drop_note:   # el equipo de hoy SIN las notas del libro (dentro de la transacción)
        for n in session.exec(select(BookNote).where(BookNote.company_id == company.id)).all():
            session.delete(n)
    for j in session.exec(select(MerchantJudgment).where(MerchantJudgment.company_id == company.id)).all():
        session.delete(j)
    for f in session.exec(select(Finding).where(Finding.company_id == company.id)).all():
        session.delete(f)
    session.flush()
    # La evidencia guardada entra como MEMORIA de la oficina (dentro de la transacción): en vivo solo se busca lo
    # que falta; con `oracle`, lo que falta sale "no encontrado" sin red (0 créditos).
    from models import MerchantLookup
    have = set(merchant_verifier.cached(session, company))
    saved_rows = [r for path in _evidence_paths(company, exam) if path.exists()
                  for r in json.load(open(path, encoding="utf-8"))]
    for r in saved_rows:
        if r["fingerprint"] not in have:
            have.add(r["fingerprint"])
            session.add(MerchantLookup(firm_id=company.firm_id, company_id=None if company.firm_id else company.id,
                                       fingerprint=r["fingerprint"], query=r["fingerprint"],
                                       status="ok" if r.get("results_count") else "not_found",
                                       business_type=r.get("business_type"), confidence=r.get("confidence"),
                                       source_url=r.get("source_url"), results_count=r.get("results_count") or 0,
                                       credits=0, search_depth=r.get("search_depth") or "basic"))
    session.flush()
    merchant_verifier.ORACLE = {} if oracle else None
    nemotron.SENT.clear()
    agents.CITATION_REJECTED.clear()
    truth = _exam_truth(session, company, exam)
    before = set(merchant_verifier.cached(session, company))
    # El flujo de `measure.web_flow` (auditar → ② → ① otra vez) con el tope de búsquedas de ESTE examen.
    agents.review(session, company, lang="en")
    vres = merchant_verifier.verify_flagged(session, company, limit=EXAMS[exam]["web_limit"])
    if vres.finding_ids:
        agents.review(session, company, lang="en", finding_ids=vres.finding_ids)
    web = {"searched": vres.searched, "credits": vres.credits}
    if not oracle:   # la corrida en vivo: su evidencia se SUMA a la guardada, para que las demás la reusen (0 créditos)
        path = _evidence_paths(company, exam)[-1]
        saved = {r["fingerprint"]: r for r in json.load(open(path, encoding="utf-8"))} if path.exists() else {}
        for fp, m in merchant_verifier.cached(session, company).items():
            if fp not in before:
                saved[fp] = {"fingerprint": m.fingerprint, "business_type": m.business_type, "confidence": m.confidence,
                             "source_url": m.source_url, "results_count": m.results_count, "search_depth": m.search_depth}
        with field_test._private_open(path) as fh:
            json.dump(list(saved.values()), fh, ensure_ascii=False, indent=2)
    pend = agents.draft(session, company, record=False)
    rev = measure.measure_review(session, company, truth, draft_rows=[p.row for p in pend.proposals],
                                 already_reviewed=True)
    costs = measure.run_costs(session, [company])
    txs = session.exec(select(Transaction).where(Transaction.company_id == company.id)).all()
    from models import AgentCall
    lat = sum(c.latency_ms for c in session.exec(select(AgentCall).where(
        AgentCall.company_id == company.id, AgentCall.agent != "verifier", AgentCall.created_at >= SINCE)).all())
    by_type = Counter(k.split("#")[0] for k in rev["found_keys"])
    planted_types = Counter(e["type"] for e in truth)
    keys = [f"{e['type']}#{i + 1}" for i, e in enumerate(truth)]
    blind = {k for k, e in zip(keys, truth) if e["blind"]}
    code_keys = set(keys) - set(rev["auditor"]["missed"])
    team_keys = set(rev["found_keys"])
    pers_keys = set(keys) - set(rev["personal"]["missed"])
    privacy = agents.privacy_scan(list(nemotron.SENT), txs, merchant_verifier.protected_names(txs))
    a, rv, team = rev["auditor"], rev["reviewer"], rev["team"]
    return {"planted": rev["planted"], "unmatched": len(rev["unmatched"]),
            "blind_planted": len(blind), "open_planted": len(keys) - len(blind),
            "code_blind": len(code_keys & blind), "code_open": len(code_keys - blind),
            "agents_blind": len(team_keys & blind), "agents_open": len(team_keys - blind),
            "personal_blind": len(pers_keys & blind), "personal_open": len(pers_keys - blind),
            "personal_false_alerts": rev["personal"]["alerts"] - rev["personal"]["true_alerts"],
            "citation_rejected": dict(Counter(agents.CITATION_REJECTED)),
            "rows_in_key": sum(e["id_rows"] for e in truth), "rows_found": sum(len(e["rows"]) for e in truth),
            "code_found": a["found"], "code_alerts": a["alerts"], "code_on_planted": a["true_alerts"],
            "agents_found": team["found"], "agents_alerts": team["alerts"], "agents_on_planted": team["true_alerts"],
            "reviewer_found": rv["found"], "personal_found": rev["personal"]["found"],
            "personal_alerts": rev["personal"]["alerts"], "drafter_found": rev["drafter"]["found"],
            "dismissed_true": len(rev["dismissed_true"]), "judged": rev["judged"],
            "found_by_type": dict(by_type), "planted_by_type": dict(planted_types),
            "searched": web["searched"], "credits_if_live": web["credits"],
            "cost_usd": costs["nebius_usd"] + ((pend.usage.cost_usd or 0.0) if pend.usage else 0.0),
            "nebius_calls": costs["nebius_calls"] + pend.calls,
            "latency_ms": lat + (pend.usage.latency_ms if pend.usage else 0), "privacy": privacy}


def exam(n: int, live_first: bool, exam_id: str = "1", drop_note: bool = False, tag: str | None = None) -> int:
    import agent_models
    from db import engine
    from models import Company
    cfg = EXAMS[exam_id]
    key_path = _key_path(exam_id)
    sha0 = (key_path.parent / "key_frozen_sha1.txt").read_text().split()[0]
    sha = lambda: hashlib.sha1(key_path.read_bytes()).hexdigest()   # noqa: E731
    prefix = cfg["prefix"] + ("_nonote" if drop_note else "") + (f"_{tag}" if tag else "")
    kinds = [(f"{prefix}_reviewer", "reviewer"), (f"{prefix}_personal", "personal")]
    plan = []
    for kind, seat in kinds:
        runs = list(_cfg_runs(kind, n))
        if drop_note or tag:   # sin nota, o una medición con etiqueta: solo el equipo de hoy
            runs = [(m, r) for m, r in runs if (m, r) == agent_models.DEFAULTS[seat]][:n]
            if kind.endswith("_personal"):
                runs = []   # el equipo de hoy es UNO: con el asiento del ① basta
        plan += [(kind, seat, m, r) for m, r in runs]
    first = True
    for kind, seat, m, r in plan:
        if _over_cap():
            break
        if sha() != sha0:
            sys.exit("⛔ la clave cambió (sha1): me detengo")
        with Session(engine) as s:
            try:
                _require_db(s, cfg["db"])
                company = s.get(Company, COMPANY_ID)
                agent_models.OVERRIDES[seat] = (m, r)
                t0 = time.time()
                try:
                    out, err = exam_once(s, company, oracle=not (live_first and first), exam=exam_id,
                                         drop_note=drop_note), None
                except Exception as e:
                    out, err = None, type(e).__name__
            finally:
                s.rollback()
                agent_models.OVERRIDES.pop(seat, None)
        if sha() != sha0:
            sys.exit("⛔ la clave cambió (sha1) durante la corrida: me detengo")
        if out and out.get("credits_if_live", 0) > TAVILY_CAP and live_first and first:
            print(f"⚠️ Tavily: {out['credits_if_live']} créditos (> {TAVILY_CAP})", flush=True)
        row = {"kind": kind, "model": m, "reasoning": r, "ts": datetime.now().isoformat(timespec="seconds"),
               "exam": exam_id, "drop_note": drop_note,
               "wall_s": round(time.time() - t0, 1), "error": err, "web": "live" if (live_first and first) else "saved",
               **(out or {"cost_usd": 0.0})}
        first = False
        _append(row)
        print(f"{kind} {m}{'+r' if r else ''}: {err or 'ok'} · ciegos: código {row.get('code_blind')} · con agentes "
              f"{row.get('agents_blind')} de {row.get('blind_planted')} · no ciegos {row.get('agents_open')}/{row.get('open_planted')} · "
              f"⑤ {row.get('personal_blind')} (+{row.get('personal_false_alerts')} sin clasificar) · "
              f"filas de la clave {row.get('rows_found')}/{row.get('rows_in_key')} · créditos {row.get('credits_if_live')} · "
              f"código solo {row.get('code_found')}/{row.get('planted')} · "
              f"con agentes {row.get('agents_found')}/{row.get('planted')} · ⑤ {row.get('personal_found')} · "
              f"① despachó {row.get('dismissed_true')} verdaderos · ${row.get('cost_usd', 0):.5f} · "
              f"{(row.get('latency_ms') or 0) / 1000:.1f}s", flush=True)
    return 0


# ── tablas ──────────────────────────────────────────────────────────────────────────────────────

def _fmt(vals, f="{}"):
    return " · ".join(f.format(v) for v in vals) if vals else "—"


def table() -> int:
    rows = _read()
    print(f"Gastado en Nebius: ${_spent():.4f} (tope ${USD_CAP})\n")
    known = ["drafter", "drafter_note", "drafter_note_fix", "personal", "exam_reviewer", "exam_personal"]
    kinds = known + sorted({r["kind"] for r in rows} - set(known))
    for kind in kinds:
        mine = [r for r in rows if r["kind"] == kind]
        if not mine:
            continue
        print(f"== {kind}")
        for m, rz in CONFIGS:
            rs = [r for r in mine if r["model"] == m and r["reasoning"] == rz and not _retry(r)]
            if not rs:
                continue
            ok = [r for r in rs if _ok(r)]
            cost = sum(r.get("cost_usd") or 0 for r in rs) / len(rs)
            lat = sum((r.get("latency_ms") or 0) for r in ok) / max(1, len(ok)) / 1000
            name = f"{m}{' razona' if rz else ''}"
            if kind in ("drafter", "drafter_note", "drafter_note_fix"):
                cov = [f"{r['spans']['total']['decided']}/{r['spans']['total']['key']}" for r in ok]
                prec = [f"{r['spans']['total']['right']}/{r['spans']['total']['decided']}" for r in ok]
                c26 = [f"{r['spans']['2026']['decided']}/{r['spans']['2026']['key']}·{r['spans']['2026']['right']}" for r in ok]
                rej = Counter()
                for r in ok:
                    rej.update(r.get("rejection_counts") or {})
                print(f"  {name:16} decide {_fmt(cov)} · acierta {_fmt(prec)} · 2026 (decide·acierta) {_fmt(c26)} · "
                      f"filas {_fmt([r['proposals'] for r in ok])} · rechazos {dict(rej)} · fallas {len(rs) - len(ok)} · "
                      f"${cost:.5f} · {lat:.1f}s")
            elif kind == "personal":
                print(f"  {name:16} personales alertados {_fmt([f'{r['personal_alerted']}/{r['personal_truth']}' for r in ok])} · "
                      f"del negocio alertados {_fmt([f'{r['business_alerted']}/{r['business_truth']}' for r in ok])} · "
                      f"contestó {_fmt([f'{r['answered']}/{r['sent']}' for r in ok])} · fallas {len(rs) - len(ok)} · "
                      f"${cost:.5f} · {lat:.1f}s")
            elif "blind_planted" in (ok[0] if ok else {}):
                b = ok[0]["blind_planted"]
                print(f"  {name:16} CIEGOS (de {b}): código {_fmt([r['code_blind'] for r in ok])} · con agentes "
                      f"{_fmt([r['agents_blind'] for r in ok])} · ⑤ {_fmt([r['personal_blind'] for r in ok])} "
                      f"(⑤ sin clasificar {_fmt([r['personal_false_alerts'] for r in ok])}) · no ciegos (de {ok[0]['open_planted']}) "
                      f"{_fmt([r['agents_open'] for r in ok])} · ① despachó verdaderos {_fmt([r['dismissed_true'] for r in ok])} · "
                      f"alertas sembradas/sin clasificar {_fmt([f'{r['agents_on_planted']}/{r['agents_alerts'] - r['agents_on_planted']}' for r in ok])} · "
                      f"fallas {len(rs) - len(ok)} · ${cost:.5f} · {lat:.1f}s")
            else:
                print(f"  {name:16} código solo {_fmt([r['code_found'] for r in ok])} · con agentes "
                      f"{_fmt([r['agents_found'] for r in ok])} de {ok[0]['planted'] if ok else '?'} · ⑤ "
                      f"{_fmt([r['personal_found'] for r in ok])} · ① despachó verdaderos {_fmt([r['dismissed_true'] for r in ok])} · "
                      f"alertas (sobre sembradas / sin clasificar) {_fmt([f'{r['agents_on_planted']}/{r['agents_alerts'] - r['agents_on_planted']}' for r in ok])} · "
                      f"fallas {len(rs) - len(ok)} · ${cost:.5f} · {lat:.1f}s")
        if kind.endswith(("_reviewer", "_personal")) and any("blind_planted" in r for r in mine):
            _adoption(mine, kind)
        print()
    return 0


def _adoption(mine: list[dict], kind: str) -> None:
    """La regla de siempre: un candidato gana si su PEOR corrida supera la MEJOR del modelo de hoy (errores ciegos)."""
    import agent_models
    today = agent_models.DEFAULTS[_seat(kind)]
    score = lambda m, r: [x["agents_blind"] for x in mine if x["model"] == m and x["reasoning"] == r and _ok(x)]   # noqa: E731
    best_today = max(score(*today), default=None)
    if best_today is None:
        return
    wins = [f"{m}{' razona' if r else ''}" for m, r in CONFIGS if (m, r) != today and score(m, r) and min(score(m, r)) > best_today]
    print(f"  → regla de adopción (peor del candidato > mejor de hoy = {best_today}): {', '.join(wins) or 'ninguno gana'}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=("check", "drafter", "personal", "exam", "table"))
    ap.add_argument("--n", type=int, default=3, help="corridas por configuración")
    ap.add_argument("--exam", choices=tuple(EXAMS), default="1", help="exam: cuál examen sembrado (base, clave, kind)")
    ap.add_argument("--tag", help="exam: medición con etiqueta (solo el equipo de hoy, kind aparte)")
    ap.add_argument("--drop-note", action="store_true", help="exam: el equipo de hoy con las notas borradas (en la transacción)")
    ap.add_argument("--keep-note", type=int, help="drafter: conservar esta nota al volver al 'antes' (kind drafter_note)")
    ap.add_argument("--live-first", action="store_true",
                    help="drafter/exam: la PRIMERA corrida busca en Tavily de verdad y guarda la evidencia; las demás la reusan")
    args = ap.parse_args()
    if args.what == "table":
        return table()
    if args.what == "check":
        return check()
    if args.what == "drafter":
        return drafter(args.n, args.live_first, args.keep_note)
    if args.what == "personal":
        return personal(args.n)
    return exam(args.n, args.live_first, args.exam, args.drop_note, args.tag)


if __name__ == "__main__":
    sys.exit(main())
