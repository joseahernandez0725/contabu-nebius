"""
ContaBu — el PERFIL de la empresa: la entrevista del bookkeeper (FASE 9 Paso 5).

Los hechos que solo el dueño sabe (¿subcontratistas?, ¿cómo le pagan?, ¿mezcla gastos personales?)
son la palanca de la confusión COGS→OpEx. Se guardan SIEMPRE en inglés (Regla de Oro) en
`Company.profile` (JSON) y se inyectan a los prompts como **HECHOS del dueño** — contexto permitido
por la idea #5, no heurísticas prescriptivas ("tal cosa es X").

Dos rondas:
- **Ronda 1** (al crear la empresa, en `Onboarding.tsx`): máximo 5 preguntas ESTRUCTURADAS y
  saltables; las opciones ya son valores en inglés, así que no hay nada que traducir.
- **Ronda 2** (tras la primera subida): preguntas DETERMINISTAS ancladas en los datos — los pagos
  persona-a-persona (Zelle/Venmo/…) que el proponedor tiene PROHIBIDO convertir en regla sin
  contexto. El dueño confirma el rol de cada persona y (a) las transacciones se corrigen al
  instante y (b) el hecho queda en el perfil, con lo que el proponedor YA puede proponer la regla.
"""

import re
from typing import Optional

from sqlmodel import Session, select

from categories import categories_for
from models import AccountType, Transaction

# Claves de la RONDA 1 y sus valores permitidos (el frontend manda estos valores en inglés).
ROUND1_FIELDS: dict[str, list[str]] = {
    "uses_subcontractors": ["yes", "no"],
    "client_payment_methods": ["checks", "zelle", "cash", "card", "bank_transfer"],  # multi
    "payroll": ["none", "w2", "1099", "both"],
    "work_vehicles": ["yes", "no"],
    "personal_expenses_mixed": ["yes", "sometimes", "no"],
}

# Roles que el dueño puede asignar a una persona en la ronda 2.
PERSON_ROLES = ("subcontractor", "customer", "owner")

# Canales persona-a-persona: el mismo canal puede ser cliente, subcontratista o retiro del dueño —
# por eso el proponedor los salta y por eso son LA pregunta de la ronda 2.
_P2P_RE = re.compile(r"\b(ZELLE|VENMO|CASH\s?APP|CASHAPP|QUICKPAY|APPLE\s?CASH)\b")
# El nombre viene después de TO/FROM; se corta en la basura dinámica (refs, IDs, fechas).
_NAME_AFTER_RE = re.compile(r"\b(?:TO|FROM)[:\s]+([A-Z][A-Z .'-]*[A-Z])")
_NAME_STOPWORDS = {"ON", "REF", "CONF", "ID", "MEMO", "PPD", "WEB", "PAYMENT", "TRANSFER", "JPM"}


def sanitize_round1(payload: dict) -> dict:
    """Filtra la ronda 1 a claves/valores permitidos (valores SIEMPRE en inglés)."""
    out: dict = {}
    for key, allowed in ROUND1_FIELDS.items():
        if key not in payload:
            continue
        value = payload[key]
        if key == "client_payment_methods":  # multi-selección
            if isinstance(value, list):
                picked = [v for v in value if v in allowed]
                if picked:
                    out[key] = picked
        elif value in allowed:
            out[key] = value
    return out


def p2p_counterparty(description: Optional[str]) -> Optional[str]:
    """Si la transacción es un pago persona-a-persona, devuelve el NOMBRE de la contraparte
    (mayúsculas, sin basura dinámica); si no lo es o no hay nombre claro, None."""
    if not description:
        return None
    text = description.upper()
    if not _P2P_RE.search(text):
        return None
    m = _NAME_AFTER_RE.search(text)
    if not m:
        return None
    words = []
    for w in re.sub(r"[^A-Z']+", " ", m.group(1)).split():
        if w in _NAME_STOPWORDS or len(w) < 2:
            break
        words.append(w)
        if len(words) == 4:  # nombres reales: 2-4 palabras; más es basura
            break
    name = " ".join(words)
    return name if len(name) >= 4 else None


def interview_questions(txs: list[Transaction], profile: Optional[dict]) -> list[dict]:
    """RONDA 2 (determinista, sin IA): agrupa los pagos persona-a-persona por contraparte y
    devuelve una pregunta por persona aún sin rol confirmado. Ancladas en los datos: nombre,
    cuántos movimientos y cuánto entró/salió (los montos los suma Python, no la IA)."""
    known = {k.upper() for k in (profile or {}).get("people", {})}
    groups: dict[str, dict] = {}
    for t in txs:
        name = p2p_counterparty(t.description)
        if not name or name in known:
            continue
        g = groups.setdefault(name, {"name": name.title(), "key": name,
                                     "count": 0, "total_in": 0.0, "total_out": 0.0})
        g["count"] += 1
        if t.is_inflow:
            g["total_in"] += float(t.amount)
        else:
            g["total_out"] += float(t.amount)
    out = [g for g in groups.values() if g["count"] >= 2]  # 1 pago suelto no amerita entrevista
    out.sort(key=lambda g: -(g["total_in"] + g["total_out"]))
    return out


def apply_person_answer(
    session: Session, company_id: int, sector: str, name: str, role: str,
) -> int:
    """Aplica la respuesta del dueño sobre una PERSONA a sus transacciones persona-a-persona:
    subcontractor → COGS/Subcontractors (salidas) · customer → REVENUE (entradas) ·
    owner → Owner Draw / Owner Contribution según la dirección. La decisión es del DUEÑO →
    source="manual", sin regla de por medio (el proponedor la propondrá con el hecho en el perfil).
    Devuelve cuántas transacciones corrigió."""
    if role not in PERSON_ROLES:
        return 0
    revenue_cats = categories_for(sector).get("REVENUE") or ["Sales Revenue"]
    target = name.upper()
    updated = 0
    txs = session.exec(
        select(Transaction).where(Transaction.company_id == company_id)
    ).all()
    for t in txs:
        if p2p_counterparty(t.description) != target:
            continue
        if role == "subcontractor":
            if t.is_inflow:  # una ENTRADA de un subcontratista (reembolso…) queda para revisión
                continue
            new = (AccountType.COGS, "Subcontractors")
        elif role == "customer":
            if not t.is_inflow:  # una SALIDA hacia un cliente (refund…) queda para revisión
                continue
            new = (AccountType.REVENUE, revenue_cats[0])
        else:  # owner: la plata del dueño no es del P&L
            new = (AccountType.NON_PL, "Owner Contribution" if t.is_inflow else "Owner Draw")
        t.account_type, t.category = new
        t.subcategory = name.title()
        t.source = "manual"          # lo decidió el dueño (la entrevista es su voz)
        t.matched_rule_id = None
        t.needs_review = False
        t.review_question = None
        session.add(t)
        updated += 1
    return updated


def format_profile_facts(profile: Optional[dict]) -> str:
    """Renderiza el perfil como bloque de HECHOS en inglés para los prompts (categorización,
    clarify y proponedor). Devuelve "" si no hay nada que decir — el prompt queda igual que antes."""
    if not profile:
        return ""
    lines: list[str] = []
    v = profile.get("uses_subcontractors")
    if v:
        lines.append("- The owner USES subcontractors." if v == "yes"
                     else "- The owner does NOT use subcontractors.")
    methods = profile.get("client_payment_methods")
    if methods:
        pretty = {"checks": "checks", "zelle": "Zelle", "cash": "cash",
                  "card": "card", "bank_transfer": "bank transfer"}
        lines.append("- Clients pay by: " + ", ".join(pretty.get(m, m) for m in methods) + ".")
    v = profile.get("payroll")
    if v:
        payroll = {"none": "no employees on payroll", "w2": "W-2 employees",
                   "1099": "1099 contractors only", "both": "both W-2 employees and 1099 contractors"}
        lines.append(f"- Payroll: {payroll[v]}.")
    v = profile.get("work_vehicles")
    if v:
        lines.append("- Uses vehicles for work." if v == "yes" else "- No work vehicles.")
    v = profile.get("personal_expenses_mixed")
    if v:
        mixed = {"yes": "this bank account MIXES personal expenses (expect owner draws)",
                 "sometimes": "this account sometimes has personal expenses",
                 "no": "this account is business-only"}
        lines.append(f"- {mixed[v]}.")
    people = profile.get("people") or {}
    for key, p in people.items():
        role = p.get("role")
        if role in PERSON_ROLES:
            label = {"subcontractor": "a SUBCONTRACTOR", "customer": "a CUSTOMER/client",
                     "owner": "the OWNER (their transfers are owner draw/contribution, NON_PL)"}[role]
            lines.append(f'- "{p.get("name", key.title())}" is {label} (confirmed by the owner).')
    return "\n".join(lines)
