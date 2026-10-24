"""
ContaBu — LA NOTA COMO CHEQUEO DEL AUDITOR (FASE 11, Auditoría / IA · 13-oct-2026).

El bookkeeper escribe una nota 📌 en lenguaje natural ("Supermarket purchases over $50 are usually
personal: review them"). Nemotron la traduce UNA vez a un chequeo estructurado; el auditor lo aplica en
código, gratis, cada vez que revisa el libro (`book_review.detect` → hallazgo `note_check`).

Lo que el modelo puede devolver está cerrado y se valida aquí:
- `business_types`: solo nombres de la lista cerrada del ② (`merchant_verifier.BUSINESS_TYPES`);
- `merchants`: solo palabras clave COPIADAS de la lista de comercios del libro que se le mandó;
- `amount_over`: solo si ese número está ESCRITO en la nota (un monto inventado tira el chequeo entero);
- sin comercios ni tipos, el chequeo no se aplica (marcaría todo el libro).
La ventana de fechas no la decide el modelo: es la de la nota.

Al modelo entra lo mismo que al ④: el texto de la nota (con los nombres de persona ya enmascarados), las
huellas de comercio y su tipo verificado. Ningún monto, ninguna descripción completa. Un chequeo NUNCA
categoriza: solo señala filas para que el bookkeeper las mire.

El chequeo se guarda en `BookNote.audit_check` con el texto y la ventana de los que salió: si la nota
cambia, queda viejo y se rehace en la siguiente revisión.
"""

import json
import re
from typing import Literal, Optional

from pydantic import BaseModel, Field
from sqlmodel import Session

import agent_calls
import agent_models
import book_notes
import merchant_verifier
import nemotron
from models import BookNote, Company

CHECK_VERSION = 2   # 24-oct: prompt reescrito; los chequeos guardados se rehacen
MAX_MERCHANTS_SENT = 60


class NoteCheck(BaseModel):
    # `applies` va AL FINAL. Medido el 13-oct (A/B, 6 llamadas por orden): el orden no cambia lo que el
    # modelo pone en las listas (eso lo cambia el razonamiento, ver `refresh`), pero este campo opcional
    # puesto PRIMERO se omitió en 6 de 6 respuestas; al final se llenó en 6 de 6.
    note_id: int
    business_types: list[str] = Field(default_factory=list)
    merchants: list[str] = Field(default_factory=list)
    amount_over: Optional[float] = None
    meaning: Literal["personal", "review"] = "review"
    direction: Literal["out", "in", "any"] = "out"
    applies: bool = False


class ChecksOut(BaseModel):
    checks: list[NoteCheck]


_SYSTEM = """You turn bookkeeper NOTES about a client book into CHECKS that a deterministic auditor applies to the book's
rows. A check never changes a category: it only FLAGS rows for the bookkeeper to look at.

For each note, fill:
- business_types: ONLY names from "business_types" below, for the kinds of merchant the note talks about (e.g.
  "pharmacy purchases"). [] if the note names no kind of merchant.
- merchants: ONLY keywords copied EXACTLY from "merchants" below that the note covers, by name or because the
  merchant is the kind of business the note talks about. [] if none. Never a placeholder like "PERSON 1".
- amount_over: ONLY if the note itself writes an amount threshold; otherwise null. Never invent one.
- meaning: "personal" if the note says those purchases are personal (not business); otherwise "review".
- direction: "out" for purchases or payments, "in" for money received, "any" otherwise.
- applies: true if you filled business_types or merchants; false if the note describes no set of transactions
  that can be checked. A note that names a kind of merchant, or a period whose purchases it describes, IS
  checkable.
- For a note with a date period, merchants marked "only_seen_in_period" appear ONLY in that period (e.g. a trip);
  the others are the business's usual merchants. The code applies the period itself: do not use it to pick
  merchants outside it.

Output ONLY JSON: {"checks": [{"note_id", "business_types", "merchants", "amount_over", "meaning", "direction",
"applies"}]}, exactly one per note."""


def _numbers(text: str) -> set[float]:
    return {float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*(?:\.\d+)?", text or "")}


def _stamp(n: BookNote) -> dict:
    return {"version": CHECK_VERSION, "for_text": n.text,
            "for_from": n.valid_from.isoformat() if n.valid_from else None,
            "for_to": n.valid_to.isoformat() if n.valid_to else None}


def is_current(n: BookNote) -> bool:
    """¿El chequeo guardado salió de ESTA versión de la nota?"""
    c = n.audit_check or {}
    return bool(c) and c == {**c, **_stamp(n)}


def clean(raw: NoteCheck, n: BookNote, allowed: set[str], period_only: Optional[set[str]] = None) -> dict:
    """Lo que el modelo propuso, recortado a lo permitido. `applies=False` con el motivo si no vale.

    Nota CON periodo (un viaje, unas vacaciones): solo cuentan los comercios que aparecen ÚNICAMENTE en
    ese periodo (`period_only`, lo cuenta el código), y ningún tipo de comercio: el negocio sigue
    funcionando mientras el dueño viaja, y su renta o su gasolinera de siempre no son gasto personal.
    Es el mismo criterio con que el ④ arma las excepciones con ventana. (✅ 13-oct: el modelo devolvía
    TODOS los comercios del periodo, aunque se le marcaba cuáles solo aparecían ahí.)"""
    merchants = {m.strip().upper() for m in raw.merchants if m.strip().upper() in allowed and "PERSON " not in m.upper()}
    types = {t for t in raw.business_types if t in merchant_verifier.BUSINESS_TYPES}
    if n.valid_from or n.valid_to:
        merchants &= period_only or set()
        types = set()
    out = {**_stamp(n), "applies": False, "meaning": raw.meaning, "direction": raw.direction,
           "business_types": sorted(types), "merchants": sorted(merchants),
           "amount_over": raw.amount_over, "dropped": None}
    if not raw.applies:
        out["dropped"] = "not_checkable"
    elif raw.amount_over is not None and float(raw.amount_over) not in _numbers(n.text):
        out["dropped"] = "invented_amount"
    elif not out["business_types"] and not out["merchants"]:
        out["dropped"] = "unbounded"
    else:
        out["applies"] = True
    return out


def _user(view, notes: list[BookNote]) -> tuple[str, set[str]]:
    by_id = {x["id"]: x for x in view.notes}
    merchants, seen = [], set()
    for m in view.merchants[:MAX_MERCHANTS_SENT]:
        if m.get("person_payment") or m["keyword"] in seen:
            continue
        seen.add(m["keyword"])
        item = {"keyword": m["keyword"], "direction": m["direction"]}
        if m.get("web") and m["web"].get("confidence") in ("high", "medium"):
            item["web_business_type"] = m["web"]["business_type"]
        merchants.append(item)
    payload_notes = []
    for n in notes:
        v = by_id.get(n.id, {})
        item = {"note_id": n.id, "text": v.get("text", ""), "valid_from": v.get("valid_from"),
                "valid_to": v.get("valid_to")}
        # Con periodo: los comercios con movimientos DENTRO, y si solo aparecen ahí (lo cuenta el código).
        inside = [{"keyword": m["keyword"], "only_seen_in_period": m["only_seen_in_period"]}
                  for m in v.get("merchants_inside_period", []) if "PERSON " not in m["keyword"]]
        if inside:
            item["merchants_inside_period"] = inside
        payload_notes.append(item)
    user = (f"business_types: {json.dumps(sorted(merchant_verifier.BUSINESS_TYPES))}\n\n"
            f"merchants: {json.dumps(merchants, ensure_ascii=False)}\n\n"
            f"notes: {json.dumps(payload_notes, ensure_ascii=False)}")
    allowed = {m["keyword"].upper() for m in merchants}
    allowed |= {m["keyword"].upper() for x in payload_notes for m in x.get("merchants_inside_period", [])}
    return user, allowed


def _period_only(view, note_id: int) -> set[str]:
    v = next((x for x in view.notes if x["id"] == note_id), {})
    return {m["keyword"].upper() for m in v.get("merchants_inside_period", []) if m["only_seen_in_period"]}


def refresh(session: Session, company: Company, view, *, model: Optional[str] = None,
            user_id: Optional[int] = None, reasoning: Optional[bool] = None) -> Optional[nemotron.Usage]:
    """Traduce las notas 📌 sin chequeo (o con uno viejo) en UNA llamada. Sin nada que traducir, no llama.
    Si se acabó el tope del día o la llamada falla, no guarda nada (se reintenta en la próxima revisión).
    No hace commit (lo hace quien llama)."""
    todo = [n for n in book_notes.active_notes(session, company.id)
            if n.kind == "keep_in_mind" and not is_current(n)]
    if not todo:
        return None
    try:
        agent_calls.check_cap(session, company)
    except agent_calls.CapReached:
        return None
    user, allowed = _user(view, todo)
    model, reasoning = agent_models.resolve("notes", model, reasoning)
    # CON razonamiento: sin él, Nano no llevó "supermarket" a `grocery` en 0 de 6 llamadas; con él, en 6
    # de 6 (✅ 13-oct, nota del supermercado del demo). Es una llamada por nota NUEVA o cambiada, no por
    # revisión: el costo medido fue ~$0.0003 por nota.
    try:
        reply = nemotron.chat_json(_SYSTEM, user, ChecksOut, model=model, max_tokens=1500, reasoning=reasoning)
    except nemotron.NemotronError as e:
        agent_calls.record(session, company, "note_checker", e.usage, outcome=e.code, user_id=user_id,
                           model=model or nemotron.model_name())
        return e.usage
    got = {}
    for c in (reply.data.get("checks") if isinstance(reply.data, dict) else []) or []:
        try:
            ok = NoteCheck.model_validate(c)
        except Exception:
            continue
        got[ok.note_id] = ok
    valid = 0
    for n in todo:
        raw = got.get(n.id)
        n.audit_check = clean(raw, n, allowed, _period_only(view, n.id)) if raw else {**_stamp(n), "applies": False, "dropped": "no_answer"}
        valid += bool(n.audit_check.get("applies"))
        session.add(n)
    agent_calls.record(session, company, "note_checker", reply.usage, user_id=user_id, items_out=len(got),
                       items_valid=valid)
    return reply.usage


# ── La página de Notas (18-oct, ronda de diseño 2): qué entendió la IA · probar antes de guardar · qué tocó ──

def understood(n: BookNote) -> Optional[dict]:
    """Lo que la IA entendió de una nota 📌, para la pantalla (los campos del chequeo, sin el sello). `None`
    si es 🚫 (esa la aplica el código, sin IA) o si todavía no se tradujo. `current`: salió de ESTE texto."""
    if n.kind != "keep_in_mind" or not n.audit_check:
        return None
    c = n.audit_check
    keys = ("applies", "meaning", "direction", "business_types", "merchants", "amount_over", "dropped")
    return {**{k: c.get(k) for k in keys}, "current": is_current(n)}


# Lo que dio "Probar", por libro y versión de la nota: si el bookkeeper la guarda tal cual dentro de la hora,
# el chequeo se adopta y la siguiente revisión no vuelve a pagar la traducción. En memoria del proceso, a
# propósito: con varios procesos, el que no lo tenga traduce como siempre (nada se rompe, cuesta 1 llamada).
_PREVIEWED: dict[tuple, tuple[float, dict]] = {}
PREVIEW_TTL_S = 3600


def _preview_key(company_id: int, text: str, valid_from, valid_to) -> tuple:
    return (company_id, text, valid_from.isoformat() if valid_from else None,
            valid_to.isoformat() if valid_to else None)


def adopt_preview(n: BookNote) -> bool:
    """Al guardar: si esta versión de la nota se probó hace poco, su chequeo queda guardado. No hace commit."""
    import time
    hit = _PREVIEWED.get(_preview_key(n.company_id, n.text, n.valid_from, n.valid_to))
    if n.kind != "keep_in_mind" or hit is None or time.time() - hit[0] > PREVIEW_TTL_S or is_current(n):
        return False
    n.audit_check = {**hit[1], **_stamp(n)}
    return True


def _brief(f, by_id: dict) -> dict:
    import book_review
    return {"finding_id": f.id, "type": f.type, "status": f.status, "merchant": f.merchant,
            "rows": len(f.transaction_ids or []),
            "transactions": [book_review.tx_summary(by_id[i]) for i in (f.transaction_ids or []) if i in by_id][:30]}


def _silenced_by(open_findings: list, merchant: Optional[str], by_id: dict) -> list[dict]:
    m = (merchant or "").upper()
    return [_brief(f, by_id) for f in open_findings if m and m in (f.merchant or "").upper()]


def preview(session: Session, company: Company, fields: dict, *, note_id: Optional[int] = None,
            user_id: Optional[int] = None) -> dict:
    """PROBAR una nota antes de guardarla (o un cambio a una guardada): qué entendería la IA y qué filas
    marcaría (📌) o qué hallazgos callaría (🚫). No guarda la nota ni ningún hallazgo; lo único que queda
    escrito es la llamada a Nemotron en `agentcall` (su costo, como toda llamada).

    Cuesta 1 llamada solo si es 📌 y esa versión del texto no tiene ya un chequeo (guardado o probado en la
    última hora); 🚫 nunca llama: el silencio lo aplica el código. Lanza `book_notes.NoteError` si la nota
    no es válida, `LookupError` si `note_id` no es de este libro y `agent_calls.CapReached` sin cupo."""
    import time
    import agents
    import book_review
    from sqlmodel import select
    from models import Transaction

    current = None
    if note_id is not None:
        current = session.get(BookNote, note_id)
        if current is None or current.company_id != company.id:
            raise LookupError("note not found")
    base = ({k: getattr(current, k) for k in ("kind", "priority", "text", "merchant", "valid_from", "valid_to")}
            if current else {"kind": "keep_in_mind", "priority": "normal", "merchant": None,
                             "valid_from": None, "valid_to": None})
    note = {**base, **book_notes.clean(fields, current=current)}
    txs = session.exec(select(Transaction).where(Transaction.company_id == company.id)).all()
    by_id = {t.id: t for t in txs}
    out = {"kind": note["kind"], "understood": None, "flagged": [], "silenced": [], "cached": True}

    if note["kind"] == "dont_report":
        nested = session.begin_nested()   # `sync` escribe hallazgos: probar no deja nada
        try:
            out["silenced"] = _silenced_by(book_review.sync(session, company), note["merchant"], by_id)
        finally:
            nested.rollback()
        out["needs_merchant"] = not note["merchant"]
        return out

    shadow = BookNote(id=current.id if current else None, company_id=company.id, kind="keep_in_mind",
                      text=note["text"], valid_from=note["valid_from"], valid_to=note["valid_to"])
    key = _preview_key(company.id, shadow.text, shadow.valid_from, shadow.valid_to)
    hit = _PREVIEWED.get(key)
    if current is not None and current.kind == "keep_in_mind" and current.text == shadow.text \
            and current.valid_from == shadow.valid_from and current.valid_to == shadow.valid_to and is_current(current):
        check = current.audit_check
    elif hit is not None and time.time() - hit[0] <= PREVIEW_TTL_S:
        check = hit[1]
    else:
        # El prompt se arma con la nota DENTRO del libro (así cuenta qué comercios caen en su periodo) y se
        # deshace antes de llamar: lo único que viaja es lo mismo que en una revisión (nota enmascarada y
        # huellas de comercio).
        nested = session.begin_nested()
        try:
            if current is not None:
                for k in ("kind", "text", "valid_from", "valid_to"):
                    setattr(current, k, note[k])
                current.paused_at = None
                session.add(current)
                tmp = current
            else:
                tmp = BookNote(company_id=company.id, **{k: note[k] for k in ("kind", "priority", "text", "merchant",
                                                                             "valid_from", "valid_to")})
                session.add(tmp)
            session.flush()
            view = agents.build_view(session, company)
            user, allowed = _user(view, [tmp])
            period = _period_only(view, tmp.id)
            shadow.id = tmp.id
        finally:
            nested.rollback()
        agent_calls.check_cap(session, company)
        model, reasoning = agent_models.resolve("notes", None, None)
        try:
            reply = nemotron.chat_json(_SYSTEM, user, ChecksOut, model=model, max_tokens=1500, reasoning=reasoning)
        except nemotron.NemotronError as e:
            agent_calls.record(session, company, "note_checker", e.usage, outcome=e.code, user_id=user_id,
                               model=model or nemotron.model_name())
            session.commit()
            raise
        raw = None
        for c in (reply.data.get("checks") if isinstance(reply.data, dict) else []) or []:
            try:
                ok = NoteCheck.model_validate(c)
            except Exception:
                continue
            if ok.note_id == shadow.id:
                raw = ok
        check = clean(raw, shadow, allowed, period) if raw else {**_stamp(shadow), "applies": False, "dropped": "no_answer"}
        agent_calls.record(session, company, "note_checker", reply.usage, user_id=user_id,
                           items_out=1 if raw else 0, items_valid=int(bool(check.get("applies"))))
        session.commit()
        _PREVIEWED[key] = (time.time(), check)
        out["cached"] = False

    keys = ("applies", "meaning", "direction", "business_types", "merchants", "amount_over", "dropped")
    out["understood"] = {**{k: check.get(k) for k in keys}, "current": True}
    if check.get("applies"):
        web = merchant_verifier.web_by_tx(session, company, txs)
        groups = book_review.note_check_groups(check, shadow.valid_from, shadow.valid_to, txs, web)
        out["flagged"] = [{"merchant": m, "rows": len(g),
                           "transactions": [book_review.tx_summary(t) for t in sorted(g, key=lambda t: t.date)][:30]}
                          for m, g in sorted(groups.items(), key=lambda kv: -len(kv[1]))]
    return out


def effects(session: Session, company: Company, n: BookNote) -> dict:
    """QUÉ TOCÓ una nota guardada: los hallazgos que marcó (📌, en cualquier estado: abierto, "está bien" o
    que ya no se ve) y los que calla hoy (🚫 con comercio, si está activa). Pone los hallazgos al día como
    lo hace la pestaña Auditoría (`book_review.board`)."""
    import book_review
    from sqlmodel import select
    from models import Finding, Transaction
    open_findings = book_review.sync(session, company)
    session.commit()
    txs = session.exec(select(Transaction).where(Transaction.company_id == company.id)).all()
    by_id = {t.id: t for t in txs}
    flagged = []
    if n.kind == "keep_in_mind":
        rows = session.exec(select(Finding).where(Finding.company_id == company.id, Finding.type == "note_check")).all()
        order = {"open": 0, "ok": 1, "gone": 2}
        flagged = [_brief(f, by_id) for f in sorted((f for f in rows if (f.payload or {}).get("note_id") == n.id),
                                                    key=lambda f: (order.get(f.status, 3), f.merchant or ""))]
    silenced = (_silenced_by(open_findings, n.merchant, by_id)
                if n.kind == "dont_report" and n.paused_at is None else [])
    return {"flagged": flagged, "silenced": silenced}
