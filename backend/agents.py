"""
ContaBu — LOS AGENTES DE LA FÓRMULA DEL LIBRO (FASE 11, Auditoría / IA · 7-oct-2026).
Ver `docs/FASE 11.md` → "🔄 Actualización (3-oct) — La fórmula del libro".

- **④ REDACTOR** (`draft`): convierte las NOTAS del bookkeeper + su HISTORIAL DE CORRECCIONES en
  FILAS de la fórmula (palabra clave, ventana de fechas, prioridad → categoría), explicadas en el
  idioma del bookkeeper y probadas contra la historia del libro. NUNCA aplica: el bookkeeper aprueba.
- **① REVISOR** (`review`): mira el resultado (los hallazgos deterministas de `audit.py`) y juzga
  cuáles son un problema y cuáles son legítimos. Sus hallazgos alimentan la siguiente propuesta del ④.

🔴 SEGURIDAD — NO NEGOCIABLE (Jose, 3-oct). Se garantiza AQUÍ, en código, no en el prompt:
- Al modelo entran SOLO: las notas, las HUELLAS de comercio (una frase corta por comercio, sin número
  de tienda ni referencias), los PARES DE CORRECCIÓN (de → a, con conteos), las reglas vivas y el plan
  de cuentas. NUNCA descripciones completas, montos ni nombres de personas.
- Los nombres de persona se reemplazan por marcadores (`PERSON 1`) ANTES de llamar, y se devuelven a
  su lugar en la respuesta (`Pseudonyms`). Un marcador que el modelo deforma queda sin resolver y el
  validador lo rechaza.
- Lo que devuelve el ④ pasa por `formula.check_rows`: lo que no cumple se descarta y se cuenta.
- La IA no categoriza: ningún camino de aquí escribe `category` en una transacción.
- `privacy_scan()` revisa lo que de verdad se envió (`nemotron.SENT`), para medirlo, no suponerlo.
"""

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field
from sqlmodel import Session, select

import agent_calls
import book_notes
import book_review
import formula
import merchant_verifier
import nemotron
from audit import audit_categorization
from company_profile import p2p_counterparty
from models import CategorizationChange, CategoryRule, Company, Transaction
from rules import is_check, merchant_signature, rule_overlaps, suggest_keyword

LANG_NAMES = {"es": "Spanish", "en": "English"}
_UNRESOLVED = re.compile(r"\bPERSON\s*_?\d+\b", re.IGNORECASE)
MAX_ROWS = 8            # filas por propuesta: más es ruido para el bookkeeper
MAX_MERCHANTS = 60      # comercios en el contexto (los más frecuentes)
MAX_FINDINGS = 25       # hallazgos que revisa el ① por llamada


# ── Nombres de persona → marcadores ──────────────────────────────────────────────────────────────

class Pseudonyms:
    """Nombres de persona del libro ↔ marcadores (`PERSON 1`). El modelo nunca ve el nombre.

    El marcador lleva un espacio, no `_`: así puede quedar dentro de una palabra clave del motor
    ("ZELLE*PERSON 1") y, al devolverle el nombre, sigue siendo una palabra clave válida. Si el modelo
    lo deforma (`PERSON_1`), no se resuelve y el validador lo rechaza por el `_`."""

    def __init__(self, names: set[str]):
        ordered = sorted({n.upper() for n in names if n and len(n) >= 4}, key=lambda n: (-len(n), n))
        self.to_mark = {n: f"PERSON {i}" for i, n in enumerate(ordered, 1)}
        self.to_name = {m: n for n, m in self.to_mark.items()}
        self._re = (re.compile(r"\b(" + "|".join(re.escape(n) for n in ordered) + r")\b", re.IGNORECASE)
                    if ordered else None)
        self._back = re.compile(r"\bPERSON (\d+)\b")

    def mask(self, text: Optional[str]) -> Optional[str]:
        if not text or self._re is None:
            return text
        return self._re.sub(lambda m: self.to_mark[m.group(1).upper()], text)

    def unmask(self, text: Optional[str]) -> Optional[str]:
        if not text:
            return text
        return self._back.sub(lambda m: self.to_name.get(f"PERSON {m.group(1)}", m.group(0)), text)


# ── El contexto del libro (lo ÚNICO que ve el modelo) ────────────────────────────────────────────

@dataclass
class BookView:
    company: Company
    ctx: formula.BookContext
    names: Pseudonyms
    merchants: list[dict]
    corrections: list[dict]                 # grupos de corrección: id "C1"…, huella, de → a, conteo
    correction_ids: dict[str, list[int]]    # "C1" → ids de `categorizationchange` (evidencia)
    notes: list[dict]
    rules: list[dict]
    chart: dict[str, list[str]]
    persons_in_txs: set[str] = field(default_factory=set)
    cutoff: Optional[date] = None   # medición: el agente (y sus filtros) solo ven lo anterior
    web: dict = field(default_factory=dict)   # tx.id → MerchantLookup (② verificador, caché de la oficina)
    # id de corrección → (movimiento, dónde terminó "SECCIÓN/Categoría"): para comprobar que la evidencia
    # que cita el ④ es de ESE comercio y que son 2+ movimientos (13-oct).
    change_info: dict = field(default_factory=dict)


def _shorten_if_full(phrase: str, full_descriptions: set[str]) -> str:
    """Si la frase ES una descripción completa del banco ("MONTHLY SERVICE FEE"), se le quita la última
    palabra hasta que deje de serlo: la regla dice NUNCA descripciones completas, aunque la línea del
    banco no traiga más que el nombre del comercio. Sigue siendo una palabra clave que caza."""
    words = (phrase or "").split()
    while len(words) > 1 and " ".join(words).upper() in full_descriptions:
        words.pop()
    return " ".join(words)


def _huella(t: Transaction, names: Pseudonyms) -> tuple[str, str]:
    """(llave de agrupación, frase visible) de un movimiento. La frase es corta y estable: la que el
    motor usaría como palabra clave (`suggest_keyword`), sin número de tienda ni referencias. Para un
    pago a persona, la frase dice la DIRECCIÓN + el marcador: "PAYMENT TO PERSON 2" si el dinero salió,
    "PAYMENT FROM PERSON 2" si entró. (Hasta el 13-oct decía siempre "TO": el ④ recibía al revés el
    dinero que ENTRÓ de una persona — un cliente que paga parecía un pago a un trabajador.) La dirección
    va también en la llave: lo que te pagó y lo que le pagaste a la misma persona no se mezclan."""
    person = p2p_counterparty(t.description)
    if person:
        way = "FROM" if t.is_inflow else "TO"
        return f"P2P:{way}:{person}", names.mask(f"PAYMENT {way} {person}")
    if is_check(t.description):
        return "CHECK", "CHECK"
    sig = merchant_signature(t.description)
    phrase = suggest_keyword(t.description) or sig
    return sig or phrase, names.mask(phrase)


def build_view(session: Session, company: Company, *, cutoff: Optional[date] = None) -> BookView:
    """Arma lo que el modelo puede ver de ESTE libro. Con `cutoff` (la medición), las correcciones de
    movimientos posteriores NO entran: el agente aprende solo del pasado."""
    ctx = formula.BookContext(session, company)
    # Nombres protegidos: contrapartes P2P + titulares de líneas ACH (`IND NAME:`), ✅ 8-oct.
    persons = merchant_verifier.protected_names(ctx.txs)
    names = Pseudonyms(persons)
    full = {(t.description or "").strip().upper() for t in ctx.txs if t.description}
    web = merchant_verifier.web_by_tx(session, company, ctx.txs)

    # Comercios: conteo por mes, dirección y cómo están categorizados hoy. Sin montos.
    # Con fecha de corte (la medición) el modelo ve SOLO el pasado: si viera cómo quedaron las filas
    # del periodo de medición, vería las respuestas (las correcciones a mano con que se le mide).
    visible = [t for t in ctx.txs if cutoff is None or t.date < cutoff]
    groups: dict[str, list[Transaction]] = defaultdict(list)
    phrase_of: dict[str, Counter] = defaultdict(Counter)
    for t in visible:
        key, phrase = _huella(t, names)
        if key == "CHECK":
            continue  # un cheque no dice a quién se pagó: no es un comercio
        groups[key].append(t)
        phrase_of[key][_shorten_if_full(phrase, full)] += 1
    merchants = []
    for key, txs in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:MAX_MERCHANTS]:
        cats = Counter(_label(t) for t in txs)
        srcs = Counter((t.source or "pending") for t in txs)
        merchants.append({
            "keyword": phrase_of[key].most_common(1)[0][0],
            "count": len(txs),
            # Cuántos meses distintos, no cuáles: con la lista de meses a la vista, el modelo los copiaba
            # como ventana de fechas ("2026-07" → "2026-08") (✅ medido el 7-oct).
            "months_seen": len({t.date.strftime("%Y-%m") for t in txs}),
            "direction": "in" if sum(1 for t in txs if t.is_inflow) > len(txs) / 2 else "out",
            "categories": dict(cats.most_common()), "decided_by": dict(srcs.most_common()),
            "person_payment": key.startswith("P2P:"),
        })
        # El ② verificador: SOLO el dato estructurado (tipo de la lista cerrada + confianza). Ni el
        # enlace ni el texto de internet entran al prompt (protección contra inyección de instrucciones).
        hit = next((web[t.id] for t in txs if t.id in web), None)
        if hit is not None:
            merchants[-1]["web"] = {"business_type": hit.business_type, "confidence": hit.confidence}

    # Correcciones a mano, POR MOVIMIENTO: de dónde salió (la primera) y dónde terminó (la última). Una
    # cadena "Pendiente → Office → Materials" es UNA corrección hacia Materials; mandada como dos pares,
    # el modelo tomaba el primero (✅ medido el 7-oct con Lowe's). Se agrupan por comercio y destino.
    tx_by_id = {t.id: t for t in ctx.txs}
    changes = session.exec(select(CategorizationChange).where(
        CategorizationChange.company_id == company.id, CategorizationChange.via == "manual")
        .order_by(CategorizationChange.id)).all()
    per_tx: dict[int, list] = defaultdict(list)
    for ch in changes:
        t = tx_by_id.get(ch.transaction_id)
        if t is not None and not (cutoff and t.date >= cutoff):
            per_tx[t.id].append(ch)
    pairs: dict[tuple, list] = defaultdict(list)
    for tx_id, chs in per_tx.items():
        t, first, last = tx_by_id[tx_id], chs[0], chs[-1]
        _key, phrase = _huella(t, names)
        before = f"{first.account_type_before}/{first.category_before}" if first.category_before else "PENDING"
        pairs[(_shorten_if_full(phrase, full), before, f"{last.account_type_after}/{last.category_after}")].append((chs, t))
    destinations: dict[str, set] = defaultdict(set)
    for (phrase, _b, after) in pairs:
        destinations[phrase].add(after)
    change_info = {ch.id: (t.id, f"{chs[-1].account_type_after}/{chs[-1].category_after}")
                   for (_p, _b, _a), items in pairs.items() for chs, t in items for ch in chs}
    corrections, correction_ids = [], {}
    for i, ((phrase, before, after), items) in enumerate(sorted(pairs.items(), key=lambda kv: -len(kv[1])), 1):
        ref = f"C{i}"
        correction_ids[ref] = [ch.id for chs, _ in items for ch in chs]
        corrections.append({"ref": ref, "merchant": phrase, "from": before, "ended_as": after,
                            "transactions": len(items),
                            "conflict": len(destinations[phrase]) > 1})

    notes = []
    for n in book_notes.active_notes(session, company.id):
        note = {"id": n.id, "kind": n.kind, "priority": n.priority, "text": names.mask(n.text),
                "merchant": names.mask(n.merchant), "valid_from": n.valid_from.isoformat() if n.valid_from else None,
                "valid_to": n.valid_to.isoformat() if n.valid_to else None}
        if n.valid_from or n.valid_to:
            # Qué comercios tienen movimientos DENTRO del periodo de la nota (lo cuenta el código, no
            # el modelo): es lo que una excepción con ventana de fechas tendría que cubrir.
            inside: Counter = Counter()
            outside: Counter = Counter()
            for t in visible:
                key, phrase = _huella(t, names)
                if key == "CHECK":
                    continue
                phrase = _shorten_if_full(phrase, full)
                if (n.valid_from is None or t.date >= n.valid_from) and (n.valid_to is None or t.date <= n.valid_to):
                    inside[(phrase, _label(t))] += 1
                else:
                    outside[phrase] += 1
            # "only_seen_in_period": el comercio no aparece NUNCA fuera del periodo de la nota. Es la
            # señal de un gasto de ese periodo (un viaje), frente a lo que sigue igual (renta, nómina).
            # Sin la categoría actual: el modelo la copiaba ("STARBUCKS → Meals" con ventana) en vez de
            # deducirla de la nota (✅ medido el 7-oct).
            merged: Counter = Counter()
            for (k, _lab), c in inside.items():
                merged[k] += c
            listed = [{"keyword": k, "rows": c, "only_seen_in_period": outside[k] == 0} for k, c in merged.items()]
            listed.sort(key=lambda m: (not m["only_seen_in_period"], -m["rows"]))
            note["merchants_inside_period"] = listed[:15]
        notes.append(note)

    rules = [{"keyword": _shorten_if_full(names.mask(r.match_text), full),
              "then": f"{r.account_type.value if r.account_type else '?'}/{r.category}", "priority": r.priority,
              "valid_from": r.valid_from.isoformat() if r.valid_from else None,
              "valid_to": r.valid_to.isoformat() if r.valid_to else None} for r in ctx.rules]

    return BookView(company=company, ctx=ctx, names=names, merchants=merchants, corrections=corrections,
                    correction_ids=correction_ids, notes=notes, rules=rules, chart=ctx.chart,
                    persons_in_txs=persons, cutoff=cutoff, web=web, change_info=change_info)


def _label(t: Transaction) -> str:
    """"SECCIÓN/Categoría" de un movimiento, o PENDING. Sección y categoría juntas: separadas, el modelo
    las mezclaba (una categoría de COGS con sección de gastos operativos)."""
    if not t.category or t.account_type is None:
        return "PENDING"
    return f"{t.account_type.value}/{t.category}"


def _chart_text(chart: dict[str, list[str]]) -> str:
    return "\n".join(f"  {sec}: " + " | ".join(cats) for sec, cats in chart.items() if cats)


# ── ④ EL REDACTOR ─────────────────────────────────────────────────────────────────────────────────

Section = Literal["REVENUE", "COGS", "OPERATING_EXPENSE", "OTHER_INCOME", "OTHER_EXPENSE", "NON_PL"]


class DraftRow(BaseModel):
    """Lo que el modelo puede devolver por fila (el esquema que se le manda). El validador de verdad
    es `formula.FormulaRow` + `formula.check_row`, contra el libro. Las fechas van como `date` para que
    el esquema diga `format: date`; si el proveedor no lo hace cumplir, el validador igual lo exige."""
    keyword: str
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    priority: Optional[int] = None
    amount_min: Optional[float] = None
    amount_max: Optional[float] = None
    account_type: Section
    category: str
    subcategory: Optional[str] = None
    explanation: str
    from_notes: list[int] = Field(default_factory=list)
    from_corrections: list[str] = Field(default_factory=list)


class DraftOut(BaseModel):
    rows: list[DraftRow]


_DRAFT_SYSTEM = """You are the FORMULA DRAFTER of a bookkeeping firm. Each client book has a "formula": an
ordered list of rows "IF the bank description contains KEYWORD (and the date is inside the optional
window) THEN section/category". A deterministic engine applies the rows; you never categorize anything
yourself. You only PROPOSE new rows; the bookkeeper approves or discards each one.

Hard rules:
- Output ONLY JSON: {"rows": [...]}. Each row has exactly these fields: keyword, valid_from, valid_to,
  priority, amount_min, amount_max, account_type, category, subcategory, explanation, from_notes,
  from_corrections. Never code.
- amount_min / amount_max: null, unless a NOTE gives an amount threshold (e.g. "over $50 is personal"
  → amount_min 50.01 for that exception). You never see transaction amounts; never invent one.
- keyword: copy it from a merchant "keyword" below, or a SHORTER stable part of it (e.g. "STARBUCKS"
  from "STARBUCKS STORE PITTSBURG"). Uppercase letters, digits, spaces and & ' . - / # * only.
  `*` means "anything in between". Never a bare generic word (PAYMENT, ONLINE, DEBIT) or only digits.
- Placeholders like "PERSON 1" stand for private people: keep them EXACTLY as written; never guess a name.
- Never propose a row for payments to a PERSON (merchant "person_payment": true) unless a note or the
  bookkeeper's corrections say what those payments are: only the client knows.
- valid_from / valid_to: always null here. Notes with a date period are handled by a separate step;
  never create date-window rows.
- account_type and category MUST come from the chart of accounts below, and the category must belong
  to that section.
- From corrections: "ended_as" is where the bookkeeper finally put those transactions. Propose a row
  when a merchant ended_as the same SECTION/Category in 2 or more transactions; use exactly that
  section and category (never the "from"). If "conflict" is true, the bookkeeper put that merchant in
  different categories: do NOT propose a row for it.
- Recurring merchants that are still PENDING (2+ rows, no rule, no correction): propose a row ONLY if
  the merchant carries "web" evidence with confidence high or medium, and pick the category that fits
  that business type; otherwise do not propose it (only the bookkeeper knows).
- A merchant may carry "web": its business type as verified on the internet, with a confidence. With
  confidence high or medium, use it as evidence for the category (e.g. cleaning_supplies → a supplies
  category) and say so in the explanation. With low confidence, do not rely on it.
- Skip merchants already decided by a rule, unless corrections show that rule is wrong.
- Do NOT duplicate an existing rule (same keyword, same window, same category).
- Notes marked "dont_report" are not instructions to create rows.
- explanation: WRITE IT IN {lang} (even though these instructions are in English), with no English
  words mixed in. ONE short sentence (max ~15 words) saying what the row DECIDES and why, e.g. in Spanish
  "Lowe's va a Materials: lo corregiste así 3 veces." Do not say "this row", "the bookkeeper", ids,
  refs (C1, F2) or field names.
- from_notes: ids of the notes used ([] if none). from_corrections: ONLY refs that appear in the
  corrections list above ("C1"…), or [] if the row does not come from corrections. Never invent a ref.
- At most {max_rows} rows. Every row must be justified by corrections, by a note, or by a recurring
  PENDING merchant (2+ rows); if nothing qualifies, return an empty list."""


_NOTE_SYSTEM = """You turn ONE bookkeeper note about a date period into EXCEPTION rows of a bookkeeping
formula. A deterministic engine applies the rows; you never categorize anything yourself, you only
propose rows that the bookkeeper approves.

You get the note and the merchants that appear ONLY during the note's period. For each merchant, decide
whether the note's statement applies to it. Example: "the owner was on vacation in Europe from Aug 3 to
Aug 17; purchases made there are personal" applies to coffee shops, restaurants, hotels or stores used
during the trip → those are the owner's personal purchases → section NON_PL, category Owner Draw.

Output ONLY JSON: {"rows": [...]}, one row per merchant the note applies to, each with exactly:
keyword (copy the merchant keyword exactly), valid_from and valid_to (copy the note's period exactly,
"YYYY-MM-DD"), priority (null), account_type and category (from the chart of accounts; the category must
belong to that section), subcategory (null), explanation (ONE short sentence WRITTEN IN {lang}, no English words
mixed in, saying what the row decides, e.g. in Spanish "Starbucks del 3 al 17 de agosto va a Retiro del
dueño: compras personales del viaje."; no ids), from_notes ([the note
id]), from_corrections ([]).
If the note does not apply to any merchant, return {"rows": []}."""


def _note_user(view: BookView, note: dict) -> str:
    merchants = [m for m in note.get("merchants_inside_period", []) if m["only_seen_in_period"]]
    return f"""Chart of accounts (section: categories):
{_chart_text(view.chart)}

The note:
{json.dumps({k: note[k] for k in ("id", "text", "valid_from", "valid_to")}, ensure_ascii=False)}

Merchants seen ONLY during that period (rows = how many transactions):
{json.dumps([{"keyword": m["keyword"], "rows": m["rows"]} for m in merchants], ensure_ascii=False)}"""


def _draft_user(view: BookView, findings: list[dict], dismissed: Optional[list] = None) -> str:
    return f"""Business sector: {view.company.sector or "unknown"}

Chart of accounts (section: categories):
{_chart_text(view.chart)}

Bookkeeper notes:
{json.dumps(view.notes, ensure_ascii=False)}

Manual corrections by the bookkeeper (per merchant: where the transactions came from and where the bookkeeper finally put them):
{json.dumps(view.corrections, ensure_ascii=False) if view.corrections else "(none — use from_corrections: [])"}

Current formula rows (existing rules):
{json.dumps(view.rules, ensure_ascii=False)}

Merchants in this book (no amounts; "categories" = how they are categorized today):
{json.dumps(view.merchants, ensure_ascii=False)}

Reviewer findings (from the reviewer agent; may be empty):
{json.dumps(findings, ensure_ascii=False)}

Rows the bookkeeper DISMISSED before (never propose them again; learn from the reason):
{json.dumps(dismissed or [], ensure_ascii=False)}"""


@dataclass
class Proposal:
    row: formula.FormulaRow
    matches: int
    test: dict
    web: list = field(default_factory=list)   # evidencia del ② verificador (Tavily)
    notes: list = field(default_factory=list)  # las notas que la fila cita (texto original, para la pantalla)


@dataclass
class DraftResult:
    proposals: list[Proposal]
    rejected: list[dict]
    usage: Optional[nemotron.Usage]     # el total de las llamadas
    outcome: str = "ok"
    calls: int = 0


def history_test(view: BookView, row: formula.FormulaRow) -> dict:
    """LA PRUEBA CONTRA LA HISTORIA de una fila propuesta, con el motor real (`formula.decide`):
    qué movimientos cambiarían de categoría, cuáles quedan protegidos (lo escrito a mano manda) y
    cuántas correcciones del bookkeeper reproduce o contradice."""
    before_rules = view.ctx.rules
    after_rules = formula.with_rows(before_rules, [row], view.company.id)
    changes: Counter = Counter()
    changed_ids, protected_ids, realigned_ids = [], [], []
    changed: list[dict] = []   # el detalle para la pantalla (comercio limpio, fecha, monto, cuenta, de → a)
    agrees = disagrees = 0
    for t in view.ctx.txs:
        if view.cutoff and t.date >= view.cutoff:
            continue   # medición: la prueba (y el filtro que usa) no mira el periodo de medición
        before, after = formula.decide(before_rules, t), formula.decide(after_rules, t)
        current = (t.account_type.value if t.account_type else None, t.category)
        if before == after:
            # Al aprobar, el motor re-aplica la precedencia de TODAS las reglas
            # (`rules.reapply_rule_precedence`): una fila que había quedado desalineada con su propia
            # regla vuelve a ella aunque esta fila nueva no la toque (✅ 7-oct: PG&E en Rivera). La
            # prueba lo anuncia aparte para que lo que se aprueba sea lo que pasa.
            if (t.source == "rule" and before.source == "rule"
                    and (before.account_type, before.category) != current):
                realigned_ids.append(t.id)
            continue
        if t.source == "manual":
            protected_ids.append(t.id)          # el motor no la toca: lo escrito a mano manda
            truth = (t.account_type.value if t.account_type else None, t.category)
            if (after.account_type, after.category) == truth:
                agrees += 1
            else:
                disagrees += 1
            continue
        changed_ids.append(t.id)
        changes[(before.category or "PENDING", after.category)] += 1
        if len(changed) < 50:
            changed.append({**book_review.tx_summary(t), "from": before.category or "PENDING", "to": after.category})
    return {
        "changes": len(changed_ids), "changed_ids": changed_ids, "changed": changed,
        "by_pair": [{"from": a, "to": b, "rows": n} for (a, b), n in changes.most_common()],
        "protected_manual": len(protected_ids), "protected_ids": protected_ids,
        "realigned": len(realigned_ids), "realigned_ids": realigned_ids,
        "agrees_with_corrections": agrees, "disagrees_with_corrections": disagrees,
    }


def draft(session: Session, company: Company, *, lang: str = "es", reasoning: bool = False,
          cutoff: Optional[date] = None, user_id: Optional[int] = None, record: bool = True,
          model: Optional[str] = None, focus_tx_ids: Optional[list[int]] = None) -> DraftResult:
    """④ — Nemotron propone filas. Devuelve las válidas con su prueba contra la historia y las
    rechazadas con sus códigos. Guarda el costo en `agentcall` (salvo `record=False`, la medición).

    `focus_tx_ids`: "pedirle al ④ una regla para ESTE comercio" (un grupo de Pendientes): una sola
    llamada, centrada en ese comercio, y solo se devuelven filas que cazan esos movimientos."""
    if record:
        agent_calls.check_cap(session, company)
    view = build_view(session, company, cutoff=cutoff)
    # Los hallazgos del revisor alimentan al redactor (el ciclo ① → ④). En la medición no: mirarían
    # filas del periodo de medición.
    findings = [] if cutoff else review_input(session, company, view)[0][:10]
    language = LANG_NAMES.get(lang, "Spanish")
    # Lo que el bookkeeper ya descartó (con su motivo): no se vuelve a proponer (filtro en código) y el
    # modelo lee los motivos como contexto.
    gone = book_review.dismissals(session, company)
    dismissed_keys = {(d.keyword.upper(), d.valid_from, d.valid_to, d.category) for d in gone}
    dismissed_ctx = [{"keyword": view.names.mask(d.keyword), "category": d.category, "reason": d.reason_code,
                      "comment": view.names.mask(d.reason_text) if d.reason_text else None} for d in gone[:30]]
    focus = [t for t in view.ctx.txs if focus_tx_ids and t.id in set(focus_tx_ids)]
    calls: list[tuple[str, str]] = []
    # 1) Una llamada CORTA por cada nota con periodo que tenga comercios vistos solo en ese periodo.
    #    Un solo prompt con todo hacía que Nano copiara la categoría actual y que Super no propusiera
    #    nada (✅ medido el 7-oct); enfocado, el modelo chico decide una cosa a la vez.
    for note in ([] if focus else view.notes):
        if note["kind"] == "keep_in_mind" and any(m["only_seen_in_period"] for m in note.get("merchants_inside_period", [])):
            calls.append((_NOTE_SYSTEM.replace("{lang}", language), _note_user(view, note)))
    # 2) Correcciones + Pendientes recurrentes (sin ventanas de fechas).
    user = _draft_user(view, findings, dismissed_ctx)
    if focus:
        keyword = _huella(focus[0], view.names)[1]
        user += (f"\n\nFOCUS: the bookkeeper asked for a row for ONE merchant only: \"{keyword}\" "
                 f"({len(focus)} pending transactions). Propose at most one row, for that merchant only.")
    calls.append((_DRAFT_SYSTEM.replace("{lang}", language).replace("{max_rows}", str(MAX_ROWS)), user))

    note_text = {n.id: n.text for n in book_notes.list_notes(session, company.id)}
    raw_rows, usages, outcome = [], [], "ok"
    for system, user in calls:
        if record and len(usages) > 0:
            agent_calls.check_cap(session, company)
        try:
            reply = nemotron.chat_json(system, user, DraftOut, model=model, reasoning=reasoning, max_tokens=3000)
        except nemotron.NemotronError as e:
            usages.append(e.usage)
            outcome = e.code
            if record:
                agent_calls.record(session, company, "drafter", e.usage, outcome=e.code, user_id=user_id,
                                   model=model or nemotron.model_name())
            continue
        rows = reply.data.get("rows") if isinstance(reply.data, dict) else reply.data
        rows = rows if isinstance(rows, list) else [rows]
        raw_rows.extend(rows)
        usages.append(reply.usage)
        if record:
            agent_calls.record(session, company, "drafter", reply.usage, user_id=user_id, items_out=len(rows))

    proposals, rejected = [], []
    seen: set = set()
    for raw in raw_rows[:MAX_ROWS * 3]:
        candidate, errors = _from_model(raw, view)
        if errors:
            rejected.append({"raw": raw, "errors": errors})
            continue
        checked = formula.check_row(candidate, view.ctx)
        if not checked.ok:
            rejected.append({"raw": raw, "errors": checked.errors})
            continue
        key = (checked.row.keyword.upper(), checked.row.valid_from, checked.row.valid_to)
        if key in seen:
            rejected.append({"raw": raw, "errors": ["repeated_in_proposal"]})
            continue
        if key + (checked.row.category,) in dismissed_keys:
            rejected.append({"raw": raw, "errors": ["dismissed_before"]})   # ya lo descartaste
            continue
        if focus:
            rule = formula.to_rule(checked.row, company.id)
            if not any(formula.match_rule([rule], formula.matchable_text(t.description, t.description_extra),
                                          t.date, t.amount) for t in focus):
                rejected.append({"raw": raw, "errors": ["outside_focus"]})
                continue
        test = history_test(view, checked.row)
        # Una fila que no cambia NADA no se le muestra al bookkeeper (Jose, 8-oct). En la medición sí se
        # puntúa: una fila puede no tocar el pasado (lo escrito a mano manda) y acertar el futuro.
        # (Las re-alineadas no cuentan: el motor las arregla con CUALQUIER aprobación, no con esta fila.)
        if cutoff is None and test["changes"] == 0:
            rejected.append({"raw": raw, "errors": ["no_effect"]})
            continue
        # Una regla para pagos a PERSONAS sin nota ni corrección que la respalde es una adivinanza: solo
        # el cliente sabe qué fue ese Zelle (✅ 8-oct: el ④ propuso "PAYMENT TO DANIEL RIVERA → Owner Draw").
        # Se miran TODAS las filas que la regla caza, no solo las que cambia: en la medición, antes de la
        # fecha de corte puede no cambiar ninguna y el control no se aplicaba (✅ 13-oct: pasó
        # "PAYMENT TO MARIA LOPEZ → Subcontractors").
        rule = formula.to_rule(checked.row, company.id)
        hit = [t for t in view.ctx.txs
               if (view.cutoff is None or t.date < view.cutoff)
               and formula.match_rule([rule], formula.matchable_text(t.description, t.description_extra),
                                      t.date, t.amount)]
        # Las correcciones citadas cuentan solo si son de movimientos que ESTA fila caza y terminaron en
        # ESTA categoría; y "una corrección no es un patrón": hacen falta 2+ movimientos (la misma vara que
        # la línea base). ✅ 13-oct: ULINE y Ecolab se proponían con UNA corrección del pasado.
        hit_ids = {t.id for t in hit}
        target = f"{checked.row.account_type.value}/{checked.row.category}"
        backing = {view.change_info[c][0] for c in checked.row.from_changes
                   if c in view.change_info and view.change_info[c][0] in hit_ids and view.change_info[c][1] == target}
        if checked.row.from_changes and not checked.row.from_notes and len(backing) < 2:
            rejected.append({"raw": raw, "errors": ["unrelated_evidence" if not backing else "weak_evidence"]})
            continue
        no_history = not checked.row.from_notes and not checked.row.from_changes
        if hit and all(p2p_counterparty(t.description) for t in hit) and no_history:
            rejected.append({"raw": raw, "errors": ["person_without_evidence"]})
            continue
        # Una fila sin nota ni corrección tuya ES una adivinanza del sector: solo pasa con evidencia de
        # internet (② con confianza alta o media) sobre ese comercio (✅ 13-oct: 4 de las 5 filas que la
        # medición daba como mal cambiadas eran esto: GUSTO, ULINE, Restaurant Depot, Ecolab).
        web_ok = any(view.web.get(t.id) is not None and view.web[t.id].confidence in ("high", "medium") for t in hit)
        if no_history and not web_ok:
            rejected.append({"raw": raw, "errors": ["no_evidence"]})
            continue
        # Una fila que CONTRADICE lo que el bookkeeper escribió a mano no se le muestra: lo escrito a mano
        # manda. Se garantiza aquí porque el prompt no basta (✅ 7-oct: Nano propuso SHELL → Vehicle Fuel
        # aunque las correcciones de Shell estaban en conflicto).
        if test["disagrees_with_corrections"]:
            rejected.append({"raw": raw, "errors": ["contradicts_corrections"]})
            continue
        seen.add(key)
        proposals.append(Proposal(row=checked.row, matches=checked.matches, test=test,
                                  web=web_evidence(view, checked.row),
                                  notes=[{"id": i, "text": note_text.get(i, "")} for i in checked.row.from_notes]))
    if record:
        session.commit()
    if outcome != "ok" and proposals:
        outcome = f"partial:{outcome}"   # una llamada falló, otra sí propuso
    return DraftResult(proposals=proposals, rejected=rejected, usage=sum_usage(usages), outcome=outcome,
                       calls=len(calls))


def sum_usage(usages: list) -> Optional[nemotron.Usage]:
    """Varias llamadas → un total (tokens, latencia y costo se suman)."""
    real = [u for u in usages if u is not None]
    if not real:
        return None
    total = nemotron.Usage(model=real[0].model, reasoning=real[0].reasoning, format_used=real[0].format_used,
                           finish_reason=real[-1].finish_reason)
    for u in real:
        total.prompt_tokens += u.prompt_tokens
        total.completion_tokens += u.completion_tokens
        total.reasoning_tokens_est += u.reasoning_tokens_est
        total.latency_ms += u.latency_ms
    return total


def _from_model(raw, view: BookView) -> tuple[dict, list[str]]:
    """La fila del modelo → la forma de `formula.FormulaRow`: devuelve los nombres a los marcadores y
    traduce las referencias "C1" a ids del historial. Campos desconocidos se dejan pasar para que el
    validador los RECHACE (no se borran en silencio)."""
    if not isinstance(raw, dict):
        return {}, ["not_an_object"]
    row = dict(raw)
    refs = row.pop("from_corrections", []) or []
    if not isinstance(refs, list) or any(r not in view.correction_ids for r in refs):
        return {}, ["unknown_correction"]
    row["from_changes"] = sorted({i for r in refs for i in view.correction_ids[r]})
    for k in ("keyword", "subcategory", "explanation"):
        if isinstance(row.get(k), str):
            row[k] = view.names.unmask(row[k])
    # Un marcador que no corresponde a nadie (el modelo lo inventó o lo deformó) no se adivina.
    if any(isinstance(row.get(k), str) and _UNRESOLVED.search(row[k]) for k in ("keyword", "subcategory")):
        return {}, ["unresolved_person"]
    for k in ("valid_from", "valid_to", "subcategory", "priority"):
        if row.get(k) in ("", "null"):
            row[k] = None
    return row, []


def web_evidence(view: BookView, row: formula.FormulaRow) -> list[dict]:
    """La evidencia del ② para una fila: la verificación de los comercios que la fila caza. La arma el
    CÓDIGO (no el modelo la cita), así lo que ve el bookkeeper es exactamente lo que se encontró."""
    rule = formula.to_rule(row, view.company.id)
    seen, out = set(), []
    for t in view.ctx.txs:
        hit = view.web.get(t.id)
        if hit is None or hit.id in seen:
            continue
        if formula.match_rule([rule], formula.matchable_text(t.description, t.description_extra), t.date, t.amount):
            seen.add(hit.id)
            out.append({"query": hit.query, "business_type": hit.business_type, "confidence": hit.confidence,
                        "source_url": hit.source_url, "source_domain": hit.source_domain})
    return out


def proposal_dict(p: Proposal) -> dict:
    r = p.row
    return {"keyword": r.keyword.upper(), "valid_from": r.valid_from.isoformat() if r.valid_from else None,
            "valid_to": r.valid_to.isoformat() if r.valid_to else None, "priority": r.priority,
            "amount_min": float(r.amount_min) if r.amount_min is not None else None,
            "amount_max": float(r.amount_max) if r.amount_max is not None else None,
            "account_type": r.account_type.value, "category": r.category, "subcategory": r.subcategory,
            "explanation": r.explanation, "from_notes": r.from_notes, "from_changes": r.from_changes,
            "matches": p.matches, "test": p.test, "web_evidence": p.web, "warnings": warnings(p),
            "notes_used": p.notes, "corrections_used": len(p.row.from_changes)}


def warnings(p: Proposal) -> list[str]:
    """Avisos que calcula el CÓDIGO sobre una fila válida (no la rechazan: la decisión es del bookkeeper).
    `web_says_personal`: internet dice que el comercio es de uso personal (un gimnasio) y la fila lo
    manda a un gasto del negocio (✅ 8-oct: el ④ propuso HOUR FITNESS → Office Expense siguiendo las
    correcciones, que eran justo el gasto personal camuflado)."""
    out = []
    personal = [e for e in p.web if e["business_type"] in merchant_verifier.PERSONAL_TYPES
                and e["confidence"] in ("high", "medium")]
    if personal and p.row.account_type.value != "NON_PL":
        out.append("web_says_personal")
    return out


# ── ① EL REVISOR ─────────────────────────────────────────────────────────────────────────────────

class Verdict(BaseModel):
    ref: str
    verdict: Literal["problem", "legitimate", "unsure"]
    observation: str
    hint_for_drafter: str = ""


class ReviewOut(BaseModel):
    reviews: list[Verdict]


_REVIEW_SYSTEM = """You are the REVIEWER of a bookkeeping firm. A deterministic auditor flagged possible problems
in a client book. For each finding, judge whether it is a real PROBLEM, LEGITIMATE (e.g. Home Depot can be
both Materials and Office for good reasons; rows the bookkeeper set by hand are deliberate exceptions),
or UNSURE. You never change any category: you only write an observation for the bookkeeper.

Rules:
- Output ONLY JSON: {"reviews": [{"ref", "verdict", "observation", "hint_for_drafter"}]}, one per finding.
- Judge ONLY from the "facts" given for each finding; never assume facts that are not there. A note
  applies to a finding only if its id is in "note_periods_covering_these_dates" (for dated notes).
- rule_deviation where the row was "decided by rule": the row is stale against its own rule → PROBLEM.
- rule_collision: two rules fight over the same transactions → PROBLEM (the bookkeeper should decide
  which one wins or change one).
- merchant_type_mismatch: a merchant verified on the internet as personal use (e.g. a gym) booked as a
  business expense → PROBLEM unless a note explains it (e.g. a gym that is the client's business).
- inconsistent_merchant: legitimate if the merchant really sells different things (a hardware store) or
  if the rows are different destinations; if the bookkeeper split it by hand, say it is deliberate but
  suggest confirming the criterion.
- Use the bookkeeper notes: "keep_in_mind" notes tell you what to watch; "dont_report" notes mean the
  bookkeeper does not want that reported (mark it legitimate).
- Rows decided "manual" were set by the bookkeeper on purpose ("hand-written wins"). Disagreement among
  them is a reason to ask, not an error by itself.
- observation: WRITE IT IN {lang} (even though these instructions are in English), with no English
  words mixed in (no "mismatch", no "bookkeeper"). ONE short sentence (max ~20 words), concrete, speaking
  directly to the reader. No amounts (you don't have them), no internal refs (F1), no field names like
  NON_PL or OPERATING_EXPENSE.
- hint_for_drafter: a short note in English for the formula drafter if a new rule would fix it, else "".
- Placeholders like "PERSON 1" stand for private people: keep them as written."""


def _notes_covering(view: BookView, txs: list[Transaction]) -> list[int]:
    """Ids de las notas cuyo periodo INCLUYE alguna de estas fechas (lo calcula el código: el modelo
    inventaba que un gasto caía en las vacaciones, ✅ 7-oct)."""
    out = []
    for n in view.notes:
        if not (n["valid_from"] or n["valid_to"]):
            continue
        lo = date.fromisoformat(n["valid_from"]) if n["valid_from"] else date.min
        hi = date.fromisoformat(n["valid_to"]) if n["valid_to"] else date.max
        if any(lo <= t.date <= hi for t in txs):
            out.append(n["id"])
    return out


def review_input(session: Session, company: Company, view: BookView,
                 findings: Optional[list] = None) -> tuple[list[dict], int, dict]:
    """Los hallazgos ABIERTOS (tabla `finding`, `book_review.sync`) en forma segura para el modelo:
    huella, categorías, quién decidió cada fila y conteos; nunca descripciones ni montos. Los HECHOS
    los calcula el código, para que el modelo juzgue sin adivinar. Las observaciones del auditor
    (nómina, datos que faltan) no se juzgan. Lo que una nota 🚫 silencia por comercio se quita AQUÍ,
    en código. Devuelve (hallazgos, silenciados, ref → Finding)."""
    import book_review
    if findings is None:
        findings = book_review.sync(session, company)
    by_id = {t.id: t for t in view.ctx.txs}
    silencers = [n["merchant"] for n in view.notes if n["kind"] == "dont_report" and n["merchant"]]
    out, silenced, ids_of = [], 0, {}
    for f in findings:
        if f.type in book_review.INFO_TYPES:
            continue
        group = [by_id[i] for i in (f.transaction_ids or []) if i in by_id]
        if not group:
            continue
        p = f.payload or {}
        phrase = _huella(group[0], view.names)[1] or ""
        if f.type == "rule_collision":
            phrase = view.names.mask(p.get("losing_rule") or phrase)
        if any(view.names.mask(x.upper()) in phrase.upper() for x in silencers):
            silenced += 1
            continue
        ref = f"F{len(out) + 1}"
        ids_of[ref] = f
        facts: dict = {"rows": len(group),
                       "by_category_and_decider": dict(Counter(f"{_label(t)} decided by {t.source or 'pending'}"
                                                               for t in group))}
        if f.type == "rule_deviation":
            facts["rule_says"] = f"{view.names.mask(p.get('rule') or '')} -> {p.get('rule_says')}"
            facts["row_has"] = p.get("row_has")
            facts["meaning"] = "the row claims to follow the rule but no longer matches it (stale row)"
        elif f.type == "rule_collision":
            facts = {"winning_rule": f"{view.names.mask(p.get('winning_rule') or '')} -> {p.get('winning_then')}",
                     "losing_rule": f"{view.names.mask(p.get('losing_rule') or '')} -> {p.get('losing_then')}",
                     "rows_both_match": p.get("rows"),
                     "meaning": "both rules match the same transactions with different categories; the winner takes them"}
        elif f.type == "merchant_type_mismatch":
            facts["meaning"] = ("the internet says this merchant is a business for PERSONAL use, but the book "
                                "has it as a business expense")
        hit = view.web.get(group[0].id)
        if hit is not None and hit.confidence in ("high", "medium"):
            facts["web_business_type"] = f"{hit.business_type} ({hit.confidence} confidence, verified on the internet)"
        out.append({"ref": ref, "type": f.type, "merchant": phrase, "facts": facts,
                    "note_periods_covering_these_dates": _notes_covering(view, group)})
        if len(out) >= MAX_FINDINGS:
            break
    return out, silenced, ids_of


@dataclass
class ReviewResult:
    reviews: list[dict]
    silenced: int
    usage: Optional[nemotron.Usage]
    outcome: str = "ok"
    reviewed: int = 0


def review(session: Session, company: Company, *, lang: str = "es", reasoning: bool = False,
           user_id: Optional[int] = None, model: Optional[str] = None, force: bool = False) -> ReviewResult:
    """① — UNA llamada a Nemotron sobre los hallazgos abiertos que todavía no tienen veredicto (con
    `force`, sobre todos). El veredicto y la observación quedan GUARDADOS en el hallazgo: la pestaña
    muestra una sola tarjeta por hallazgo. Sin nada que juzgar, no llama."""
    import book_review
    open_findings = book_review.sync(session, company)
    view = build_view(session, company)
    # Sin veredicto, o con un veredicto guardado en OTRO idioma (se rehace en el de la pantalla, 13-oct).
    todo = open_findings if force else [f for f in open_findings
                                        if f.agent_verdict is None or book_review.observation_stale(f, lang)]
    findings, silenced, ids_of = review_input(session, company, view, todo)
    if not findings:
        session.commit()
        return ReviewResult(reviews=[], silenced=silenced, usage=None, outcome="nothing_to_review")
    agent_calls.check_cap(session, company)
    system = _REVIEW_SYSTEM.replace("{lang}", LANG_NAMES.get(lang, "Spanish"))
    user = f"""Bookkeeper notes:
{json.dumps(view.notes, ensure_ascii=False)}

Findings to review:
{json.dumps(findings, ensure_ascii=False)}"""
    try:
        reply = nemotron.chat_json(system, user, ReviewOut, model=model, reasoning=reasoning, max_tokens=3000)
    except nemotron.NemotronError as e:
        agent_calls.record(session, company, "reviewer", e.usage, outcome=e.code, user_id=user_id,
                           model=model or nemotron.model_name())
        session.commit()
        return ReviewResult(reviews=[], silenced=silenced, usage=e.usage, outcome=e.code)
    verdicts = {}
    for v in (reply.data.get("reviews") if isinstance(reply.data, dict) else []) or []:
        try:
            ok = Verdict.model_validate(v)
        except Exception:
            continue
        if ok.ref in ids_of:
            verdicts[ok.ref] = ok
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    reviews = []
    for item in findings:
        f, v = ids_of[item["ref"]], verdicts.get(item["ref"])
        if v is not None:
            f.agent_verdict = v.verdict
            f.agent_observation = view.names.unmask(v.observation)[:400]
            # El idioma que se guarda es el que TIENE el texto (comprobado), no el que se pidió: si el modelo
            # contestó en otro idioma, queda marcado viejo y se rehace la próxima vez.
            f.agent_lang = book_review.text_lang(f.agent_observation) or lang
            f.agent_reviewed_at = now
            session.add(f)
        reviews.append({"ref": item["ref"], "finding_id": f.id, "type": f.type, "merchant": f.merchant,
                        "transaction_ids": f.transaction_ids, "verdict": v.verdict if v else "unsure",
                        "observation": f.agent_observation or "", "answered": v is not None})
    agent_calls.record(session, company, "reviewer", reply.usage, user_id=user_id,
                       items_out=len(findings), items_valid=len(verdicts))
    session.commit()
    return ReviewResult(reviews=reviews, silenced=silenced, usage=reply.usage, reviewed=len(verdicts))


# ── Privacidad, medida (no supuesta) ─────────────────────────────────────────────────────────────

def privacy_scan(sent: list[dict], txs: list[Transaction], persons: set[str]) -> dict:
    """¿Viajó a Nebius algo prohibido? Busca en los mensajes ENVIADOS: descripciones completas del
    banco, montos del libro (con centavos) y nombres de persona. Devuelve los conteos (deben ser 0)."""
    blob = "\n".join(m["content"] for s in sent for m in s["messages"]).upper()
    descs = {(t.description or "").upper() for t in txs if t.description and len(t.description) >= 12}
    amounts = {f"{abs(t.amount):.2f}" for t in txs if t.amount is not None and abs(t.amount) >= 1}
    return {
        "descriptions": sum(1 for d in descs if d in blob),
        "amounts": sum(1 for a in amounts if re.search(rf"(?<![\d.]){re.escape(a)}(?!\d)", blob)),
        "person_names": sum(1 for p in persons if re.search(rf"\b{re.escape(p.upper())}\b", blob)),
    }
