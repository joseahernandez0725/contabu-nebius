"""
ContaBu — LAS NOTAS DEL LIBRO (FASE 11, área Auditoría / IA · 4-oct-2026).

Lo que el bookkeeper le escribe al agente sobre cada cliente: lo que hay que recordar y lo
prioritario. Dos tipos:

- 📌 `keep_in_mind` (tenlo en cuenta) — para que el agente encuentre más.
  *"The owner was on vacation in Europe from Aug 3 to Aug 17"*
- 🚫 `dont_report` (no me reportes) — para corregir al agente.
  *"Large Costco purchases are inventory: don't flag them"*

🔴 Una nota NUNCA categoriza. Es la entrada del agente ④ (el redactor de "la fórmula del libro",
`docs/FASE 11.md` → Actualización 3-oct): el agente la lee y PROPONE filas de la fórmula, que el
bookkeeper aprueba. Reemplaza a las preguntas del comienzo (la entrevista, eliminada el 2-oct).

Determinista, sin IA. La validación vive aquí (no en el endpoint) para que la siembra del demo y la
API pasen por la MISMA puerta.
"""

from datetime import date, datetime, timezone
from typing import Optional

from sqlmodel import Session, select

from models import BookNote

KINDS = ("keep_in_mind", "dont_report")
PRIORITIES = ("high", "normal")

MAX_TEXT = 1000        # una nota es un recordatorio, no un documento
MAX_MERCHANT = 80
MAX_NOTES_PER_BOOK = 100   # tope para que el prompt del agente no crezca sin control


class NoteError(ValueError):
    """Nota inválida. `code` es estable y en inglés: el frontend lo traduce."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _day(v) -> Optional[date]:
    if v in (None, ""):
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v))
    except ValueError:
        raise NoteError("bad_date")


def clean(fields: dict, current: Optional[BookNote] = None) -> dict:
    """Valida y normaliza los campos de una nota (crear o editar). Con `current`, los campos que no
    vienen se toman de la nota existente, para validar la vigencia completa."""
    out: dict = {}
    if "text" in fields or current is None:
        text = (fields.get("text") or "").strip()
        if not text:
            raise NoteError("empty_text")
        if len(text) > MAX_TEXT:
            raise NoteError("text_too_long")
        out["text"] = text
    if "kind" in fields or current is None:
        kind = fields.get("kind") or "keep_in_mind"
        if kind not in KINDS:
            raise NoteError("bad_kind")
        out["kind"] = kind
    if "priority" in fields or current is None:
        priority = fields.get("priority") or "normal"
        if priority not in PRIORITIES:
            raise NoteError("bad_priority")
        out["priority"] = priority
    if "merchant" in fields:
        merchant = (fields.get("merchant") or "").strip().upper() or None
        if merchant and len(merchant) > MAX_MERCHANT:
            raise NoteError("merchant_too_long")
        out["merchant"] = merchant
    for key in ("valid_from", "valid_to"):
        if key in fields:
            out[key] = _day(fields.get(key))
    start = out.get("valid_from", current.valid_from if current else None)
    end = out.get("valid_to", current.valid_to if current else None)
    if start and end and start > end:
        raise NoteError("bad_window")
    return out


def list_notes(session: Session, company_id: int) -> list[BookNote]:
    """Las notas del libro: primero las de prioridad alta, luego las más recientes."""
    notes = session.exec(select(BookNote).where(BookNote.company_id == company_id)).all()
    return sorted(notes, key=lambda n: (n.priority != "high", -(n.id or 0)))


def create_note(session: Session, company_id: int, fields: dict,
                user_id: Optional[int] = None) -> BookNote:
    """Crea una nota validada. No hace commit (lo hace quien llama)."""
    count = len(session.exec(select(BookNote.id).where(BookNote.company_id == company_id)).all())
    if count >= MAX_NOTES_PER_BOOK:
        raise NoteError("too_many_notes")
    note = BookNote(company_id=company_id, created_by_user_id=user_id, **clean(fields))
    session.add(note)
    return note


def update_note(session: Session, note: BookNote, fields: dict) -> BookNote:
    for key, value in clean(fields, current=note).items():
        setattr(note, key, value)
    note.updated_at = datetime.now(timezone.utc)
    session.add(note)
    return note


def seed_notes(session: Session, company_id: int, notes: list[dict]) -> list[BookNote]:
    """Siembra notas (el demo: `demo_data.DEMO_NOTES[nombre del libro]`). Pasa por la MISMA
    validación que la API. Solo `add`: el `flush`/`commit` es de quien llama."""
    return [create_note(session, company_id, n) for n in notes]


def active_notes(session: Session, company_id: int, on: Optional[date] = None) -> list[BookNote]:
    """Las notas que el agente debe leer: todas, salvo las PAUSADAS (18-oct) y las que ya VENCIERON
    antes de `on` (por defecto, nunca vence ninguna: el agente revisa la historia completa del libro, y
    una nota de agosto sigue siendo verdad sobre agosto). Prioridad alta primero."""
    notes = [n for n in list_notes(session, company_id) if n.paused_at is None]
    if on is None:
        return notes
    return [n for n in notes if n.valid_to is None or n.valid_to >= on]


def silencing_merchants(session: Session, company_id: int) -> list[str]:
    """Los comercios que callan las notas 🚫 "no me reportes" ACTIVAS (en código, no en un prompt). Una
    sola regla para la pestaña Auditoría, el panel de la transacción y la página de Notas."""
    return [n.merchant.upper() for n in active_notes(session, company_id)
            if n.kind == "dont_report" and n.merchant]


def set_paused(session: Session, note: BookNote, paused: bool) -> BookNote:
    """Pausar / activar (18-oct). Pausada, ningún agente la lee y el código no la aplica; el texto y su
    chequeo se quedan como estaban: al activarla, si nada cambió, no se vuelve a pagar la traducción."""
    if paused and note.paused_at is None:
        note.paused_at = datetime.now(timezone.utc).replace(tzinfo=None)
    elif not paused:
        note.paused_at = None
    session.add(note)
    return note


def to_dict(note: BookNote) -> dict:
    return {
        "id": note.id,
        "kind": note.kind,
        "priority": note.priority,
        "text": note.text,
        "merchant": note.merchant,
        "valid_from": note.valid_from.isoformat() if note.valid_from else None,
        "valid_to": note.valid_to.isoformat() if note.valid_to else None,
        "created_at": note.created_at.isoformat() if note.created_at else None,
        "updated_at": note.updated_at.isoformat() if note.updated_at else None,
        "paused_at": note.paused_at.isoformat() if note.paused_at else None,
    }
