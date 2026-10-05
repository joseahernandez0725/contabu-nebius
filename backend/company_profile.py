"""
ContaBu — los HECHOS del cliente que ya están guardados (`Company.profile`) y el detector de pagos a
personas.

🔄 2-oct-2026 (FASE 13 Paso 5, decisión de Jose): **se eliminó la entrevista** — la ronda 1 del
onboarding (`PATCH /profile`, `sanitize_round1`) y la ronda 2 de la pestaña Auditoría
(`/interview`, `interview_questions`, `apply_person_answer`, `Interview.tsx`). Con el pivote al
bookkeeper, quien se registra es un bookkeeper, y a él no se le pregunta "¿usas subcontratistas?".
El criterio del bookkeeper ahora entra por las NOTAS de la pestaña Auditoría (F11), y lo que sabe el
cliente, por sus respuestas a las preguntas sobre transferencias.

Se CONSERVA, a propósito:
- `format_profile_facts()`: los hechos YA guardados en `Company.profile` (la columna se queda, sin
  migración) se siguen inyectando al prompt del proponedor de reglas (`ai.propose_rules`).
- `p2p_counterparty()`: saca el nombre de la persona de un Zelle/Venmo/… → es la llave de la memoria
  del libro (preguntas al cliente).
Todo en inglés (Regla de Oro).
"""

import re
from typing import Optional

# Roles que el dueño puede asignar a una persona en la ronda 2.
PERSON_ROLES = ("subcontractor", "customer", "owner")

# Canales persona-a-persona: el mismo canal puede ser cliente, subcontratista o retiro del dueño —
# por eso el proponedor los salta y por eso son LA pregunta de la ronda 2.
_P2P_RE = re.compile(r"\b(ZELLE|VENMO|CASH\s?APP|CASHAPP|QUICKPAY|APPLE\s?CASH)\b")
# El nombre viene después de TO/FROM; se corta en la basura dinámica (refs, IDs, fechas).
_NAME_AFTER_RE = re.compile(r"\b(?:TO|FROM)[:\s]+([A-Z][A-Z .'-]*[A-Z])")
_NAME_STOPWORDS = {"ON", "REF", "CONF", "ID", "MEMO", "PPD", "WEB", "PAYMENT", "TRANSFER", "JPM",
                   "INC", "LLC", "CORP", "LTD"}


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
    raw = re.sub(r"[^A-Z']+", " ", m.group(1)).split()
    # Referencia bancaria PEGADA a dígitos al final ("WFCT0ZND…", "BACs0hm…", "USBkI79…"): el regex
    # corta en el dígito y deja el prefijo de letras como si fuera un apellido → si el capture
    # termina pegado a un dígito, la ÚLTIMA palabra del capture es referencia, no nombre. Se quita
    # ANTES del filtro de stopwords (si no, el pop se comería una palabra buena).
    if raw and m.end(1) < len(text) and text[m.end(1)].isdigit():
        raw.pop()
    words = []
    for w in raw:
        if w in _NAME_STOPWORDS:
            break
        # Letras sueltas SON parte del nombre (inicial: "LAURA L GALINDO"; conector: "PIZZA N PASTA").
        words.append(w)
        if len(words) == 4:  # nombres reales: 2-4 palabras; más es basura
            break
    while words and len(words[-1]) < 2:  # sin iniciales colgando al final ("LAURA L" → "LAURA")
        words.pop()
    name = " ".join(words)
    return name if len(name) >= 4 else None


def format_profile_facts(profile: Optional[dict]) -> str:
    """Renderiza el perfil como bloque de HECHOS en inglés para los prompts (categorización,
    clarify y proponedor). Devuelve "" si no hay nada que decir — el prompt queda igual que antes."""
    if not profile:
        return ""
    lines: list[str] = []
    trade = profile.get("trade")
    if trade:
        lines.append(f'- The owner describes the business as: "{trade}" '
                     "(may be written in the owner's language).")
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
