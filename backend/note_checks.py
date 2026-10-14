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
import book_notes
import merchant_verifier
import nemotron
from models import BookNote, Company

CHECK_VERSION = 1
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


_SYSTEM = """You turn bookkeeper NOTES about a client book into CHECKS that a deterministic auditor applies
to the book's rows. A check never changes a category: it only FLAGS rows for the bookkeeper to look at.

Rules:
- Output ONLY JSON: {"checks": [{"note_id", "business_types", "merchants", "amount_over", "meaning",
  "direction", "applies"}]}, exactly one per note.
- A note that names a kind of merchant (e.g. "supermarket purchases"), or a period whose purchases it
  describes, IS checkable: fill business_types and/or merchants first.
- meaning: "personal" if the note says those purchases are personal (not business); otherwise "review".
- business_types: ONLY names from "business_types" below, for the kinds of merchant the note talks about.
  [] if the note names no kind of merchant.
- merchants: ONLY keywords copied EXACTLY from "merchants" below that the note covers, by name or because
  that merchant is the kind of business the note talks about. [] if none.
- amount_over: ONLY if the note itself writes an amount threshold; otherwise null. Never invent one.
- direction: "out" for purchases or payments, "in" for money received, "any" otherwise.
- For a note with a date period, merchants marked "only_seen_in_period" appear ONLY in that period
  (e.g. a trip); the others are the business's usual merchants.
- applies: true if you filled business_types or merchants; false if the note describes no set of
  transactions that can be checked.
- The note's own date period is applied by the code: do not use it to pick merchants outside it.
- Placeholders like "PERSON 1" stand for private people: never put them in "merchants"."""


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
            user_id: Optional[int] = None) -> Optional[nemotron.Usage]:
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
    # CON razonamiento: sin él, Nano no llevó "supermarket" a `grocery` en 0 de 6 llamadas; con él, en 6
    # de 6 (✅ 13-oct, nota del supermercado del demo). Es una llamada por nota NUEVA o cambiada, no por
    # revisión: el costo medido fue ~$0.0003 por nota.
    try:
        reply = nemotron.chat_json(_SYSTEM, user, ChecksOut, model=model, max_tokens=1500, reasoning=True)
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
