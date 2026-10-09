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
"""

import os
import re
import time
from collections import Counter
from dataclasses import dataclass
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
}
# Tipos que en un libro de NEGOCIO suelen ser gasto PERSONAL del dueño (para el ① revisor).
PERSONAL_TYPES = {"gym_fitness"}

_TYPE_RE = {t: re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.IGNORECASE)
            for t, words in BUSINESS_TYPES.items()}
_P2P_WORDS = re.compile(r"\b(ZELLE|VENMO|CASH ?APP|PAYPAL TRANSFER|QUICKPAY|WIRE|REMITLY|WESTERN UNION)\b")
# El titular de una línea ACH ("… IND NAME:JUAN PEREZ TRN: …") puede ser una persona: se trata como
# nombre protegido (✅ 8-oct, visto en un libro real). Y un pago de TARJETA no es un comercio.
_ACH_NAME_RE = re.compile(r"IND NAME:\s*([A-Z][A-Z .'&-]{2,}?)(?:\s{2,}|\s+TRN\b|\s+[A-Z]{2,}\d|$)")
_CARD_PAYMENT_RE = re.compile(r"\b(CARD ONLINE|CREDIT CARD|CARD PAYMENT|CARDMEMBER|CRD PMT|AUTOPAY|EPAY)\b")
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
    for r in results:
        text = f"{r.get('title') or ''} {r.get('content') or ''}"
        rel = float(r.get("score") or 0.5)
        if name_tokens and all(tok in (text + " " + (r.get("url") or "")).lower() for tok in name_tokens[:2]):
            name_seen = True
        for t, rx in _TYPE_RE.items():
            hits = len(rx.findall(text))
            if hits:
                scores[t] += rel * min(hits, 3)
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


def _search(query: str) -> tuple[list[dict], int, int]:
    """Una búsqueda `basic` en Tavily → (resultados, créditos, latencia ms). Sin respuesta generada
    (`include_answer=False`) ni contenido completo de la página: solo título, enlace y fragmento."""
    key = os.getenv("TAVILY_API_KEY")
    if not key:
        raise TavilyUnavailable("TAVILY_API_KEY no está configurada en backend/.env")
    started = time.monotonic()
    r = httpx.post(TAVILY_URL, headers={"Authorization": f"Bearer {key}"}, timeout=30.0,
                   json={"query": query, "search_depth": "basic", "max_results": 5, "include_answer": False,
                         "include_raw_content": False, "topic": "general", "include_usage": True})
    r.raise_for_status()
    body = r.json()
    credits = int((body.get("usage") or {}).get("credits") or 1)
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


def verify(session: Session, company: Company, *, user_id: Optional[int] = None,
           limit: int = MAX_SEARCHES_PER_RUN, record: bool = True) -> VerifyResult:
    """Verifica los comercios del libro: lo que está en la caché de la oficina no se vuelve a buscar.
    Devuelve, por comercio, el dato estructurado (y por qué se saltó lo que no se buscó)."""
    txs = session.exec(select(Transaction).where(Transaction.company_id == company.id)).all()
    cache = cached(session, company)
    items, skipped = [], Counter()
    searched = from_cache = credits = 0
    outcome = "ok"
    for c in candidates(txs):
        if c.skip:
            skipped[c.skip] += 1
            continue
        hit = cache.get(c.fingerprint)
        if hit is not None:
            from_cache += 1
        else:
            if searched >= limit:
                skipped["limit"] += 1
                continue
            if record:
                try:
                    agent_calls.check_cap(session, company, agent="verifier")
                except agent_calls.CapReached:
                    outcome = "agent_cap_reached"
                    break
            SENT_QUERIES.append(c.query)
            try:
                results, used, latency = _search(c.query)
                status = "ok" if results else "not_found"
                btype, conf, url = classify(results, c.query)
            except httpx.HTTPError as e:
                results, used, latency, status, btype, conf, url = [], 0, 0, "error", None, None, None
                outcome = f"search_error:{type(e).__name__}"
            hit = MerchantLookup(firm_id=company.firm_id, company_id=None if company.firm_id else company.id,
                                 fingerprint=c.fingerprint, query=c.query, status=status, business_type=btype,
                                 confidence=conf, source_url=url,
                                 source_domain=urlparse(url).netloc if url else None,
                                 results_count=len(results), credits=used)
            session.add(hit)
            cache[c.fingerprint] = hit
            searched += 1
            credits += used
            if record:
                agent_calls.record(session, company, "verifier", None, model="tavily:basic",
                                   outcome="ok" if status != "error" else "error", user_id=user_id,
                                   items_out=len(results), items_valid=int(btype is not None),
                                   credits=used, latency_ms=latency)
        items.append(lookup_dict(hit, c.keyword, c.rows))
    if record:
        session.commit()
    else:
        session.flush()
    return VerifyResult(items=items, searched=searched, from_cache=from_cache, skipped=skipped,
                        credits=credits, outcome=outcome)


def lookup_dict(m: MerchantLookup, keyword: str, rows: int) -> dict:
    return {"merchant": keyword, "rows": rows, "status": m.status, "business_type": m.business_type,
            "confidence": m.confidence, "source_url": m.source_url, "source_domain": m.source_domain,
            "fingerprint": m.fingerprint}


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
