"""
ContaBu — LA REVISIÓN DEL LIBRO: los hallazgos guardados y la pantalla de Auditoría (FASE 11, Auditoría / IA
· 8-oct-2026).

Una sola lista de hallazgos por libro. Cada uno es UNA fila `Finding`, que junta:
- lo que detectó el auditor (determinista, gratis);
- el veredicto del ① revisor (Nemotron);
- la evidencia del ② verificador (Tavily).

Lo que el bookkeeper marcó "está bien" (`status="ok"`) no vuelve a salir; lo que el auditor deja de ver
pasa a `gone`.

Lo que el auditor sabe de contabilidad (sin IA; observa, nunca categoriza):
- **Nómina neta** (`payroll_net_only`): Gusto, ADP, PAYROLL SERVICE, depósitos directos, remesas
  IRS/EFTPS. El banco solo ve la nómina NETA que pagó; los salarios brutos y los impuestos del empleador
  salen del resumen de payroll.
- **Monto inusual** (`unusual_amount`, 13-oct): un cargo de un comercio que es 3 veces o más lo normal
  de ESE comercio (la mediana de sus otros cargos, con 3+ de historia) y más que cualquiera anterior.
  No es un error: es una pregunta para el cliente. El ① no lo juzga (nunca ve montos).
- **Lo que dice una nota** (`note_check`, 13-oct): la nota 📌 traducida por Nemotron a un chequeo
  (`note_checks.py`) se aplica AQUÍ, en código: comercios o tipos, monto mínimo escrito en la nota y la
  ventana de la nota. Lo pidió el bookkeeper: el ① no lo vuelve a juzgar.
- **¿Personal para este negocio?** (`personal_use`, 14-oct): la opinión de Nemotron sobre cada comercio
  EN ESTE LIBRO (`personal_use.py`, guardada por libro) se aplica aquí, en código. Ya es una opinión del
  modelo: el ① no la vuelve a juzgar.
- **Datos que faltan no son pérdidas** (`coverage_gap`, `bank_disconnected`): un mes sin movimientos en
  una cuenta, o un banco desconectado, es "faltan datos", nunca "el negocio perdió dinero".

Los textos para la pantalla NO se arman aquí: el hallazgo lleva `type` + `payload` (en inglés,
estructurado) y la pantalla traduce (EN/ES). La observación del ① sí es texto, en el idioma del
bookkeeper.
"""

import re
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlmodel import Session, func, select

import book_notes
import merchant_verifier
import note_checks
import personal_use
from audit import audit_categorization
from company_profile import p2p_counterparty
from models import (AgentCall, BankConnection, CategoryRule, Company, Finding, ProposalDismissal,
                    Transaction)
from rules import is_check, load_rules, matchable_text, merchant_signature, rule_overlaps, suggest_keyword

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}
# Tipos que son una OBSERVACIÓN del auditor, no un posible error: el ① no los juzga.
INFO_TYPES = {"payroll_net_only", "coverage_gap", "bank_disconnected", "unusual_amount"}
# Lo que el ① no juzga: las observaciones y lo que el bookkeeper pidió en una nota.
UNJUDGED_TYPES = INFO_TYPES | {"note_check", "personal_use"}


def ai_cannot_dismiss(f) -> bool:
    """"Internet dice uso personal" con confianza ALTA: el ① lo explica pero NO lo puede despachar; sigue
    siendo alerta (decisión de Jose, 14-oct, igual que el ⑤ y los chequeos de las notas). ✅ Medido el
    14-oct: una vez de 7 el ① llamó "legítimo" a Netflix (escrito a mano como suscripción)."""
    return f.type == "merchant_type_mismatch" and (f.payload or {}).get("confidence") == "high"
# Monto inusual: N veces la mediana del comercio, por encima de todo lo anterior, con historia mínima.
UNUSUAL_TIMES, UNUSUAL_MIN_HISTORY, UNUSUAL_MIN_AMOUNT = 3, 3, Decimal("100")

_PAYROLL_RE = re.compile(r"\b(GUSTO|ADP|PAYCHEX|PAYROLL|ONLINE PAYROLL|DIR(?:ECT)? ?DEP(?:OSIT)?|INTUIT PAYROLL|"
                         r"QUICKBOOKS PAYROLL|PAYLOCITY|RIPPLING|TRINET|JUSTWORKS)\b")
_PAYROLL_TAX_RE = re.compile(r"\b(IRS|EFTPS|USATAXPYMT|USA TAX PYMT|TAX PAYMT|TAXPAYMENT|EDD|IRS TREAS)\b")


_ES_WORDS = {"el", "la", "los", "las", "de", "del", "que", "es", "está", "pero", "como", "una", "un", "por", "para",
             "con", "debería", "parece", "aparece", "se", "su", "sus", "y", "o", "no"}
_EN_WORDS = {"the", "is", "are", "but", "as", "it", "should", "be", "this", "of", "and", "to", "for", "with",
             "appears", "seems", "looks", "an", "a", "not", "or"}


def text_lang(text: Optional[str]) -> Optional[str]:
    """¿En qué idioma está una observación? Cuenta palabras frecuentes de cada idioma (sin IA). None si
    no alcanza para decidir. Sirve para no mostrar un veredicto en inglés con la app en español (13-oct)."""
    words = re.findall(r"[a-záéíóúñü]+", (text or "").lower())
    es, en = sum(w in _ES_WORDS for w in words), sum(w in _EN_WORDS for w in words)
    if es == en or max(es, en) < 2:
        return None
    return "es" if es > en else "en"


def observation_stale(f: Finding, lang: Optional[str]) -> bool:
    """Un veredicto guardado en OTRO idioma que el de la pantalla está viejo: no se muestra su texto y el
    próximo "Revisar el libro" lo rehace en el idioma actual."""
    return bool(f.agent_observation and lang and f.agent_lang and f.agent_lang != lang)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def merchant_name(t: Transaction) -> str:
    """El nombre limpio del comercio para la PANTALLA: la misma huella que usan los agentes, sin
    número de tienda ni referencias (la del bookkeeper: aquí sí va el nombre de la persona)."""
    person = p2p_counterparty(t.description)
    if person:
        # La dirección según el banco: entró = "PAYMENT FROM", salió = "PAYMENT TO" (13-oct).
        return f"PAYMENT {'FROM' if t.is_inflow else 'TO'} {person}"
    if is_check(t.description):
        return "CHECK"
    return suggest_keyword(t.description) or merchant_signature(t.description) or (t.description or "")[:40]


def tx_summary(t: Transaction) -> dict:
    """Un movimiento como lo ve el bookkeeper: comercio limpio + fecha + monto + cuenta; la línea cruda
    del banco va aparte (la pantalla la deja expandible)."""
    return {"id": t.id, "merchant": merchant_name(t), "date": t.date.isoformat(), "amount": float(t.amount),
            "is_inflow": bool(t.is_inflow), "account": t.bank_account, "description": t.description,
            "category": t.category, "account_type": t.account_type.value if t.account_type else None,
            "source": t.source}


# ── 1. Detectar (determinista) ──────────────────────────────────────────────────────────────────

def detect(session: Session, company: Company, txs: list[Transaction]) -> list[dict]:
    """Todo lo que el auditor ve HOY en el libro. Cada uno con una `key` estable."""
    by_id = {t.id: t for t in txs}
    rules = session.exec(select(CategoryRule).where(CategoryRule.company_id == company.id)).all()
    rule_of = {r.id: r for r in rules}
    items = [{"id": t.id, "description": t.description, "account_type": t.account_type.value if t.account_type else None,
              "category": t.category, "is_inflow": t.is_inflow, "matched_rule_id": t.matched_rule_id,
              "source": t.source} for t in txs]
    rules_by_id = {r.id: {"match_text": r.match_text, "account_type": r.account_type.value if r.account_type else None,
                          "category": r.category} for r in rules}
    out: list[dict] = []

    for f in audit_categorization(items, rules_by_id):
        ids = f["transaction_ids"]
        sample = by_id.get(ids[0]) if ids else None
        if f["type"] == "uncategorized" or sample is None:
            continue  # lo Pendiente va agrupado por comercio, aparte (`pending_groups`)
        merchant = merchant_name(sample)
        if f["type"] == "rule_deviation":
            rule = rule_of.get(sample.matched_rule_id)
            out.append({"key": f"rule_deviation:{sample.id}", "type": "rule_deviation", "severity": "high",
                        "merchant": merchant, "transaction_ids": ids, "payload": {
                            "rule": rule.match_text if rule else None,
                            "rule_says": f"{f.get('suggested_account_type')}/{f.get('suggested_category')}",
                            "row_has": f"{sample.account_type.value if sample.account_type else None}/{sample.category}",
                            "suggested_account_type": f.get("suggested_account_type"),
                            "suggested_category": f.get("suggested_category")}})
        elif f["type"] == "inconsistent_merchant":
            out.append({"key": f"inconsistent:{f.get('merchant')}", "type": "inconsistent_merchant",
                        "severity": "high" if not f.get("manual_ids") else "medium",
                        "merchant": merchant, "transaction_ids": ids, "payload": {
                            "categories": sorted(f["detail"]), "odd_ids": f.get("odd_ids", []),
                            "manual_ids": f.get("manual_ids", []),
                            "suggested_account_type": f.get("suggested_account_type"),
                            "suggested_category": f.get("suggested_category")}})
        elif f["type"] == "sign_mismatch":
            out.append({"key": f"sign:{sample.id}", "type": "sign_mismatch", "severity": "high",
                        "merchant": merchant, "transaction_ids": ids,
                        "payload": {"account_type": f.get("account_type"), "direction": f.get("direction")}})

    # Reglas que CHOCAN (cazan los mismos movimientos con categorías distintas). Una regla con
    # condiciones (ventana de fechas o monto) que le gana a la general es una excepción, no un choque.
    live = [t for t in txs if t.source != "manual"]
    ordered = load_rules(session, company.id, company.sector or "")
    for rid, ov in rule_overlaps(ordered, live).items():
        w = rule_of.get(rid)
        if w is None or w.valid_from or w.valid_to or getattr(w, "amount_min", None) is not None \
                or getattr(w, "amount_max", None) is not None:
            continue
        for lid, n in ov["beats"].items():
            l = rule_of.get(lid)
            if l and (w.account_type, w.category) != (l.account_type, l.category):
                hit = [t.id for t in live if t.matched_rule_id == rid][:50]
                out.append({"key": f"collision:{min(rid, lid)}:{max(rid, lid)}", "type": "rule_collision",
                            "severity": "medium", "merchant": l.match_text, "transaction_ids": hit, "payload": {
                                "winning_rule": w.match_text, "winning_then": f"{w.account_type.value}/{w.category}",
                                "losing_rule": l.match_text, "losing_then": f"{l.account_type.value}/{l.category}",
                                "rows": n}})

    # El ② dice "uso personal" (un gimnasio) y el libro lo tiene como gasto del negocio.
    web = merchant_verifier.web_by_tx(session, company, txs)
    personal: dict[int, list[Transaction]] = defaultdict(list)
    for t in txs:
        hit = web.get(t.id)
        if (hit and hit.business_type in merchant_verifier.PERSONAL_TYPES and hit.confidence in ("high", "medium")
                and t.account_type is not None and t.account_type.value != "NON_PL"):
            personal[hit.id].append(t)
    for hid, group in personal.items():
        hit = web[group[0].id]
        out.append({"key": f"personal:{hit.fingerprint}", "type": "merchant_type_mismatch", "severity": "high",
                    "merchant": merchant_name(group[0]), "transaction_ids": [t.id for t in group], "payload": {
                        "business_type": hit.business_type, "confidence": hit.confidence,
                        "categories": sorted({f"{t.account_type.value}/{t.category}" for t in group})}})
    # ¿Personal PARA ESTE NEGOCIO? (opinión de Nemotron por libro); lo que ya marcó el ② no se repite.
    out.extend(personal_use.findings(session, company, txs, {t.id for g in personal.values() for t in g}))

    # Nómina: el banco solo ve lo NETO.
    payroll: dict[tuple, list[Transaction]] = defaultdict(list)
    for t in txs:
        if t.is_inflow:
            continue
        up = (t.description or "").upper()
        kind = "tax" if _PAYROLL_TAX_RE.search(up) else ("payroll" if _PAYROLL_RE.search(up) else None)
        if kind:
            payroll[(kind, merchant_name(t))].append(t)
    for (kind, merchant), group in payroll.items():
        out.append({"key": f"payroll:{kind}:{merchant}", "type": "payroll_net_only", "severity": "info",
                    "merchant": merchant, "transaction_ids": [t.id for t in group][:50],
                    "payload": {"kind": kind, "rows": len(group)}})

    out.extend(_unusual_amounts(txs))
    out.extend(_note_checks(session, company, txs, web))
    # Datos que faltan: un mes sin movimientos en una cuenta, dentro del periodo del libro.
    out.extend(_coverage(txs))
    for conn in session.exec(select(BankConnection).where(BankConnection.company_id == company.id)).all():
        if conn.status != "active" or conn.last_error:
            out.append({"key": f"bank:{conn.id}", "type": "bank_disconnected", "severity": "medium",
                        "merchant": conn.institution_name or "Bank", "transaction_ids": [], "payload": {
                            "institution": conn.institution_name, "error": conn.last_error,
                            "last_synced": conn.last_synced_at.isoformat() if conn.last_synced_at else None}})
    return out


def _note_checks(session: Session, company: Company, txs: list[Transaction], web: dict) -> list[dict]:
    """Los chequeos de las notas 📌 (vigentes y que valen), aplicados en código. Uno por nota y comercio."""
    out = []
    for n in book_notes.active_notes(session, company.id):
        c = n.audit_check or {}
        if n.kind != "keep_in_mind" or not c.get("applies") or not note_checks.is_current(n):
            continue
        kws, types, over = c.get("merchants") or [], set(c.get("business_types") or []), c.get("amount_over")
        groups: dict[str, list[Transaction]] = defaultdict(list)
        for t in txs:
            if (c.get("direction") == "out" and t.is_inflow) or (c.get("direction") == "in" and not t.is_inflow):
                continue
            if (n.valid_from and t.date < n.valid_from) or (n.valid_to and t.date > n.valid_to):
                continue
            if over is not None and abs(Decimal(t.amount)) <= Decimal(str(over)):
                continue
            # "Es personal": solo lo que está como gasto o ingreso DEL NEGOCIO (lo Pendiente va aparte).
            if c.get("meaning") == "personal" and (t.account_type is None or t.account_type.value == "NON_PL"):
                continue
            hit = web.get(t.id)
            by_type = bool(hit and hit.business_type in types and hit.confidence in ("high", "medium"))
            text = matchable_text(t.description, t.description_extra).upper()
            if by_type or any(k in text for k in kws):
                groups[merchant_name(t)].append(t)
        for merchant, group in groups.items():
            out.append({"key": f"note:{n.id}:{merchant}", "type": "note_check", "severity": "medium",
                        "merchant": merchant, "transaction_ids": [t.id for t in group][:50], "payload": {
                            "note_id": n.id, "note": n.text[:200], "meaning": c.get("meaning"),
                            "amount_over": over, "rows": len(group),
                            "categories": sorted({f"{t.account_type.value}/{t.category}" for t in group
                                                  if t.account_type})}})
    return out


def _unusual_amounts(txs: list[Transaction]) -> list[dict]:
    """Cargos (salidas) muy por encima de lo normal de SU comercio. Aritmética de código sobre los montos
    del banco; ningún monto sale de aquí hacia un modelo."""
    by_merchant: dict[str, list[Transaction]] = defaultdict(list)
    for t in txs:
        sig = merchant_signature(t.description or "") if not t.is_inflow else ""
        if sig and t.amount is not None:
            by_merchant[sig].append(t)
    out = []
    for sig, group in by_merchant.items():
        if len(group) < UNUSUAL_MIN_HISTORY + 1:
            continue
        for t in group:
            others = sorted(abs(Decimal(o.amount)) for o in group if o.id != t.id)
            amount = abs(Decimal(t.amount))
            mid = len(others) // 2
            usual = others[mid] if len(others) % 2 else (others[mid - 1] + others[mid]) / 2
            if amount >= UNUSUAL_MIN_AMOUNT and usual > 0 and amount >= UNUSUAL_TIMES * usual and amount > others[-1]:
                out.append({"key": f"unusual:{t.id}", "type": "unusual_amount", "severity": "info",
                            "merchant": merchant_name(t), "transaction_ids": [t.id], "payload": {
                                "amount": float(amount), "usual": float(round(usual, 2)),
                                "times": float(round(amount / usual, 1)), "history": len(others)}})
    return out


def _months(lo: date, hi: date) -> list[str]:
    out, y, m = [], lo.year, lo.month
    while (y, m) <= (hi.year, hi.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _coverage(txs: list[Transaction]) -> list[dict]:
    """Meses del periodo del libro en los que una cuenta no tiene NINGÚN movimiento. Es "faltan datos",
    nunca "pérdida". Solo con 2+ meses de historia (con uno solo no hay periodo que cubrir)."""
    dated = [t for t in txs if t.date]
    if not dated:
        return []
    period = _months(min(t.date for t in dated), max(t.date for t in dated))
    if len(period) < 2:
        return []
    by_acct: dict[str, set] = defaultdict(set)
    for t in dated:
        by_acct[t.bank_account or "—"].add(t.date.strftime("%Y-%m"))
    out = []
    for acct, months in by_acct.items():
        for m in period:
            if m not in months:
                out.append({"key": f"gap:{acct}:{m}", "type": "coverage_gap", "severity": "medium",
                            "merchant": acct, "transaction_ids": [], "payload": {"account": acct, "month": m}})
    return out


# ── 2. Guardar (sync) y leer ────────────────────────────────────────────────────────────────────

def sync(session: Session, company: Company) -> list[Finding]:
    """Pone la tabla `finding` al día con lo que el auditor ve hoy. Lo nuevo entra `open`; lo que ya
    estaba conserva su estado (un "está bien" sigue siendo "está bien"); lo que el auditor ya no ve pasa
    a `gone`. Devuelve los ABIERTOS. No hace commit."""
    txs = session.exec(select(Transaction).where(Transaction.company_id == company.id)).all()
    current = {d["key"]: d for d in detect(session, company, txs)}
    existing = {f.key: f for f in session.exec(select(Finding).where(Finding.company_id == company.id)).all()}
    now = _now()
    for key, d in current.items():
        f = existing.get(key)
        if f is None:
            f = Finding(company_id=company.id, key=key, type=d["type"], severity=d["severity"],
                        merchant=d["merchant"], transaction_ids=d["transaction_ids"], payload=d["payload"])
            existing[key] = f
        else:
            if f.status == "gone":
                f.status = "open"            # volvió a aparecer
            if f.transaction_ids != d["transaction_ids"] or f.payload != d["payload"]:
                if f.transaction_ids != d["transaction_ids"]:
                    f.agent_verdict = f.agent_observation = None   # cambió lo que el ① juzgó
                f.transaction_ids, f.payload = d["transaction_ids"], d["payload"]
            f.severity, f.merchant = d["severity"], d["merchant"]
        f.last_seen_at = now
        session.add(f)
    for key, f in existing.items():
        if key not in current and f.status == "open":
            f.status = "gone"
            session.add(f)
    session.flush()
    return sorted([f for f in existing.values() if f.status == "open"],
                  key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.type, f.merchant or ""))


def pending_groups(txs: list[Transaction], web: dict) -> list[dict]:
    """Lo Pendiente, AGRUPADO por comercio ("REMITLY ×2"), del más frecuente al menos."""
    groups: dict[str, list[Transaction]] = defaultdict(list)
    for t in txs:
        if not t.category or t.account_type is None:
            groups[merchant_name(t)].append(t)
    out = []
    for merchant, group in groups.items():
        hit = next((web[t.id] for t in group if t.id in web), None)
        out.append({
            "merchant": merchant, "count": len(group),
            "total": float(sum((abs(t.amount) for t in group), Decimal(0))),
            "direction": "in" if sum(1 for t in group if t.is_inflow) > len(group) / 2 else "out",
            "first": min(t.date for t in group).isoformat(), "last": max(t.date for t in group).isoformat(),
            "accounts": sorted({t.bank_account for t in group if t.bank_account}),
            "person_payment": any(p2p_counterparty(t.description) for t in group),
            "web": {"business_type": hit.business_type, "confidence": hit.confidence,
                    "source_url": hit.source_url, "source_domain": hit.source_domain} if hit else None,
            "transactions": [tx_summary(t) for t in sorted(group, key=lambda t: t.date)][:50],
        })
    out.sort(key=lambda g: (-g["count"], -g["total"]))
    return out


def finding_dict(f: Finding, by_id: dict, web: dict, lang: Optional[str] = None) -> dict:
    hit = next((web[i] for i in f.transaction_ids if i in web), None)
    stale = observation_stale(f, lang)
    return {
        "observation_stale": stale,
        "id": f.id, "key": f.key, "type": f.type, "severity": f.severity, "merchant": f.merchant,
        "payload": f.payload or {}, "status": f.status,
        "agent_verdict": f.agent_verdict, "agent_observation": None if stale else f.agent_observation,
        "agent_lang": f.agent_lang,
        "agent_reviewed_at": f.agent_reviewed_at.isoformat() if f.agent_reviewed_at else None,
        "web": {"business_type": hit.business_type, "confidence": hit.confidence,
                "source_url": hit.source_url, "source_domain": hit.source_domain} if hit else None,
        "transactions": [tx_summary(by_id[i]) for i in f.transaction_ids if i in by_id][:30],
        "rows": len(f.transaction_ids),
        "is_info": f.type in INFO_TYPES,
    }


def last_review_at(session: Session, company: Company) -> Optional[datetime]:
    return session.exec(select(func.max(AgentCall.created_at)).where(
        AgentCall.company_id == company.id, AgentCall.agent == "reviewer")).one()


def seconds_since_review(session: Session, company: Company) -> Optional[int]:
    """Hace cuánto fue la última revisión, calculado EN la base con su mismo reloj: la columna guarda la
    hora en la zona de la sesión (✅ 8-oct: la pantalla decía "hace 7 horas" justo después de revisar,
    al tratarla como UTC)."""
    from sqlalchemy import text
    v = session.connection().execute(text(
        "SELECT EXTRACT(EPOCH FROM (LOCALTIMESTAMP - MAX(created_at))) FROM agentcall "
        "WHERE company_id = :c AND agent = 'reviewer'"), {"c": company.id}).scalar()
    return int(v) if v is not None else None


def board(session: Session, company: Company, lang: Optional[str] = None) -> dict:
    """Todo lo que muestra la pestaña Auditoría, en una llamada: el encabezado de estado, la lista
    única de hallazgos y lo Pendiente agrupado."""
    open_findings = sync(session, company)
    session.commit()
    txs = session.exec(select(Transaction).where(Transaction.company_id == company.id)).all()
    by_id = {t.id: t for t in txs}
    web = merchant_verifier.web_by_tx(session, company, txs)
    groups = pending_groups(txs, web)
    # Una nota 🚫 "no me reportes" con comercio esconde sus hallazgos (en código, no en un prompt).
    silencers = [n.merchant.upper() for n in book_notes.list_notes(session, company.id)
                 if n.kind == "dont_report" and n.merchant]
    shown = [f for f in open_findings if not any(x in (f.merchant or "").upper() for x in silencers)]
    findings = [finding_dict(f, by_id, web, lang) for f in shown]
    reviewed = last_review_at(session, company)
    return {
        "stats": {
            "to_review": sum(1 for f in findings if not f["is_info"]),
            "info": sum(1 for f in findings if f["is_info"]),
            "problems": sum(1 for f in findings if f["agent_verdict"] == "problem"
                            or (f["agent_verdict"] is None and f["severity"] == "high" and not f["is_info"])),
            "pending": sum(g["count"] for g in groups), "pending_merchants": len(groups),
            "reviewed_at": reviewed.isoformat() if reviewed else None,
            "reviewed_seconds_ago": seconds_since_review(session, company),
            "silenced": len(open_findings) - len(shown),
        },
        "findings": findings,
        "pending_groups": groups,
    }


def summary(session: Session, company: Company) -> dict:
    """Solo el encabezado (para el contador de la barra lateral)."""
    return board(session, company)["stats"]


def resolve(session: Session, company: Company, finding_id: int, status: str, *,
            user_id: Optional[int], note: Optional[str] = None) -> Finding:
    """El bookkeeper decide sobre un hallazgo: "ok" = está bien (no vuelve a salir) · "open" = lo reabre."""
    f = session.get(Finding, finding_id)
    if f is None or f.company_id != company.id:
        raise LookupError("finding not found")
    if status not in ("ok", "open"):
        raise ValueError("bad_status")
    f.status = status
    f.resolved_by_user_id, f.resolved_at = (user_id, _now()) if status == "ok" else (None, None)
    f.resolution_note = (note or "").strip()[:300] or None
    session.add(f)
    session.commit()
    return f


def open_by_transaction(session: Session, company: Company) -> dict[int, Finding]:
    """Para la columna Observación de la Hoja (Movimientos): el hallazgo ABIERTO más grave de cada
    movimiento. Respeta "está bien" y trae el veredicto del ①."""
    rows = session.exec(select(Finding).where(Finding.company_id == company.id, Finding.status == "open")).all()
    out: dict[int, Finding] = {}
    for f in sorted(rows, key=lambda f: SEVERITY_ORDER.get(f.severity, 9), reverse=True):
        for i in f.transaction_ids or []:
            out[i] = f
    return out


# ── 3. Los descartes del ④ ──────────────────────────────────────────────────────────────────────

DISMISS_REASONS = ("wrong_category", "too_broad", "one_off", "not_now", "other")


def dismiss(session: Session, company: Company, fields: dict, *, user_id: Optional[int]) -> ProposalDismissal:
    code = fields.get("reason_code") or "other"
    if code not in DISMISS_REASONS:
        raise ValueError("bad_reason")
    kw = (fields.get("keyword") or "").strip().upper()
    if not kw:
        raise ValueError("empty_keyword")
    d = ProposalDismissal(company_id=company.id, keyword=kw, valid_from=fields.get("valid_from"),
                          valid_to=fields.get("valid_to"), account_type=fields.get("account_type"),
                          category=fields.get("category"), reason_code=code,
                          reason_text=(fields.get("reason_text") or "").strip()[:300] or None, user_id=user_id)
    session.add(d)
    session.commit()
    return d


def dismissals(session: Session, company: Company) -> list[ProposalDismissal]:
    return session.exec(select(ProposalDismissal).where(ProposalDismissal.company_id == company.id)
                        .order_by(ProposalDismissal.id.desc())).all()
