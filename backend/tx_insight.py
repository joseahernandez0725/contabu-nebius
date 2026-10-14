"""
ContaBu — EL PANEL DE LA TRANSACCIÓN (FASE 13, área Firm · 13-oct-2026).

Decisión de Jose (tablero, 13-oct): en Bookkeeping, seleccionar un movimiento abre un panel a la derecha
con todo lo que se sabe de él. Reemplaza a la celda Observación que se expandía.

Este módulo SOLO JUNTA lo que ya calculan otros, sin cambiarles la lógica:
- los hallazgos guardados, el veredicto del ① y la evidencia del ② → `book_review` (Auditoría / IA);
- la evidencia web por movimiento → `merchant_verifier.web_by_tx`;
- las notas del libro → `book_notes`;
- el historial ANTES → DESPUÉS → `CategorizationChange` (Movimientos).

Solo lectura. Lo único que escribe es `flags()`, que llama a `book_review.sync` — lo mismo que hace la
pestaña Auditoría al abrirse — para que los puntitos de la hoja no queden viejos después de una edición.
"""

from typing import Optional

from sqlmodel import Session, select

import book_notes
import book_review
import merchant_verifier
from models import CategorizationChange, CategoryRule, Company, Finding, Transaction, User

# El puntito de la fila. El nivel más alto gana: problema > por revisar > observación.
LEVEL_ORDER = {"problem": 3, "review": 2, "info": 1}


def _silencers(session: Session, company: Company) -> list[str]:
    """Las notas 🚫 "no me reportes" con comercio. MISMA regla que `book_review.board()`: lo que la
    pestaña Auditoría esconde, el panel y los puntitos también lo esconden."""
    return [n.merchant.upper() for n in book_notes.list_notes(session, company.id)
            if n.kind == "dont_report" and n.merchant]


def _shown(findings: list[Finding], silencers: list[str]) -> list[Finding]:
    return [f for f in findings if not any(x in (f.merchant or "").upper() for x in silencers)]


def finding_level(f: Finding) -> str:
    """Rojo / ámbar / azul, con el MISMO criterio que el contador "problemas" de Auditoría
    (`book_review.board()["stats"]["problems"]`): el ① dijo problema, o no lo ha visto y es grave."""
    if f.type in book_review.INFO_TYPES:
        return "info"
    if f.agent_verdict == "problem" or (f.agent_verdict is None and f.severity == "high"):
        return "problem"
    return "review"


def flags(session: Session, company: Company) -> dict[int, str]:
    """Movimiento → nivel de su puntito ("problem" | "review" | "info"). Sin entrada = nada que mirar.
    Pone la tabla de hallazgos al día antes (como la pestaña Auditoría), así después de corregir una
    celda el puntito desaparece en la recarga."""
    open_findings = book_review.sync(session, company)
    session.commit()
    out: dict[int, str] = {}
    for f in _shown(open_findings, _silencers(session, company)):
        level = finding_level(f)
        for i in f.transaction_ids or []:
            if LEVEL_ORDER[level] > LEVEL_ORDER.get(out.get(i, ""), 0):
                out[i] = level
    # El banco vinculado RETIRÓ la fila (Movimientos, 4-oct): siempre es un problema a decidir.
    for tid in session.exec(select(Transaction.id).where(Transaction.company_id == company.id,
                                                          Transaction.bank_removed_at != None)).all():  # noqa: E711
        out[tid] = "problem"
    return out


def _provenance(session: Session, tx: Transaction) -> dict:
    """DE DÓNDE salió la categoría: regla (con cuál) · escrita a mano · transferencia · Pendiente."""
    if not tx.category or tx.account_type is None:
        return {"kind": "pending", "rule": None}
    if tx.source == "manual":
        return {"kind": "manual", "rule": None}
    if tx.source == "transfer":
        return {"kind": "transfer", "rule": None}
    if tx.matched_rule_id is not None:
        r = session.get(CategoryRule, tx.matched_rule_id)
        rule = None
        if r is not None and r.company_id == tx.company_id:
            rule = {
                "id": r.id, "keyword": r.match_text,
                "account_type": r.account_type.value if r.account_type else None,
                "category": r.category, "subcategory": r.subcategory, "priority": r.priority,
                "valid_from": r.valid_from.isoformat() if r.valid_from else None,
                "valid_to": r.valid_to.isoformat() if r.valid_to else None,
                "amount_min": float(r.amount_min) if r.amount_min is not None else None,
                "amount_max": float(r.amount_max) if r.amount_max is not None else None,
                "origin": r.origin,
            }
        return {"kind": "rule", "rule": rule}       # rule=None: la regla ya no existe
    return {"kind": "other", "source": tx.source, "rule": None}


def _history(session: Session, tx: Transaction) -> list[dict]:
    """Sus correcciones (`CategorizationChange`), la más reciente primero: quién cambió qué y por qué vía."""
    rows = session.exec(select(CategorizationChange)
                        .where(CategorizationChange.transaction_id == tx.id,
                               CategorizationChange.company_id == tx.company_id)
                        .order_by(CategorizationChange.changed_at.desc(), CategorizationChange.id.desc())).all()
    uids = {c.changed_by_user_id for c in rows if c.changed_by_user_id is not None}
    emails = {u.id: u.email for u in session.exec(select(User).where(User.id.in_(uids))).all()} if uids else {}
    return [{
        "id": c.id, "changed_at": c.changed_at.isoformat(), "via": c.via,
        "by": emails.get(c.changed_by_user_id) if c.changed_by_user_id is not None else None,
        "by_system": c.changed_by_user_id is None,
        "before": {"account_type": c.account_type_before, "category": c.category_before,
                   "subcategory": c.subcategory_before, "source": c.source_before},
        "after": {"account_type": c.account_type_after, "category": c.category_after,
                  "subcategory": c.subcategory_after, "source": c.source_after},
        "reason": c.reason,
    } for c in rows[:50]]


def _notes(session: Session, company: Company, tx: Transaction, merchant: str) -> list[dict]:
    """Las notas del libro que LE APLICAN: las que nombran su comercio y están vigentes en su fecha, y
    las generales que tienen una ventana de fechas que la cubre. Una nota general sin fechas vale para
    todo el libro: mostrarla en cada movimiento sería ruido (vive en la pestaña Auditoría)."""
    text = f"{tx.description or ''} {tx.description_extra or ''} {merchant}".upper()
    out = []
    for n in book_notes.list_notes(session, company.id):
        if (n.valid_from and tx.date < n.valid_from) or (n.valid_to and tx.date > n.valid_to):
            continue
        if n.merchant:
            if n.merchant.upper() in text:
                out.append(book_notes.to_dict(n))
        elif n.valid_from or n.valid_to:
            out.append(book_notes.to_dict(n))
    return out


def insight(session: Session, company: Company, tx: Transaction) -> dict:
    """Todo lo que se sabe de UN movimiento, para el panel lateral."""
    txs = session.exec(select(Transaction).where(Transaction.company_id == company.id)).all()
    by_id = {t.id: t for t in txs}
    web = merchant_verifier.web_by_tx(session, company, txs)
    merchant = book_review.merchant_name(tx)

    open_findings = session.exec(select(Finding).where(Finding.company_id == company.id,
                                                       Finding.status == "open")).all()
    mine = [f for f in _shown(open_findings, _silencers(session, company)) if tx.id in (f.transaction_ids or [])]
    mine.sort(key=lambda f: (-LEVEL_ORDER[finding_level(f)], book_review.SEVERITY_ORDER.get(f.severity, 9)))
    findings = [{**book_review.finding_dict(f, by_id, web), "level": finding_level(f)} for f in mine]

    hit = web.get(tx.id)
    return {
        "transaction": {
            **book_review.tx_summary(tx),
            "subcategory": tx.subcategory, "note": tx.note, "description_extra": tx.description_extra,
            "matched_rule_id": tx.matched_rule_id,
            "bank_removed_at": tx.bank_removed_at.isoformat() if tx.bank_removed_at else None,
        },
        "merchant": merchant,
        "provenance": _provenance(session, tx),
        "history": _history(session, tx),
        "findings": findings,
        "web": {"business_type": hit.business_type, "confidence": hit.confidence,
                "source_url": hit.source_url, "source_domain": hit.source_domain} if hit else None,
        "notes": _notes(session, company, tx, merchant),
        "level": max((f["level"] for f in findings), key=lambda l: LEVEL_ORDER[l], default=None)
                 if not tx.bank_removed_at else "problem",
    }


def transaction_of(session: Session, company: Company, transaction_id: int) -> Optional[Transaction]:
    tx = session.get(Transaction, transaction_id)
    return tx if tx is not None and tx.company_id == company.id else None
