"""
ContaBu — EL ② VERIFICADOR DE COMERCIOS (FASE 11, Auditoría / IA · 8-oct-2026), con Tavily.

Busca en internet qué negocio es cada comercio del libro y devuelve un DATO ESTRUCTURADO: el tipo de
negocio (de una lista cerrada), la confianza y el enlace a la fuente. Es EVIDENCIA para las filas que
propone el ④ y para el ① revisor ("Tavily: gimnasio · enlace"). **No crea reglas ni cambia
categorías.**

🔒 LAS GARANTÍAS (Jose, 1-oct y 8-oct), en código, no en un prompt:
1. **Nunca buscar personas.** Antes de llamar, un filtro determinista saca: pagos a personas (Zelle y
   similares, `company_profile.p2p_counterparty`), cheques, cualquier huella que contenga un nombre de
   persona del libro, transferencias, cargos del propio banco y lo que entra (clientes y depósitos).
   La consulta la arma ESTE código con la huella del comercio: sin montos, sin nombres, sin la
   descripción completa.
2. **Una búsqueda por comercio, guardada.** Caché `MerchantLookup` por huella y POR OFICINA (nada cruza
   entre oficinas). Antes de buscar, se consulta la caché.
3. **Lo que sale de internet es un dato, no un texto.** El tipo de negocio lo decide una tabla de
   palabras de este archivo sobre los títulos y fragmentos de los resultados. Ni ese texto ni el de
   la página entran al prompt de ningún agente: el modelo ve solo `business_type` y `confidence`
   (protección contra inyección de instrucciones). Determinista y gratis: sin otra llamada al modelo.

Tavily por `httpx` (sin SDK), clave `TAVILY_API_KEY` en `backend/.env`. Búsqueda `basic` = 1 crédito
(✅ documentación oficial, 8-oct). Cada búsqueda deja su fila en `agentcall` y cuenta contra el tope
diario de búsquedas de la oficina (`AGENT_DAILY_SEARCHES`).

🔄 REDISEÑADO PARA GASTAR POCO (Jose, 14-oct; tablero → *Decisiones*):
- **Un comercio a pedido** (`search_one`): "Buscar este comercio" desde un hallazgo o desde el panel de
  la transacción. Primero la memoria de la oficina; si no está, se busca al instante.
- **En la revisión, solo lo que la auditoría marcó** (`verify_flagged`): los comercios de los hallazgos
  abiertos (lo que vio el código, una nota o "¿personal?", y lo que el ① llamó problema o inseguro; no lo
  que llamó legítimo, ni las observaciones). Ya no todos los comercios del libro.
- **Primero "basic" (1 crédito); si la confianza no sale alta, "advanced" (2 créditos) UNA vez**, y se
  queda la de la avanzada (16-oct; hasta entonces, la mejor de las dos, y en un empate ganaba la básica). Se guarda cuál se usó (`MerchantLookup.search_depth`).
- Las garantías de arriba no cambian: el filtro de personas corre igual antes de cada búsqueda.
"""

import os
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
from sqlmodel import Session, select

import agent_calls
from company_profile import p2p_counterparty
from models import Company, MerchantLookup, Transaction
from rules import _MERCHANT_NOISE, _PROCESSOR_PREFIXES, is_check, merchant_signature, suggest_keyword

load_dotenv()

TAVILY_URL = "https://api.tavily.com/search"
MIN_RELEVANCE = 0.2   # por debajo, un resultado de Tavily no cuenta como evidencia
MAX_SEARCHES_PER_RUN = 12

# ── La lista cerrada de tipos de negocio y sus palabras ─────────────────────────────────────────
# Las palabras se buscan como palabras completas en el título y el fragmento de cada resultado. El
# orden no importa; gana el tipo con más evidencia ponderada por la relevancia de Tavily.
BUSINESS_TYPES: dict[str, tuple[str, ...]] = {
    "restaurant": ("restaurant", "taqueria", "tacos", "taco", "mexican food", "burrito", "diner", "eatery",
                   "grill", "pizzeria", "food truck"),
    "coffee_shop": ("coffee", "coffeehouse", "espresso", "cafe", "café"),
    "grocery": ("supermarket", "grocery", "groceries", "grocery store"),
    "warehouse_club": ("warehouse club", "wholesale club", "membership warehouse", "costco", "sam's club"),
    "restaurant_supply": ("restaurant supply", "foodservice", "food service distributor", "restaurant depot"),
    "gas_station": ("gas station", "fuel", "gasoline", "petrol", "service station", "service stations",
                    "petroleum", "oil and gas", "oil company", "motorist", "motorists"),
    "hardware_store": ("hardware", "home improvement", "lumber"),
    "building_supplies": ("hvac supply", "hvac", "plumbing supply", "building materials", "contractor supply",
                          "electrical supply"),
    "cleaning_supplies": ("janitorial", "cleaning supplies", "cleaning products", "sanitation supplies",
                          "jan-san", "wipes", "disinfectant", "disinfecting", "sanitizer", "anti-bacterial",
                          "antibacterial"),
    # Sin "tools" suelto: cazaba "website tools" (✅ 8-oct: SQUARESPACE salió industrial con confianza alta).
    "industrial_supplies": ("industrial supplies", "industrial supply", "mro", "power tools", "hand tools",
                            "tool supplier", "maintenance supplies"),
    "packaging_shipping": ("packaging", "shipping supplies", "boxes", "shipping and packaging"),
    "uniforms_apparel": ("uniform", "uniforms", "workwear", "scrubs", "apparel"),
    "linen_laundry": ("linen", "laundry", "uniform rental"),
    "gym_fitness": ("gym", "gyms", "fitness", "health club", "workout", "fitness center", "fitness club"),
    "insurance": ("insurance", "insurer", "insurance company"),
    "telecom": ("wireless", "mobile carrier", "phone service", "cellular", "telecommunications", "mobile network"),
    "utility": ("utility", "utilities", "electric company", "gas and electric", "power company", "water district"),
    "pest_control": ("pest control", "exterminator", "pest management"),
    "payroll_hr": ("payroll", "human resources", "hr software"),
    "software_saas": ("software", "saas", "cloud-based", "website builder", "web hosting", "domain names",
                      "e-commerce platform", "website platform"),
    "convenience_store": ("convenience store", "convenience stores"),
    "tolls_parking": ("toll", "tolls", "express lanes", "toll road", "parking"),
    "food_delivery": ("food delivery", "delivery platform", "delivery app"),
    "payment_processor": ("payment processing", "point of sale", "payments company"),
    "property_management": ("property management", "homeowners association", "hoa management"),
    "bank_financial": ("bank", "credit union", "financial services"),
    # 13-oct: los comercios de gasto personal del examen del demo no tenían tipo (salió de mirar el examen
    # por TIPOS de comercio, no por filas): streaming y cine, tienda por departamentos, electrónica.
    "streaming_entertainment": ("streaming service", "video streaming", "streaming platform", "movie theater",
                                "movie theatre", "cinema", "cinemas"),
    "department_store": ("department store", "department stores", "discount retailer", "general merchandise",
                         "off-price"),
    "consumer_electronics": ("consumer electronics", "electronics retailer", "electronics store"),
}
# Tipos que en un libro de NEGOCIO suelen ser gasto PERSONAL del dueño (para el ① revisor). Tienda por
# departamentos y electrónica NO: pueden ser material de oficina o equipo (que lo juzgue el bookkeeper).
PERSONAL_TYPES = {"gym_fitness", "streaming_entertainment"}

_TYPE_RE = {t: re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.IGNORECASE)
            for t, words in BUSINESS_TYPES.items()}
_P2P_WORDS = re.compile(r"\b(ZELLE|VENMO|CASH ?APP|PAYPAL TRANSFER|QUICKPAY|WIRE|REMITLY|WESTERN UNION)\b")
# El titular de una línea ACH ("… IND NAME:JUAN PEREZ TRN: …") puede ser una persona: se trata como
# nombre protegido (✅ 8-oct, visto en un libro real). Y un pago de TARJETA no es un comercio.
_ACH_NAME_RE = re.compile(r"IND NAME:\s*([A-Z][A-Z .'&-]{2,}?)(?:\s{2,}|\s+TRN\b|\s+[A-Z]{2,}\d|$)")
_CARD_PAYMENT_RE = re.compile(r"\b(CARD ONLINE|CREDIT CARD|CARD PAYMENT|CARDMEMBER|CRD PMT|AUTOPAY|EPAY|EPAYMENT)\b")
_NOT_MERCHANT = {"TRANSFER", "DEPOSIT", "DEP", "FEE", "FEES", "MONTHLY", "SERVICE", "INTEREST", "WITHDRAWAL",
                 "ATM", "OVERDRAFT", "CHECK", "PAYROLL"}


class TavilyUnavailable(RuntimeError):
    """No hay `TAVILY_API_KEY`: el verificador no está disponible (el resto de la app sí)."""


@dataclass
class Candidate:
    fingerprint: str      # la llave de la caché: la frase que se busca, en mayúsculas
    keyword: str          # la huella visible (la que ven los agentes)
    query: str            # lo que se mandaría a Tavily
    rows: int
    skip: Optional[str]   # None = se puede buscar; si no, la razón (person, check, transfer, …)


# ── 1. Qué se puede buscar (el filtro, ANTES de cualquier llamada) ───────────────────────────────

def _query_phrase(description: str) -> str:
    """La frase que se busca: el comercio tal como empieza la línea del banco, sin prefijo de procesador
    (SQ*, TST*, PAYPAL*), sin número de tienda, sin ciudad/estado. Conserva un número pegado ADELANTE
    del nombre ("24 HOUR FITNESS"), que `suggest_keyword` quita."""
    kw = suggest_keyword(description) or ""
    words = re.sub(r"[^A-Z0-9&' -]+", " ", description.upper().replace("*", " ")).split()
    while words and words[0] in _PROCESSOR_PREFIXES:
        words.pop(0)
    kw_words = kw.split()
    if kw_words and kw_words[0] in words:
        i = words.index(kw_words[0])
        if i > 0 and words[i - 1].isdigit() and len(words[i - 1]) <= 3:
            return " ".join([words[i - 1]] + kw_words)
    return kw


def protected_names(txs: list[Transaction]) -> set[str]:
    """Todos los nombres que NUNCA salen: contrapartes de pagos a personas y titulares de líneas ACH."""
    names = {p for t in txs if (p := p2p_counterparty(t.description))}
    for t in txs:
        m = _ACH_NAME_RE.search((t.description or "").upper())
        if m and len(m.group(1).strip()) >= 4:
            names.add(re.sub(r"\s+", " ", m.group(1)).strip())
    return names


def query_for(description: str, full_descriptions: set[str]) -> str:
    """La consulta para UNA descripción: la frase del comercio, nunca la descripción completa del banco
    (si coinciden, se le quitan palabras del final, dejando al menos dos). Es también la llave de la
    caché, así los agentes encuentran la verificación de cada movimiento."""
    qwords = _query_phrase(description or "").split()
    while len(qwords) > 2 and " ".join(qwords).upper() in full_descriptions:
        qwords.pop()
    return " ".join(qwords)


def candidates(txs: list[Transaction]) -> list[Candidate]:
    """Los comercios del libro con la decisión de si se pueden buscar. Lo que entra (clientes,
    depósitos) no se busca: el verificador mira gastos."""
    persons = protected_names(txs)
    person_re = (re.compile(r"\b(" + "|".join(re.escape(p) for p in sorted(persons, key=len, reverse=True)) + r")\b")
                 if persons else None)
    # Se agrupa por la FRASE QUE SE BUSCARÍA, no por la huella: "STARBUCKS 1123 PARIS FR" y
    # "STARBUCKS 2210 MADRID ES" tienen huellas distintas pero son UNA búsqueda (✅ 8-oct: Rivera iba a
    # buscar Starbucks 6 veces). Un cheque no tiene comercio: va solo.
    full = {(t.description or "").strip().upper() for t in txs}
    groups: dict[str, list[Transaction]] = {}
    for t in txs:
        key = f"__check_{t.id}" if is_check(t.description) else (query_for(t.description, full).upper()
                                                                  or f"__empty_{t.id}")
        groups.setdefault(key, []).append(t)
    out = []
    for sig, group in groups.items():
        sample = Counter(t.description for t in group).most_common(1)[0][0] or ""
        up = sample.upper()
        query = query_for(sample, full)
        keyword = suggest_keyword(sample) or query
        skip = None
        if sig.startswith("__check_"):
            skip = "check"
        elif sig.startswith("__empty_"):
            skip = "not_a_merchant"
        elif any(p2p_counterparty(t.description) for t in group) or _P2P_WORDS.search(up):
            skip = "person"                     # pago a persona: jamás se busca
        elif person_re and person_re.search(query.upper()):
            # La CONSULTA contiene un nombre protegido. (Se mira la consulta, no la línea entera: el
            # titular de una cuenta aparece en todas sus líneas ACH, también en las de una aseguradora.)
            skip = "person"
        elif sum(1 for t in group if t.is_inflow) > len(group) / 2:
            skip = "inflow"                     # clientes y depósitos: no son gastos
        elif "TRANSFER" in up or "XFER" in up or _CARD_PAYMENT_RE.search(up):
            skip = "transfer"                   # transferencias y pagos de tarjeta: no son comercios
        else:
            words = [w for w in re.split(r"[^A-Z0-9&']+", query.upper()) if w]
            meaningful = [w for w in words if any(c.isalpha() for c in w)
                          and w not in _MERCHANT_NOISE and w not in _NOT_MERCHANT]
            if not meaningful or len(query) < 3:
                skip = "not_a_merchant"         # cargos del banco, ruido
        out.append(Candidate(fingerprint=sig, keyword=keyword, query=query, rows=len(group), skip=skip))
    out.sort(key=lambda c: -c.rows)
    return out


# ── 2. La búsqueda (Tavily) y la clasificación (determinista) ───────────────────────────────────

def classify(results: list[dict], query: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Resultados de Tavily → (tipo de negocio, confianza, enlace). Solo cuenta palabras de la lista
    cerrada en título y fragmento, ponderadas por la relevancia (`score`). El texto no sale de aquí."""
    scores: Counter = Counter()
    support: Counter = Counter()           # en cuántos resultados DISTINTOS aparece cada tipo
    best_url: dict[str, tuple[float, str]] = {}
    name_tokens = [w.lower() for w in query.split() if len(w) >= 3 and not w.isdigit()]
    name_seen = False
    # Los resultados casi irrelevantes no son evidencia: 4 páginas genéricas sobre "restaurant" (relevancia
    # 0.05–0.11) le ganaban a la página oficial de RESTAURANT DEPOT (0.68, "Foodservice") — ✅ 13-oct.
    relevant = [r for r in results if float(r.get("score") or 0) >= MIN_RELEVANCE] or results[:1]
    for r in relevant:
        text = f"{r.get('title') or ''} {r.get('content') or ''}"
        rel = float(r.get("score") or 0.5)
        if name_tokens and all(tok in (text + " " + (r.get("url") or "")).lower() for tok in name_tokens[:2]):
            name_seen = True
        for t, rx in _TYPE_RE.items():
            found = rx.findall(text)
            # Una FRASE de varias palabras ("restaurant depot", "gas station") dice más que una palabra
            # suelta: pesa el doble (✅ 13-oct: RESTAURANT DEPOT salía "restaurant" por la palabra suelta).
            hits = sum(2 if " " in w.strip() else 1 for w in found)
            if hits:
                scores[t] += rel * min(hits, 4)
                support[t] += 1
                if rel > best_url.get(t, (-1, ""))[0]:
                    best_url[t] = (rel, r.get("url") or "")
    if not scores:
        return None, None, None
    (top, top_s), *rest = scores.most_common()
    share = top_s / sum(scores.values())
    second = rest[0][1] if rest else 0.0
    # Confianza alta exige evidencia en 2+ resultados: con UNO solo, una palabra suelta decidía (✅ 8-oct:
    # SHELL OIL salió "packaging" con confianza alta por un único fragmento).
    if not name_seen or support[top] < 2:
        conf = "low"          # los resultados no hablan de ESTE comercio, o la evidencia es de 1 resultado
    elif share >= 0.5 and top_s >= 2 * max(second, 0.01):
        conf = "high"
    elif share >= 0.35:
        conf = "medium"
    else:
        conf = "low"
    return top, conf, best_url.get(top, (0, None))[1]


def _search(query: str, depth: str = "basic") -> tuple[list[dict], int, int]:
    """Una búsqueda en Tavily (`basic` = 1 crédito, `advanced` = 2) → (resultados, créditos, latencia ms).
    Sin respuesta generada (`include_answer=False`) ni contenido completo de la página: solo título,
    enlace y fragmento."""
    key = os.getenv("TAVILY_API_KEY")
    if not key:
        raise TavilyUnavailable("TAVILY_API_KEY no está configurada en backend/.env")
    started = time.monotonic()
    r = httpx.post(TAVILY_URL, headers={"Authorization": f"Bearer {key}"}, timeout=30.0,
                   json={"query": query, "search_depth": depth, "max_results": 5, "include_answer": False,
                         "include_raw_content": False, "topic": "general", "include_usage": True})
    r.raise_for_status()
    body = r.json()
    credits = int((body.get("usage") or {}).get("credits") or (2 if depth == "advanced" else 1))
    return body.get("results") or [], credits, int((time.monotonic() - started) * 1000)


def _scope(q, company: Company):
    """La caché es POR OFICINA (nada cruza entre oficinas); un libro sin oficina, por libro."""
    if company.firm_id:
        return q.where(MerchantLookup.firm_id == company.firm_id)
    return q.where(MerchantLookup.firm_id.is_(None), MerchantLookup.company_id == company.id)


def cached(session: Session, company: Company) -> dict[str, MerchantLookup]:
    rows = session.exec(_scope(select(MerchantLookup), company)).all()
    return {r.fingerprint: r for r in rows}


SENT_QUERIES: list[str] = []   # las consultas enviadas (para la prueba de privacidad)


@dataclass
class VerifyResult:
    items: list[dict]
    searched: int
    from_cache: int
    skipped: Counter
    credits: int
    outcome: str = "ok"
    finding_ids: list = field(default_factory=list)   # hallazgos con evidencia nueva (ida y vuelta, 14-oct)


_RANK = {"high": 3, "medium": 2, "low": 1, None: 0}
# Medición sin gastar créditos (`measure.py`): fingerprint → la verificación guardada del demo. Hace de
# Tavily: la "básica" responde lo guardado y, si no es de confianza alta, la "avanzada" cuesta igual sus
# 2 créditos y responde lo mismo (no hay con qué simularla mejor). None = Tavily de verdad.
ORACLE: Optional[dict[str, dict]] = None


@dataclass
class Looked:
    hit: MerchantLookup
    searched: bool = False
    credits: int = 0
    outcome: str = "ok"


def _lookup(session: Session, company: Company, c: Candidate, cache: dict, *, user_id: Optional[int] = None,
            record: bool = True) -> Optional[Looked]:
    """UN comercio: la memoria de la oficina primero; si no está, "basic" y, si la confianza no sale alta,
    "advanced" una sola vez (se queda la mejor). None = se acabó el tope de búsquedas del día."""
    hit = cache.get(c.fingerprint)
    if hit is not None:
        return Looked(hit=hit)
    if record:
        try:
            agent_calls.check_cap(session, company, agent="verifier")
        except agent_calls.CapReached:
            return None
    best = (None, None, None)
    status, total, depth, n_results, outcome = "not_found", 0, "basic", 0, "ok"
    for level in ("basic", "advanced"):
        if level == "advanced":
            if _RANK[best[1]] >= _RANK["high"] or status == "error":
                break
            depth = "basic+advanced"
        SENT_QUERIES.append(c.query)
        try:
            if ORACLE is not None:
                row = ORACLE.get(c.fingerprint) or {}
                results, used, latency = ([{}] * int(row.get("results_count") or 0)), (1 if level == "basic" else 2), 0
                got = (row.get("business_type"), row.get("confidence"), row.get("source_url"))
            else:
                results, used, latency = _search(c.query, level)
                got = classify(results, c.query)
            if results:
                status = "ok"
            n_results = max(n_results, len(results))
            # La básica vale solo si salió ALTA; si no, se busca la avanzada y SE QUEDA LA AVANZADA, aunque empate
            # o baje (✅ 16-oct, web_matrix.jsonl: una "media" de la básica acertó 2 de 8 y sobrevivía en los
            # empates; quedándose con la avanzada, 56/68 correctos contra 52, y 8 evidencias equivocadas contra 12,
            # al mismo costo). Decisión de Jose del 16-oct.
            best = got
        except httpx.HTTPError as e:
            used, latency, status, outcome = 0, 0, "error", f"search_error:{type(e).__name__}"
        total += used
        if record:
            agent_calls.record(session, company, "verifier", None, model=f"tavily:{level}",
                               outcome="ok" if status != "error" else "error", user_id=user_id,
                               items_out=n_results, items_valid=int(best[0] is not None), credits=used,
                               latency_ms=latency)
    btype, conf, url = best
    hit = MerchantLookup(firm_id=company.firm_id, company_id=None if company.firm_id else company.id,
                         fingerprint=c.fingerprint, query=c.query, status=status, business_type=btype,
                         confidence=conf, source_url=url, source_domain=urlparse(url).netloc if url else None,
                         results_count=n_results, credits=total, search_depth=depth)
    session.add(hit)
    cache[c.fingerprint] = hit
    return Looked(hit=hit, searched=True, credits=total, outcome=outcome)


def _run(session: Session, company: Company, chosen: list[Candidate], *, user_id: Optional[int], limit: int,
         record: bool, skipped: Optional[Counter] = None) -> VerifyResult:
    cache = cached(session, company)
    items, skipped = [], skipped if skipped is not None else Counter()
    searched = from_cache = credits = 0
    outcome = "ok"
    for c in chosen:
        if c.skip:
            skipped[c.skip] += 1
            continue
        if c.fingerprint not in cache and searched >= limit:
            skipped["limit"] += 1
            continue
        got = _lookup(session, company, c, cache, user_id=user_id, record=record)
        if got is None:
            outcome = "agent_cap_reached"
            break
        searched += int(got.searched)
        from_cache += int(not got.searched)
        credits += got.credits
        if got.outcome != "ok":
            outcome = got.outcome
        items.append(lookup_dict(got.hit, c.keyword, c.rows))
    if record:
        session.commit()
    else:
        session.flush()
    return VerifyResult(items=items, searched=searched, from_cache=from_cache, skipped=skipped,
                        credits=credits, outcome=outcome)


def verify(session: Session, company: Company, *, user_id: Optional[int] = None,
           limit: int = MAX_SEARCHES_PER_RUN, record: bool = True) -> VerifyResult:
    """TODOS los comercios buscables del libro (lo que ya está en la memoria de la oficina no se busca).
    Desde el 14-oct la pantalla ya no lo usa: lo usa `demo_web_evidence.py` para guardar las del demo."""
    txs = session.exec(select(Transaction).where(Transaction.company_id == company.id)).all()
    return _run(session, company, candidates(txs), user_id=user_id, limit=limit, record=record)


# Hallazgos donde saber QUÉ NEGOCIO es el comercio puede cambiar la decisión: lo que puede ser PERSONAL (lo
# marcó "¿personal?", una nota, o internet ya lo dijo). Fuera, a propósito (✅ 14-oct, medido con el primer
# examen y el oráculo): una fila desalineada de su regla, el signo contra la sección y dos reglas que
# chocan son problemas MECÁNICOS; y "mismo comercio, dos categorías" encontró lo mismo (30/32) con 62
# créditos en vez de 40. Internet no decide ninguno de esos. ACEPTADO por Jose el 15-oct.
SEARCH_TYPES = {"personal_use", "note_check", "merchant_type_mismatch"}


def flagged_candidates(session: Session, company: Company, txs: list[Transaction]) -> tuple[list[Candidate], dict]:
    """Los comercios que la AUDITORÍA marcó (hallazgos abiertos), y fingerprint → ids de esos hallazgos.
    Entra lo que vio una nota o "¿personal?", un comercio con dos categorías, y lo que el ① llamó problema
    o inseguro. No entra lo que el ① llamó legítimo, las observaciones (nómina, datos que faltan, montos
    inusuales) ni los problemas mecánicos (ver `SEARCH_TYPES`)."""
    import book_review
    findings = [f for f in book_review.sync(session, company)
                if f.type in SEARCH_TYPES and f.agent_verdict != "legitimate"]
    full = {(t.description or "").strip().upper() for t in txs}
    by_id = {t.id: t for t in txs}
    wanted: dict[str, set[int]] = {}
    for f in findings:
        for i in f.transaction_ids or []:
            t = by_id.get(i)
            if t is not None and not is_check(t.description):
                wanted.setdefault(query_for(t.description, full).upper(), set()).add(f.id)
    return [c for c in candidates(txs) if c.fingerprint in wanted], wanted


def verify_flagged(session: Session, company: Company, *, user_id: Optional[int] = None,
                   limit: int = MAX_SEARCHES_PER_RUN, record: bool = True) -> VerifyResult:
    """En la revisión: busca SOLO los comercios de los hallazgos abiertos (memoria de la oficina primero).
    `finding_ids`: los hallazgos cuyo comercio tiene evidencia NUEVA de esta corrida (para la ida y vuelta:
    el ① los vuelve a juzgar)."""
    txs = session.exec(select(Transaction).where(Transaction.company_id == company.id)).all()
    chosen, _wanted = flagged_candidates(session, company, txs)
    before = set(cached(session, company))
    res = _run(session, company, chosen, user_id=user_id, limit=limit, record=record)
    # "Evidencia nueva" = un tipo de negocio con confianza alta o media (la baja no la usa ningún agente).
    fresh = {i["fingerprint"] for i in res.items if i["fingerprint"] not in before and i.get("business_type")
             and i.get("confidence") in ("high", "medium")}
    res.finding_ids = _findings_for(session, company, txs, fresh)
    return res


def _findings_for(session: Session, company: Company, txs: list[Transaction], fingerprints: set[str]) -> list[int]:
    """Los hallazgos abiertos (DESPUÉS de la búsqueda) que tocan esos comercios. Se miran después porque la
    evidencia nueva puede cambiar el hallazgo: "¿personal?" pasa a "internet dice uso personal" (✅ 14-oct,
    Netflix), y es ESE el que el ① tiene que juzgar."""
    if not fingerprints:
        return []
    import book_review
    import personal_use
    full = {(t.description or "").strip().upper() for t in txs}
    ids = {t.id for t in txs if not is_check(t.description) and query_for(t.description, full).upper() in fingerprints}
    # La ida y vuelta incluye "¿personal?": su opinión sobre esos comercios se hizo SIN el tipo verificado;
    # se olvida y la siguiente revisión la rehace con él (✅ 14-oct: sin esto, una renta o un proveedor de
    # comida quedaban "personal" aunque internet ya dijera qué negocio eran).
    personal_use.forget(session, company, [t for t in txs if t.id in ids])
    return sorted(f.id for f in book_review.sync(session, company)
                  if set(f.transaction_ids or []) & ids and f.type not in book_review.INFO_TYPES)


def search_one(session: Session, company: Company, transaction_id: int, *, user_id: Optional[int] = None,
               record: bool = True) -> VerifyResult:
    """"Buscar este comercio": el comercio de UN movimiento, al instante. Memoria de la oficina primero. Un
    pago a una persona, un cheque o una transferencia no se buscan nunca (`skipped` dice por qué)."""
    txs = session.exec(select(Transaction).where(Transaction.company_id == company.id)).all()
    t = next((x for x in txs if x.id == transaction_id), None)
    if t is None:
        raise LookupError("transaction not found")
    full = {(x.description or "").strip().upper() for x in txs}
    fp = f"__check_{t.id}" if is_check(t.description) else query_for(t.description, full).upper()
    chosen = [c for c in candidates(txs) if c.fingerprint == fp]
    if not chosen:
        return VerifyResult(items=[], searched=0, from_cache=0, skipped=Counter({"not_a_merchant": 1}), credits=0)
    before = set(cached(session, company))
    res = _run(session, company, chosen, user_id=user_id, limit=1, record=record)
    useful = bool(res.items) and res.items[0].get("confidence") in ("high", "medium")
    res.finding_ids = _findings_for(session, company, txs, {fp} if res.searched and useful and fp not in before else set())
    return res


def lookup_dict(m: MerchantLookup, keyword: str, rows: int) -> dict:
    return {"merchant": keyword, "rows": rows, "status": m.status, "business_type": m.business_type,
            "confidence": m.confidence, "source_url": m.source_url, "source_domain": m.source_domain,
            "fingerprint": m.fingerprint, "search_depth": m.search_depth, "credits": m.credits}


def web_by_tx(session: Session, company: Company, txs: list[Transaction]) -> dict[int, MerchantLookup]:
    """Movimiento → la verificación de su comercio (si existe en la caché de la oficina). Para que los
    agentes y la prueba contra la historia encuentren la evidencia sin volver a buscar."""
    cache = cached(session, company)
    if not cache:
        return {}
    full = {(t.description or "").strip().upper() for t in txs}
    out = {}
    for t in txs:
        if is_check(t.description):
            continue
        hit = cache.get(query_for(t.description, full).upper())
        if hit is not None and hit.business_type:
            out[t.id] = hit
    return out
