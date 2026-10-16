"""
ContaBu — ¿PERSONAL PARA ESTE NEGOCIO? (FASE 11, Auditoría / IA · 14-oct-2026).

Lo personal NO es una lista fija de comercios: depende del negocio. Home Depot es materiales en una
constructora y una reparación en un restaurante; una tienda de ropa barata es personal en una
constructora y útiles para los niños en una guardería. Por eso no lo decide una tabla de palabras
(como el ② verificador, que dice QUÉ es el comercio), sino Nemotron, con lo que sabe del mundo y del
negocio de ESTE libro.

Al modelo entra, por libro:
- el negocio: su sector y las notas del bookkeeper SIN periodo (las de un viaje o unas vacaciones las
  maneja el ④ con ventanas y el chequeo de la nota; aquí confundirían "lo que se compró en el viaje"
  con "lo que el negocio compra");
- las categorías que usa el libro (qué compra este negocio);
- la HUELLA de cada comercio que el libro tiene como gasto del negocio, y su tipo verificado por el ②
  si lo hay (solo el tipo, nunca el texto de internet).
Nunca descripciones completas, montos ni nombres de persona (los pagos a personas y los cheques no se
mandan). Lo enviado lo revisa `agents.privacy_scan`, igual que a los demás agentes.

Responde, por comercio, con una lista cerrada: "business" (uso normal para este negocio) · "mixed" ·
"personal" (probablemente personal), con confianza y una razón corta en inglés y en español. Se guarda
en `MerchantJudgment`, POR LIBRO, y el auditor la aplica en código (`book_review.detect` → hallazgo
`personal_use`). Nunca categoriza: es una observación para el bookkeeper. Un "está bien" sobre el
hallazgo se recuerda para ese libro (tabla `finding`).
"""

import hashlib
import json
from typing import Literal, Optional

from pydantic import BaseModel
from sqlmodel import Session, select

import agent_calls
import agent_models
import nemotron
from company_profile import p2p_counterparty
from models import Company, MerchantJudgment, Transaction
from rules import is_check, merchant_signature, suggest_keyword

PROMPT_VERSION = 3   # 2 (14-oct): renta/servicios siempre del negocio; lo que el libro ya compra no es personal
                     # 3 (14-oct): eso, por lo que el comercio vende PRINCIPALMENTE
MAX_MERCHANTS_SENT = 60
BUSINESS_SECTIONS = ("OPERATING_EXPENSE", "COGS", "OTHER_EXPENSE")
VERDICTS = ("business", "mixed", "personal")
# Lo que se vuelve hallazgo: "probablemente personal" con confianza alta o media.
ALERT_VERDICTS, ALERT_CONFIDENCE = ("personal",), ("high", "medium")


class Judgment(BaseModel):
    ref: str
    verdict: Literal["business", "mixed", "personal"]
    confidence: Literal["high", "medium", "low"]
    reason_en: str = ""
    reason_es: str = ""


class JudgmentsOut(BaseModel):
    judgments: list[Judgment]


_SYSTEM = """You help a bookkeeper spot PERSONAL spending hidden among a client's business expenses.
You get ONE client business (its sector, the bookkeeper's notes about it, and the expense categories its
book uses) and a list of MERCHANTS that the book records as business expenses. For each merchant, judge
whether buying there is a normal expense FOR THIS SPECIFIC BUSINESS.

The same merchant can be business for one client and personal for another: a home-improvement store is
job materials for a contractor but rarely for an accounting office; a discount clothing store is personal
for a contractor but can be supplies for a daycare. Judge what THIS business does, not the merchant alone.

Rules:
- Output ONLY JSON: {"judgments": [{"ref", "verdict", "confidence", "reason_en", "reason_es"}]}, exactly
  one per merchant, with its "ref".
- verdict:
  - "business": a normal place for THIS business to buy what it needs to operate.
  - "mixed": this business plausibly buys here AND people commonly buy personal things here too.
  - "personal": for THIS business, spending here is most likely the owner's personal or household
    spending (leisure, entertainment, personal shopping), not an operating cost.
- Rent paid to a landlord or property manager, utilities, insurance, banks, payroll services, taxes
  and government agencies are "business" for any business.
- Look at "expense_categories_used": if what this merchant MAINLY sells is something this business
  buys to operate (food for a restaurant, meals for its crew, the supplies of its trade), the merchant is
  "business" or "mixed", not "personal". A store that only happens to carry such things among many
  other goods does not count: judge it by what it mainly sells.
- confidence: "high", "medium" or "low". Use "low" when you do not recognize the merchant.
- reason_en / reason_es: at most 15 words, the same reason in English and in Spanish. Name the
  business type, never a person.
- You never choose a category and never see amounts. You only give an opinion for the bookkeeper.
- Placeholders like "PERSON 1" stand for private people."""


def merchant_key(t: Transaction) -> Optional[str]:
    """La llave del comercio de un movimiento (la misma huella que agrupa el ④), o None si no es un
    comercio: un pago a una persona o un cheque no se juzgan nunca."""
    if p2p_counterparty(t.description) or is_check(t.description):
        return None
    sig = merchant_signature(t.description or "")
    key = sig or suggest_keyword(t.description or "") or ""
    return key.upper() or None


def is_business_expense(t: Transaction) -> bool:
    """Una salida de dinero que el libro tiene como gasto DEL NEGOCIO (no Pendiente, no NON_PL)."""
    return (not t.is_inflow and t.account_type is not None and t.account_type.value in BUSINESS_SECTIONS
            and bool(t.category))


def _business_notes(view) -> list[str]:
    return [n["text"] for n in view.notes if not n.get("valid_from") and not n.get("valid_to") and n.get("text")]


def context_hash(company: Company, view) -> str:
    """El negocio tal como se le describe al modelo. Si cambia (otro sector, una nota nueva), las
    opiniones guardadas quedan viejas y se rehacen."""
    blob = json.dumps({"v": PROMPT_VERSION, "sector": company.sector or "", "notes": _business_notes(view)},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def current(session: Session, company: Company, ctx_hash: Optional[str] = None) -> dict[str, MerchantJudgment]:
    """Las opiniones guardadas de este libro (las de su contexto actual, si se da `ctx_hash`)."""
    rows = session.exec(select(MerchantJudgment).where(MerchantJudgment.company_id == company.id)
                        .order_by(MerchantJudgment.id)).all()
    return {r.merchant_key: r for r in rows if ctx_hash is None or r.context_hash == ctx_hash}


def candidates(txs: list[Transaction]) -> dict[str, list[Transaction]]:
    """Comercios con al menos una salida registrada como gasto del negocio: los únicos que pueden ser
    "personal camuflado"."""
    out: dict[str, list[Transaction]] = {}
    for t in txs:
        if not is_business_expense(t):
            continue
        k = merchant_key(t)
        if k:
            out.setdefault(k, []).append(t)
    return out


def _user(company: Company, view, todo: list[tuple[str, list[Transaction]]]) -> tuple[str, dict[str, tuple]]:
    """Lo que ve el modelo, y ref → (llave, frase). Las frases pasan por el mismo acortador y los mismos
    marcadores de persona que las del ④ y el ①."""
    import agents
    full = {(t.description or "").strip().upper() for t in view.ctx.txs if t.description}
    categories = sorted({f"{t.account_type.value}/{t.category}" for t in view.ctx.txs if is_business_expense(t)})
    merchants, refs = [], {}
    for i, (key, group) in enumerate(todo, 1):
        _k, phrase = agents._huella(group[0], view.names)
        phrase = agents._shorten_if_full(phrase, full)
        ref = f"M{i}"
        refs[ref] = (key, phrase)
        item = {"ref": ref, "merchant": phrase}
        hit = next((view.web[t.id] for t in group if t.id in view.web), None)
        if hit is not None and hit.confidence in ("high", "medium"):
            item["verified_business_type"] = hit.business_type
        merchants.append(item)
    business = {"sector": company.sector or "unknown",
                "bookkeeper_notes": [view.names.mask(x) for x in _business_notes(view)],
                "expense_categories_used": categories}
    user = (f"business: {json.dumps(business, ensure_ascii=False)}\n\n"
            f"merchants: {json.dumps(merchants, ensure_ascii=False)}")
    return user, refs


def refresh(session: Session, company: Company, view, *, model: Optional[str] = None,
            user_id: Optional[int] = None, reasoning: Optional[bool] = None) -> Optional[nemotron.Usage]:
    """Juzga los comercios que todavía no tienen opinión en el contexto actual del libro, en UNA llamada.
    Sin nada nuevo, no llama. Si se acabó el tope del día o la llamada falla, no guarda nada (se
    reintenta en la próxima revisión). No hace commit (lo hace quien llama)."""
    h = context_hash(company, view)
    have = current(session, company, h)
    todo = [(k, g) for k, g in sorted(candidates(view.ctx.txs).items(), key=lambda kv: -len(kv[1]))
            if k not in have][:MAX_MERCHANTS_SENT]
    if not todo:
        return None
    try:
        agent_calls.check_cap(session, company)
    except agent_calls.CapReached:
        return None
    user, refs = _user(company, view, todo)
    model, reasoning = agent_models.resolve("personal", model, reasoning)
    try:
        reply = nemotron.chat_json(_SYSTEM, user, JudgmentsOut, model=model, max_tokens=4000, reasoning=reasoning)
    except nemotron.NemotronError as e:
        agent_calls.record(session, company, "personal_judge", e.usage, outcome=e.code, user_id=user_id,
                           model=model or nemotron.model_name())
        return e.usage
    valid = 0
    seen = set()
    for raw in (reply.data.get("judgments") if isinstance(reply.data, dict) else []) or []:
        try:
            j = Judgment.model_validate(raw)
        except Exception:
            continue
        if j.ref not in refs or j.ref in seen:
            continue
        seen.add(j.ref)
        key, phrase = refs[j.ref]
        old = session.exec(select(MerchantJudgment).where(MerchantJudgment.company_id == company.id,
                                                         MerchantJudgment.merchant_key == key)).all()
        for o in old:
            session.delete(o)
        session.add(MerchantJudgment(
            company_id=company.id, merchant_key=key, keyword=phrase, verdict=j.verdict, confidence=j.confidence,
            reason_en=(view.names.unmask(j.reason_en) or "")[:200] or None,
            reason_es=(view.names.unmask(j.reason_es) or "")[:200] or None,
            context_hash=h, model=reply.usage.model if reply.usage else model))
        valid += 1
    agent_calls.record(session, company, "personal_judge", reply.usage, user_id=user_id,
                       items_out=len(todo), items_valid=valid)
    session.flush()
    return reply.usage


def forget(session: Session, company: Company, txs: list[Transaction]) -> int:
    """Olvida la opinión sobre los comercios de esos movimientos (llegó evidencia NUEVA del ②): la
    siguiente revisión los vuelve a juzgar, ahora con el tipo verificado. No hace commit."""
    keys = {k for t in txs if (k := merchant_key(t))}
    if not keys:
        return 0
    rows = session.exec(select(MerchantJudgment).where(MerchantJudgment.company_id == company.id,
                                                       MerchantJudgment.merchant_key.in_(keys))).all()
    for r in rows:
        session.delete(r)
    session.flush()
    return len(rows)


def findings(session: Session, company: Company, txs: list[Transaction], skip_ids: set[int]) -> list[dict]:
    """El hallazgo `personal_use`: comercios que el modelo juzgó "probablemente personal" (confianza alta
    o media) y que el libro tiene como gasto del negocio. Se aplica en código, gratis, en cada revisión.
    `skip_ids`: movimientos que ya marcó el ② (internet dice "uso personal"): no se repite el aviso."""
    saved = current(session, company)
    out = []
    for key, group in candidates(txs).items():
        j = saved.get(key)
        if j is None or j.verdict not in ALERT_VERDICTS or j.confidence not in ALERT_CONFIDENCE:
            continue
        group = [t for t in group if t.id not in skip_ids]
        if not group:
            continue
        from book_review import merchant_name
        out.append({"key": f"personal_use:{key}", "type": "personal_use", "severity": "medium",
                    "merchant": merchant_name(group[0]), "transaction_ids": [t.id for t in group][:50],
                    "payload": {"verdict": j.verdict, "confidence": j.confidence, "sector": company.sector,
                                "reason_en": j.reason_en, "reason_es": j.reason_es, "rows": len(group),
                                "categories": sorted({f"{t.account_type.value}/{t.category}" for t in group})}})
    return out
