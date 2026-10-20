"""
ContaBu — EL GENERADOR DE DATOS DEL DEMO (FASE 10, área Movimientos · 4-oct-2026). Espina 2 del demo.

Ver `docs/DEMO Y ENTREGA.md` → "Los datos del demo". Firm lo llama desde `demo.seed_visitor_books()`
con la firma acordada en el 📬 Buzón (3-oct):

    seed_demo_books(session, firm, owner) -> list[Company]

El contrato, punto por punto:
1. Los libros nacen DENTRO de la oficina (`firm_id`, `user_id`) y **con `sector`** (sin sector el
   visitante caería en el alta).
2. `session.flush()`, **nunca `commit()`**: el commit lo hace `/api/me`, así oficina y libros nacen
   juntos. Firm lo envuelve en un SAVEPOINT.
3. Toda tabla sembrada cuelga de `company` o de `transaction` (`categoryrule.company_id`,
   `transaction.company_id`, `categorizationchange.company_id/transaction_id`, `companycategory.company_id`
   — el plan de cuentas del libro, 18-oct): la limpieza de 7 días la borra sola.
4. Determinista y rápido: fechas FIJAS (jul–sep 2026) y montos de un `random.Random` con semilla por
   libro; ~600 filas en total, sin IA y sin red.

**Datos sintéticos.** Los negocios son inventados; los comercios son cadenas reales, como en cualquier
extracto. Nada sale del CSV de Chase ni de la base local.

Los movimientos entran por **el mismo motor que un CSV** (`ingestion.ingest_parsed`: regla →
transferencia → Pendiente). Después se plantan, a propósito, las situaciones que cada agente debe
encontrar (lo escrito a mano, el historial de correcciones, la fila que se salió de su regla).
"""

import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Callable, Optional

from sqlmodel import Session, select

import book_notes
import corrections
from categories import seed_chart
import demo_web_evidence
from ingestion import ingest_parsed, make_fingerprint
from models import AccountType, CategoryRule, Company, Firm, RuleScope, Transaction, User

START = date(2026, 7, 1)
END = date(2026, 9, 30)

OPEX, COGS, REVENUE, NON_PL = (AccountType.OPERATING_EXPENSE, AccountType.COGS,
                               AccountType.REVENUE, AccountType.NON_PL)
OTHER_EXPENSE = AccountType.OTHER_EXPENSE

# ── Las NOTAS del bookkeeper ────────────────────────────────────────────────────────────────────
# Se siembran en la tabla `booknote` (F11) con `book_notes.seed_notes`, en `_seed_book`. Por nombre
# de libro. En inglés (los datos siempre en inglés; el idioma es solo la capa visual).
# `kind`: "keep_in_mind" (📌 tenlo en cuenta) | "dont_report" (🚫 no me reportes).
DEMO_NOTES: dict[str, list[dict]] = {
    "Rivera HVAC LLC": [{
        "text": "The owner was on vacation in Europe from Aug 3 to Aug 17, 2026. "
                "Purchases made there are personal, not business.",
        "kind": "keep_in_mind", "priority": "high",
        "valid_from": "2026-08-03", "valid_to": "2026-08-17",
    }],
    "Casa Lupita Taqueria": [{
        "text": "Supermarket purchases over $50 are usually personal: review them.",
        "kind": "keep_in_mind", "priority": "normal",
        "valid_from": None, "valid_to": None,
    }],
}


# ── Calendarios (deterministas) ─────────────────────────────────────────────────────────────────

def _days() -> list[date]:
    return [START + timedelta(days=i) for i in range((END - START).days + 1)]


def weekly(weekday: int) -> list[date]:
    """Cada semana, el día `weekday` (0 = lunes)."""
    return [d for d in _days() if d.weekday() == weekday]


def monthly(day: int) -> list[date]:
    """Un día fijo de cada mes (si cae en fin de semana, el lunes siguiente, como un ACH)."""
    out = []
    for m in (7, 8, 9):
        d = date(2026, m, day)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append(d)
    return out


def biweekly(first: date) -> list[date]:
    out, d = [], first
    while d <= END:
        out.append(d)
        d += timedelta(days=14)
    return out


def spread(n: int, rng: random.Random, start: date = START, end: date = END) -> list[date]:
    """`n` fechas repartidas en el periodo, con un poco de ruido (días hábiles)."""
    span = (end - start).days
    out = []
    for i in range(n):
        d = start + timedelta(days=round(span * (i + 0.5) / n) + rng.randint(-2, 2))
        d = min(max(d, start), end)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        out.append(max(d, start))
    return out


# ── Un libro ────────────────────────────────────────────────────────────────────────────────────

class Book:
    """Arma las filas de un libro: (fecha, descripción, monto CON signo, etiqueta de plantado)."""

    def __init__(self, key: str):
        self.rng = random.Random(f"contabu-demo-{key}")  # semilla por libro → siempre lo mismo
        self.rows: list[tuple[date, str, Decimal, Optional[str]]] = []

    def amount(self, lo: float, hi: float) -> Decimal:
        return Decimal(str(round(self.rng.uniform(lo, hi), 2)))

    def add(self, dates: list[date], desc: "str | Callable[[date], str]", lo: float, hi: float = 0,
            *, inflow: bool = False, tag: Optional[str] = None) -> None:
        for d in dates:
            amt = Decimal(str(lo)) if not hi else self.amount(lo, hi)
            text = desc(d) if callable(desc) else desc
            self.rows.append((d, text, amt if inflow else -amt, tag))


def _rule(company: Company, match: str, at: AccountType, category: str, sub: str,
          priority: Optional[int] = None) -> CategoryRule:
    return CategoryRule(scope=RuleScope.COMPANY, company_id=company.id, match_text=match,
                        account_type=at, category=category, subcategory=sub, priority=priority)


def _manual(session: Session, tx: Transaction, at: AccountType, category: str, sub: str,
            owner: User, when: datetime, reason: Optional[str] = None) -> None:
    """Lo que el bookkeeper escribió a mano (lo manual manda), con su fila en el historial.
    `reason="planted_mistake"`: una corrección EQUIVOCADA plantada a propósito (una trampa para el
    auditor). La medición de la F11 no debe contarla como verdad (pedido de la F11, 4-oct)."""
    before = corrections.snapshot(tx)
    tx.account_type, tx.category, tx.subcategory = at, category, sub.upper()
    tx.source, tx.matched_rule_id = "manual", None
    tx.confidence, tx.needs_review, tx.review_question = 1.0, False, None
    session.add(tx)
    row = corrections.record_change(session, tx, before, via=corrections.VIA_MANUAL, user_id=owner.id,
                                    reason=reason)
    if row is not None:
        row.changed_at = when


def _history(session: Session, company: Company, owner: User, plan: list[tuple]) -> None:
    """MÁS HISTORIAL DE CORRECCIONES (pedido de la F11, 4-oct; aprobado por Jose el 8-oct): el
    bookkeeper categorizó a mano algunos Pendientes, repartidos entre julio y septiembre, y a veces se
    contradijo. Es la materia con que el agente ④ redacta la fórmula y con que se mide contra la línea
    base. `plan`: (inicio de la descripción, [(n-ésima aparición, sección, categoría, comercio)…])."""
    rows = sorted(session.exec(select(Transaction).where(Transaction.company_id == company.id)).all(),
                  key=lambda t: (t.date, t.id))
    for prefix, picks in plan:
        hits = [t for t in rows if t.description.startswith(prefix) and t.source is None]
        for n, at, cat, sub in picks:
            if n < len(hits):
                tx = hits[n]
                _manual(session, tx, at, cat, sub, owner,
                        datetime.combine(tx.date + timedelta(days=2), datetime.min.time()))


# ── Errores plantados para el revisor ① (13-oct) ─────────────────────────────────────────────────
# Cada error dice QUÉ filas (inicio de la descripción + cuáles de ellas, en el orden del libro), de qué
# TIPO es, qué haría falta (`expected`) y, si hay que sembrarlo, cómo (`action`). Con lo mismo se arma la
# respuesta escondida `DEMO_TRUTH_ERRORS`, que el agente NUNCA ve.

@dataclass
class Planted:
    type: str                 # personal_expense | sign_mismatch | merchant_two_categories |
                              # unusual_amount | rule_deviation | payroll_net_only
    detail: str               # en inglés: por qué es un error (lo lee la medición, no el visitante)
    prefix: str               # inicio de la descripción del banco
    which: object = "all"     # "all" | [índices entre las filas con ese inicio] | función(fila) -> bool
    action: Optional[tuple] = None    # ("manual", sección, categoría, comercio) | ("drift", categoría)
    expected: Optional[tuple] = None  # (sección, categoría) correcta; None = hay que preguntar / revisar
    story: Optional[str] = None       # "vacation_note" | "supermarket_note": la historia de una NOTA
    truth: bool = True                # False = solo PREPARA filas correctas (no es un error, no va a la respuesta)


def _pick(rows: list[tuple], spec: Planted) -> list[int]:
    """Índices (en `rows`, ya ordenadas como el libro) de las filas de un error."""
    idx = [i for i, r in enumerate(rows) if r[1].startswith(spec.prefix)]
    if spec.which == "all":
        return idx
    if callable(spec.which):
        return [i for i in idx if spec.which(rows[i])]
    return [idx[n] for n in spec.which if n < len(idx)]


def _apply_errors(session: Session, owner: User, rows: list[tuple], ids: list[int], specs: list[Planted]) -> None:
    """Siembra los errores que necesitan una acción (lo demás ya sale mal solo: una regla que caza de más,
    un monto raro). Lo escrito a mano va con `reason="planted_mistake"`: la medición no lo toma por verdad."""
    for spec in specs:
        if not spec.action:
            continue
        for i in _pick(rows, spec):
            tx = session.get(Transaction, ids[i])
            if spec.action[0] == "manual":
                _, at, cat, sub = spec.action
                _manual(session, tx, at, cat, sub, owner,
                        datetime.combine(tx.date + timedelta(days=3), datetime.min.time()), reason="planted_mistake")
            elif spec.action[0] == "manual_ok":
                # Filas de CONTEXTO que el bookkeeper escribió bien (el juego a ciegas): a mano, pero SIN
                # fila en el historial. Si entraran, la medición de la F11 las listaría al aprender y el
                # juego dejaría de ser ciego (14-oct).
                _, at, cat, sub = spec.action
                tx.account_type, tx.category, tx.subcategory = at, cat, sub.upper()
                tx.source, tx.matched_rule_id = "manual", None
                tx.confidence, tx.needs_review, tx.review_question = 1.0, False, None
                session.add(tx)
            elif spec.action[0] == "drift":   # la regla se editó y esta fila quedó atrás
                tx.category = spec.action[1]
                session.add(tx)


def _seed_book(session: Session, firm: Firm, owner: User, name: str, sector: str, account: str,
               book: Book, rules: list[tuple], plant: Callable) -> Company:
    company = Company(name=name, sector=sector, user_id=owner.id, firm_id=firm.id)
    session.add(company)
    session.flush()
    seed_chart(session, company)   # SU copia del plan por defecto (Schedule C del sector; 18-oct)
    made = [_rule(company, *r) for r in rules]
    session.add_all(made)
    session.flush()

    book.rows.sort(key=lambda r: (r[0], r[1]))
    parsed = [(d, desc, signed, account, make_fingerprint(d, desc, signed, account))
              for d, desc, signed, _tag in book.rows]
    core = ingest_parsed(session, company.id, sector, parsed, commit=False)
    ids = core["transaction_ids"]
    assert len(ids) == len(book.rows), "el motor omitió filas del demo"
    tagged: dict[str, list[Transaction]] = {}
    for (_d, _desc, _s, tag), tid in zip(book.rows, ids):
        if tag:
            tagged.setdefault(tag, []).append(session.get(Transaction, tid))
    # Los errores plantados van ANTES de `plant`: lo que se escribe a mano aquí queda fuera del historial
    # que `plant` arma después (así no se corren los índices de `_history`).
    _apply_errors(session, owner, book.rows, ids, ERRORS.get(name, []) + ERRORS_BLIND.get(name, []))
    plant(session, company, {r.match_text: r for r in made}, tagged, owner)
    # Las NOTAS del bookkeeper (tabla `booknote` de la F11, 8-oct): misma validación que la API.
    book_notes.seed_notes(session, company.id, DEMO_NOTES.get(name, []))
    session.flush()
    return company


# ── Rivera HVAC LLC (contratista) ───────────────────────────────────────────────────────────────
# La historia central: STARBUCKS todo el trimestre (Meals, por regla) y la nota de las vacaciones en
# Europa del 3 al 17-ago → la fórmula del libro con ventana de fechas. Además: comercios recurrentes
# SIN regla (muchos Pendientes), SHELL categorizado de dos formas, una fila que se salió de su regla
# (PG&E), dos reglas que chocan (AMAZON / AMAZON MKTPL) y LOWE'S corregido de Office a Materials.

_EUROPE = ["STARBUCKS 1123 PARIS FR", "STARBUCKS 0457 PARIS FR", "STARBUCKS 2210 MADRID ES",
           "STARBUCKS 0981 BARCELONA ES", "STARBUCKS 3307 ROMA IT", "STARBUCKS 3312 ROMA IT",
           "STARBUCKS 1876 FIRENZE IT", "STARBUCKS 4021 MILANO IT"]


def _rivera() -> Book:
    b = Book("rivera")
    vac = lambda d: date(2026, 8, 3) <= d <= date(2026, 8, 17)  # noqa: E731
    b.add(weekly(4), "INTUIT QB PAYMENTS DEPOSIT RIVERA HVAC", 1800, 6500, inflow=True)
    b.add(spread(5, b.rng), lambda d: f"MOBILE DEPOSIT REF {d:%m%d}{b.rng.randint(100, 999)}",
          900, 3500, inflow=True)
    home = [d for d in weekly(0) + weekly(3) if not vac(d)]
    b.add(sorted(home), "STARBUCKS STORE 08812 PITTSBURG CA", 5.25, 14.80)
    europe = [d for d in _days() if vac(d) and d.weekday() in (0, 2, 4, 5)]
    for i, d in enumerate(europe):
        b.add([d], _EUROPE[i % len(_EUROPE)], 4.50, 12.90)
    b.add(spread(12, b.rng), "CHEVRON 0209384 CONCORD CA", 62, 118)
    b.add(spread(9, b.rng), "THE HOME DEPOT #0628 ANTIOCH CA", 85, 940)
    b.add(spread(4, b.rng), "FERGUSON HVAC SUPPLY #1462 CONCORD CA", 320, 1850)
    b.add(spread(3, b.rng), "JOHNSTONE SUPPLY 0331 HAYWARD CA", 210, 760)
    b.add(monthly(1), "ACH DEBIT BAYSIDE PROPERTIES LLC RENT", 2400)
    # NÓMINA como la ve un banco de verdad (Jose, 10-oct): solo los pagos NETOS a los empleados (Gusto,
    # quincenal) y las remesas de impuestos al IRS (EFTPS, depósito mensual de la 941). El BRUTO no
    # aparece en ninguna parte: es lo que el auditor tiene que notar.
    b.add(biweekly(date(2026, 7, 10)), "GUSTO PAYROLL NET PAY RIVERA HVAC", 3150, 4100)
    net = {m: sum(-a for d, desc, a, _t in b.rows if desc.startswith("GUSTO PAYROLL") and d.month == m) for m in (7, 8)}
    # Depósito mensual: el día 15 del mes siguiente (impuesto retenido + Seguro Social y Medicare de las
    # dos partes ≈ 32 % del neto). Julio paga junio, que no está en el libro: se estima como julio.
    for d, base, k in ((date(2026, 7, 15), net[7], Decimal("0.31")), (date(2026, 8, 14), net[7], Decimal("0.32")),
                       (date(2026, 9, 15), net[8], Decimal("0.32"))):
        b.add([d], "IRS USATAXPYMT 270648213 EFTPS RIVERA HVAC LLC", float((base * k).quantize(Decimal("0.01"))))
    b.add(monthly(5), "STATE FARM INSURANCE 800-956-6310", 412.50)
    b.add(monthly(12), "VERIZON WIRELESS PAYMENTS", 148.32)
    b.add(monthly(18), "PGANDE WEB ONLINE PG&E", 176, 262, tag="pge")
    b.add(monthly(28), "MONTHLY SERVICE FEE", 15)
    b.add(spread(4, b.rng), "AMAZON.COM*2K7RT AMZN.COM/BILL WA", 18, 140)
    b.add(spread(3, b.rng), "AMAZON MKTPL*RT4Q1 AMZN.COM/BILL WA", 45, 320)
    b.add(spread(6, b.rng), "SHELL OIL 57444 WALNUT CREEK CA", 48, 96, tag="shell")
    b.add(spread(3, b.rng), "LOWE'S #1557 ANTIOCH CA", 96, 480, tag="lowes")
    b.add(monthly(20), "ONLINE TRANSFER TO SAVINGS XXXXXX1209", 1000)
    b.add(monthly(25), "ONLINE TRANSFER TO CREDIT CARD XXXXXX8823", 650, 1400)
    b.add(spread(5, b.rng), "ZELLE PAYMENT TO DANIEL RIVERA", 1200, 1800)
    # Lo que trae un libro real (13-oct), con fechas y montos FIJOS (no tocan el generador aleatorio, así
    # nada de lo anterior cambia): la comisión de QuickBooks Payments, un cliente que pagó por Zelle, una
    # compra grande, un mes de teléfonos nuevos, suscripciones y compras de la casa.
    for d, a in ((date(2026, 7, 31), 38.42), (date(2026, 8, 31), 51.17), (date(2026, 9, 30), 44.90)):
        b.add([d], "INTUIT QB PAYMENTS FEE RIVERA HVAC", a)
    b.add([date(2026, 8, 19)], "ZELLE PAYMENT FROM MARCUS OKAFOR JPM51K", 850, inflow=True)
    b.add([date(2026, 9, 28)], "THE HOME DEPOT #0628 ANTIOCH CA", 4850.00)
    b.add([date(2026, 8, 27)], "VERIZON WIRELESS PAYMENTS", 612.40)
    b.add(monthly(9), "NETFLIX.COM", 22.99)
    b.add([date(2026, 7, 23)], "TARGET T-2771 ANTIOCH CA", 186.45)
    _blind_rows(b, "Rivera HVAC LLC")
    return b


_RIVERA_RULES = [
    ("INTUIT QB PAYMENTS", REVENUE, "Gross receipts or sales", "QuickBooks Payments"),
    ("STARBUCKS", OPEX, "Deductible meals", "Starbucks"),
    ("STATE FARM", OPEX, "Insurance", "State Farm"),
    ("VERIZON", OPEX, "Utilities", "Verizon"),
    ("PG&E", OPEX, "Utilities", "PG&E"),
    ("MONTHLY SERVICE FEE", OPEX, "Bank fees", "Bank"),
    # Dos reglas que chocan: "AMAZON MKTPL" cae también en "AMAZON" con otra categoría.
    ("AMAZON", OPEX, "Office expense", "Amazon"),
    ("AMAZON MKTPL", OPEX, "Supplies", "Amazon Marketplace"),
]


def _plant_rivera(session, company, rules, tagged, owner) -> None:
    # SHELL de dos formas: la mitad a mano como combustible del vehículo, la otra como del equipo.
    for i, tx in enumerate(tagged["shell"]):
        at, cat = (OPEX, "Car and truck expenses") if i % 2 == 0 else (COGS, "Equipment fuel")
        _manual(session, tx, at, cat, "Shell", owner, datetime.combine(tx.date + timedelta(days=3),
                                                                        datetime.min.time()))
    # LOWE'S: el bookkeeper lo puso en Office y lo corrigió a Materials, las 3 veces.
    for tx in tagged["lowes"]:
        first = datetime.combine(tx.date + timedelta(days=2), datetime.min.time())
        _manual(session, tx, OPEX, "Office expense", "Lowe's", owner, first)
        session.flush()
        _manual(session, tx, COGS, "Materials", "Lowe's", owner, first + timedelta(days=9))
    # Una fila que SE SALIÓ de su regla: dice que la decidió "PG&E", pero su categoría ya no es la de
    # la regla (la regla se editó después y esta fila quedó atrás) → el auditor la marca.
    stray = tagged["pge"][0]
    stray.category = "Office expense"
    session.add(stray)
    # El historial de un trimestre: lo que el bookkeeper fue resolviendo a mano. HOME DEPOT una vez
    # como herramienta (se contradice), la renta en julio y agosto, la nómina, los Zelle al dueño.
    _history(session, company, owner, [
        ("CHEVRON", [(0, OPEX, "Car and truck expenses", "Chevron"), (4, OPEX, "Car and truck expenses", "Chevron"),
                     (9, OPEX, "Car and truck expenses", "Chevron")]),
        ("THE HOME DEPOT", [(0, COGS, "Materials", "Home Depot"), (3, OPEX, "Supplies", "Home Depot"),
                            (7, COGS, "Materials", "Home Depot")]),
        ("FERGUSON", [(0, COGS, "Materials", "Ferguson"), (2, COGS, "Materials", "Ferguson")]),
        ("JOHNSTONE", [(1, COGS, "Materials", "Johnstone Supply")]),
        ("ACH DEBIT BAYSIDE", [(0, OPEX, "Rent or lease – other business property", "Bayside Properties"), (1, OPEX, "Rent or lease – other business property", "Bayside Properties")]),
        ("GUSTO PAYROLL", [(0, OPEX, "Wages", "Gusto"), (2, OPEX, "Wages", "Gusto"),
                           (5, OPEX, "Wages", "Gusto")]),
        ("ZELLE PAYMENT TO DANIEL RIVERA", [(0, NON_PL, "Owner Draw", "Daniel Rivera"), (3, NON_PL, "Owner Draw", "Daniel Rivera")]),
    ])


# ── Sparkle Cleaning Co. (limpieza) ─────────────────────────────────────────────────────────────
# Descripciones crípticas (SQ *, TST*, PAYPAL *), un gimnasio camuflado como gasto de oficina, y
# pagos Zelle a personas que se quedan Pendientes (solo el cliente sabe qué son).

def _sparkle() -> Book:
    b = Book("sparkle")
    b.add(monthly(2), "ACH CREDIT BAYVIEW HOA OPERATING", 2800, inflow=True)
    b.add(monthly(3), "ACH CREDIT OAKMONT PROPERTY MGMT", 4200, inflow=True)
    b.add(weekly(1), lambda d: f"SQ DEP {d:%y%m%d} T3XQ8", 580, 1420, inflow=True)
    b.add(spread(4, b.rng), "SQ *JANITEX SUPPLY", 140, 520)
    b.add(spread(5, b.rng), "TST* EL FAROLITO 0412", 14, 46)
    b.add(spread(2, b.rng), "PAYPAL *ZOROTOOLS", 60, 240)
    b.add(spread(3, b.rng), "PAYPAL *ULINE", 85, 410)
    b.add(spread(2, b.rng), "SQ *BAYCITY UNIFORMS", 120, 300)
    b.add(monthly(6), "24 HOUR FITNESS USA 800-432-6348 CA", 49.99, tag="gym")
    b.add(biweekly(date(2026, 7, 3)), "ZELLE PAYMENT TO MARIA LOPEZ JPM99B", 450, 720)
    b.add(biweekly(date(2026, 7, 10)), "ZELLE PAYMENT TO ANA GUTIERREZ JPM77C", 420, 680)
    b.add(spread(3, b.rng), "ZELLE PAYMENT TO JOSE MARTINEZ JPM12D", 150, 400)
    b.add(spread(5, b.rng), "COSTCO WHSE #0481 SAN LEANDRO CA", 90, 380)
    b.add(spread(7, b.rng), "SHELL OIL 12093 OAKLAND CA", 40, 85)
    b.add(monthly(8), "NEXT INSURANCE PREMIUM", 186.40)
    b.add(monthly(15), "T-MOBILE PCS SVC", 95)
    b.add(monthly(28), "MONTHLY SERVICE FEE", 12)
    b.add(monthly(22), "ONLINE TRANSFER TO SAVINGS XXXXXX5530", 500)
    # Lo que trae un libro real (13-oct), con fechas y montos fijos.
    b.add([date(2026, 8, 12)], "ZELLE PAYMENT FROM BRIGHTWAY REALTY JPM40Q", 640, inflow=True)
    b.add([date(2026, 9, 2)], "T-MOBILE PCS SVC", 389.00)
    b.add([date(2026, 8, 5)], "NORDSTROM RACK #0512 OAKLAND CA", 142.80)
    b.add([date(2026, 7, 14)], "SAFEWAY #2213 SAN LEANDRO CA", 96.20)
    b.add([date(2026, 9, 9)], "SAFEWAY #2213 SAN LEANDRO CA", 71.35)
    b.add([date(2026, 9, 24)], "SQ *JANITEX SUPPLY", 1940.00)
    _blind_rows(b, "Sparkle Cleaning Co.")
    return b


_SPARKLE_RULES = [
    ("BAYVIEW HOA", REVENUE, "Gross receipts or sales", "Bayview HOA"),
    ("COSTCO", COGS, "Supplies – cleaning supplies", "Costco"),
    ("SHELL", OPEX, "Car and truck expenses", "Shell"),
    ("NEXT INSURANCE", OPEX, "Insurance", "Next Insurance"),
    ("MONTHLY SERVICE FEE", OPEX, "Bank fees", "Bank"),
]


def _plant_sparkle(session, company, rules, tagged, owner) -> None:
    # El gimnasio, cargado a mano como gasto de oficina (un gasto personal camuflado).
    for tx in tagged["gym"]:
        _manual(session, tx, OPEX, "Office expense", "24 Hour Fitness", owner,
                datetime.combine(tx.date + timedelta(days=4), datetime.min.time()), reason="planted_mistake")
    _history(session, company, owner, [
        ("TST* EL FAROLITO", [(0, OPEX, "Deductible meals", "El Farolito"), (3, OPEX, "Deductible meals", "El Farolito")]),
        ("SQ *JANITEX", [(0, COGS, "Supplies – cleaning supplies", "Janitex Supply"), (2, COGS, "Supplies – cleaning supplies", "Janitex Supply")]),
        ("PAYPAL *ULINE", [(0, COGS, "Supplies – cleaning supplies", "Uline"), (2, OPEX, "Office expense", "Uline")]),
        ("T-MOBILE", [(0, OPEX, "Utilities", "T-Mobile"), (2, OPEX, "Utilities", "T-Mobile")]),
        ("ZELLE PAYMENT TO MARIA LOPEZ", [(0, COGS, "Contract labor – subcontractors", "Maria Lopez"), (2, COGS, "Contract labor – subcontractors", "Maria Lopez"),
                                          (5, OPEX, "Wages", "Maria Lopez")]),
        ("SQ DEP", [(0, REVENUE, "Gross receipts or sales", "Square"), (6, REVENUE, "Gross receipts or sales", "Square")]),
    ])


# ── Casa Lupita Taqueria (restaurante) ──────────────────────────────────────────────────────────
# La nota "más de $50 en supermercado suele ser personal" y compras de supermercado que la cruzan
# (SAFEWAY y WALMART, por regla como Purchases – food), y una regla vieja que ya no agarra nada.

def _lupita() -> Book:
    b = Book("lupita")
    b.add(sorted(weekly(0) + weekly(2) + weekly(4)), "SQUARE INC DEPOSIT 8YH2K", 880, 2650, inflow=True)
    b.add(weekly(1), "DOORDASH INC PAYMENT", 290, 810, inflow=True)
    b.add(weekly(3), "SYSCO SAN FRANCISCO INC", 620, 1480)
    b.add(weekly(5), "RESTAURANT DEPOT #12 SAN LEANDRO", 190, 610)
    # Supermercado a los dos lados de la nota de los $50 (8-oct: con un solo rango salían todas arriba).
    b.add(spread(4, b.rng, START, date(2026, 9, 25)), "SAFEWAY #1711 OAKLAND CA", 18, 46)
    b.add(spread(4, b.rng, date(2026, 7, 6), END), "SAFEWAY #1711 OAKLAND CA", 58, 142)
    b.add(spread(3, b.rng), "WALMART SUPERCENTER #2341", 14, 44)
    b.add(spread(2, b.rng, date(2026, 7, 20), END), "WALMART SUPERCENTER #2341", 64, 96)
    b.add(spread(4, b.rng), "SMART & FINAL #412", 35, 180)
    b.add(monthly(1), "ACH DEBIT MISSION ST PROPERTIES RENT", 3800)
    b.add(monthly(16), "PGANDE WEB ONLINE PG&E", 410, 690)
    b.add(monthly(10), "ECOLAB PEST ELIM", 118)
    b.add(biweekly(date(2026, 7, 7)), "CINTAS CORP LINEN SVC", 84, 132)
    b.add(biweekly(date(2026, 7, 9)), "ADP PAYROLL FEES NET PAY", 4200, 5600)
    b.add(monthly(28), "MONTHLY SERVICE FEE", 15)
    b.add(monthly(24), "ONLINE TRANSFER TO CREDIT CARD XXXXXX4410", 900, 1700)
    # Lo que trae un libro real (13-oct), con fechas y montos fijos: el préstamo de Square Capital (sale
    # por la misma "SQUARE INC" de las ventas), un recibo de luz atrasado, un pedido grande, compras de casa.
    for d, a in ((date(2026, 7, 21), 310.00), (date(2026, 8, 18), 310.00), (date(2026, 9, 15), 310.00)):
        b.add([d], "SQUARE INC LOAN PMT 8YH2K", a)
    b.add([date(2026, 9, 2)], "PGANDE WEB ONLINE PG&E", 1840.17)
    b.add([date(2026, 8, 26)], "SYSCO SAN FRANCISCO INC", 4920.55)
    b.add([date(2026, 8, 8)], "BEST BUY 00118 EMERYVILLE CA", 849.99)
    b.add([date(2026, 7, 18)], "AMC METREON 16 SAN FRANCISCO", 54.20)
    b.add(monthly(11), "DISNEY PLUS", 15.99)
    _blind_rows(b, "Casa Lupita Taqueria")
    return b


_LUPITA_RULES = [
    ("SQUARE INC", REVENUE, "Gross receipts or sales", "Square"),
    ("SYSCO", COGS, "Purchases – food", "Sysco"),
    ("SAFEWAY", COGS, "Purchases – food", "Safeway"),
    ("WALMART", COGS, "Purchases – food", "Walmart"),
    ("PG&E", OPEX, "Utilities", "PG&E"),
    # La regla vieja: el proveedor de verduras cerró y ya no aparece en el banco.
    ("CHEVYS FRESH PRODUCE", COGS, "Purchases – food", "Chevys Fresh Produce"),
]


def _plant_lupita(session, company, rules, tagged, owner) -> None:
    _history(session, company, owner, [
        ("RESTAURANT DEPOT", [(0, COGS, "Purchases – food", "Restaurant Depot"), (4, COGS, "Purchases – food", "Restaurant Depot"),
                              (9, COGS, "Materials and supplies", "Restaurant Depot")]),
        ("CINTAS", [(0, OPEX, "Linens and laundry", "Cintas"), (3, OPEX, "Linens and laundry", "Cintas")]),
        ("ADP PAYROLL", [(0, OPEX, "Wages", "ADP"), (3, OPEX, "Wages", "ADP")]),
        ("ECOLAB", [(0, OPEX, "Cleaning services", "Ecolab"), (2, OPEX, "Repairs and maintenance", "Ecolab")]),
        ("ACH DEBIT MISSION ST", [(0, OPEX, "Rent or lease – other business property", "Mission St Properties"), (1, OPEX, "Rent or lease – other business property", "Mission St Properties")]),
        ("SMART & FINAL", [(1, COGS, "Purchases – food", "Smart & Final")]),
    ])


_FOREIGN = lambda r: r[1].split()[-1] in ("FR", "ES", "IT")  # noqa: E731
_OVER_50 = lambda r: -r[2] >= 50  # noqa: E731

ERRORS: dict[str, list[Planted]] = {
    "Rivera HVAC LLC": [
        Planted("rule_deviation", "Decided by the PG&E rule but no longer carries the rule's category (the rule was edited later).",
                "PGANDE WEB ONLINE PG&E", [0], expected=(OPEX, "Utilities")),
        Planted("merchant_two_categories", "Shell is service-van fuel; half the rows were typed as equipment fuel.",
                "SHELL OIL 57444", [1, 3, 5], expected=(OPEX, "Car and truck expenses")),
        Planted("personal_expense", "Coffee bought in Europe during the owner's vacation (Aug 3–17): personal, not deductible meals.",
                "STARBUCKS", _FOREIGN, expected=(NON_PL, "Owner Draw"), story="vacation_note"),
        Planted("sign_mismatch", "QuickBooks Payments processing FEE (money out) caught by the revenue rule: it is a bank fee.",
                "INTUIT QB PAYMENTS FEE", expected=(OPEX, "Bank fees")),
        Planted("sign_mismatch", "A customer paid by Zelle (money IN) and it was typed as a subcontractor expense.",
                "ZELLE PAYMENT FROM MARCUS OKAFOR", action=("manual", COGS, "Contract labor – subcontractors", "Marcus Okafor"),
                expected=(REVENUE, "Gross receipts or sales")),
        Planted("unusual_amount", "$4,850 at Home Depot when it is usually $85–940: equipment? personal? a duplicate? Ask.",
                "THE HOME DEPOT #0628", lambda r: -r[2] > 2000, action=("manual", COGS, "Materials", "Home Depot")),
        Planted("unusual_amount", "Verizon is $148.32 every month; one extra charge of $612.40 (new phones?).",
                "VERIZON WIRELESS", lambda r: -r[2] > 300),
        Planted("personal_expense", "Netflix booked as a business subscription.",
                "NETFLIX.COM", action=("manual", OPEX, "Dues and subscriptions", "Netflix"), expected=(NON_PL, "Owner Draw")),
        Planted("personal_expense", "A Target run (household items) typed as office expense.",
                "TARGET T-2771", action=("manual", OPEX, "Office expense", "Target"), expected=(NON_PL, "Owner Draw")),
        Planted("payroll_net_only", "Payroll shows only NET pay (Gusto) and the IRS remittances: gross wages and the employer tax split are missing.",
                "GUSTO PAYROLL NET PAY"),
        Planted("payroll_net_only", "IRS payroll-tax remittances: part employee withholding (wages), part employer tax.",
                "IRS USATAXPYMT"),
    ],
    "Sparkle Cleaning Co.": [
        Planted("personal_expense", "Gym membership typed as office expense.",
                "24 HOUR FITNESS", expected=(NON_PL, "Owner Draw")),
        Planted("merchant_two_categories", "Uline is cleaning supplies; one row was typed as office expense.",
                "PAYPAL *ULINE", [2], expected=(COGS, "Supplies – cleaning supplies")),
        Planted("sign_mismatch", "A client paid by Zelle (money IN) and it was typed as a subcontractor expense.",
                "ZELLE PAYMENT FROM BRIGHTWAY REALTY", action=("manual", COGS, "Contract labor – subcontractors", "Brightway Realty"),
                expected=(REVENUE, "Gross receipts or sales")),
        Planted("unusual_amount", "T-Mobile is $95 a month; a $389 charge (a phone?).",
                "T-MOBILE PCS SVC", lambda r: -r[2] > 200, action=("manual", OPEX, "Utilities", "T-Mobile")),
        Planted("personal_expense", "Clothes from Nordstrom Rack typed as uniforms.",
                "NORDSTROM RACK", action=("manual", OPEX, "Uniforms", "Nordstrom Rack"), expected=(NON_PL, "Owner Draw")),
        Planted("personal_expense", "Groceries at Safeway typed as cleaning supplies (supplies come from Costco).",
                "SAFEWAY #2213", action=("manual", COGS, "Supplies – cleaning supplies", "Safeway"), expected=(NON_PL, "Owner Draw")),
        Planted("rule_deviation", "Decided by the COSTCO rule but carries Office expense (the rule changed later).",
                "COSTCO WHSE", [2], action=("drift", "Office expense"), expected=(COGS, "Supplies – cleaning supplies")),
        # 🔄 18-oct (plan de cuentas oficial): "Transportation" y "Vehicle Fuel" son hoy la MISMA cuenta (Car and
        # truck expenses); la fila equivocada pasa a insumos de limpieza para que el error siga existiendo.
        Planted("merchant_two_categories", "Shell is fuel for the van (rule: Car and truck expenses); one row typed as cleaning supplies.",
                "SHELL OIL 12093", [3], action=("manual", COGS, "Supplies – cleaning supplies", "Shell"), expected=(OPEX, "Car and truck expenses")),
        Planted("unusual_amount", "$1,940 at Janitex when it is usually $140–520: a bulk order, or a mistake?",
                "SQ *JANITEX", lambda r: -r[2] > 1000, action=("manual", COGS, "Supplies – cleaning supplies", "Janitex Supply")),
    ],
    "Casa Lupita Taqueria": [
        Planted("personal_expense", "Supermarket purchases over $50 (the bookkeeper's note): usually personal.",
                "SAFEWAY #1711", _OVER_50, expected=(NON_PL, "Owner Draw"), story="supermarket_note"),
        Planted("personal_expense", "Supermarket purchases over $50 (the bookkeeper's note): usually personal.",
                "WALMART SUPERCENTER", _OVER_50, expected=(NON_PL, "Owner Draw"), story="supermarket_note"),
        Planted("merchant_two_categories", "Restaurant Depot is food purchases; one row was typed as materials and supplies (paper & packaging).",
                "RESTAURANT DEPOT", [9], expected=(COGS, "Purchases – food")),
        Planted("merchant_two_categories", "Ecolab pest control typed two ways; the cleaning-services row is the odd one.",
                "ECOLAB", [0], expected=(OPEX, "Repairs and maintenance")),
        Planted("sign_mismatch", "Square Capital LOAN payment (money out) caught by the SQUARE INC sales rule.",
                "SQUARE INC LOAN PMT", expected=(NON_PL, "Loan Principal Payment")),
        Planted("unusual_amount", "PG&E is $410–690 a month; one $1,840 bill (past-due catch-up?).",
                "PGANDE WEB ONLINE PG&E", lambda r: -r[2] > 1000),
        Planted("unusual_amount", "Sysco is $620–1,480 a week; one $4,920 order.",
                "SYSCO SAN FRANCISCO", lambda r: -r[2] > 3000),
        Planted("personal_expense", "Best Buy (a TV?) typed as smallwares.",
                "BEST BUY", action=("manual", OPEX, "Supplies – smallwares", "Best Buy"), expected=(NON_PL, "Owner Draw")),
        Planted("personal_expense", "Movie tickets typed as advertising.",
                "AMC METREON", action=("manual", OPEX, "Advertising", "AMC"), expected=(NON_PL, "Owner Draw")),
        Planted("personal_expense", "Disney+ booked as a business subscription.",
                "DISNEY PLUS", action=("manual", OPEX, "Dues and subscriptions", "Disney+"), expected=(NON_PL, "Owner Draw")),
        Planted("rule_deviation", "Decided by the SYSCO rule but carries Materials and supplies (the rule changed later).",
                "SYSCO SAN FRANCISCO", [6], action=("drift", "Materials and supplies"), expected=(COGS, "Purchases – food")),
        Planted("payroll_net_only", "Payroll shows only NET pay (ADP) and NO tax remittance at all: gross wages and payroll taxes are missing.",
                "ADP PAYROLL"),
    ],
}

# ── EL SEGUNDO JUEGO, A CIEGAS (14-oct; pedido de la F11, aprobado por Jose) ──────────────────────────
# Para un número honesto en el README: la F11 ajustó su auditor mirando el primer juego, así que este
# NO lo ve antes de medir. Filas nuevas (otros comercios, otros montos, otras fechas), fecha y monto FIJOS
# (no tocan el generador aleatorio: ni los montos ni `DEMO_TRUTH_ERRORS` cambian), y nada queda Pendiente
# (así `DEMO_TRUTH_PENDING` tampoco cambia). Además de los tipos de siempre, tipos que el primero no tiene:
# `duplicate_charge`, `transfer_as_expense`, `loan_as_revenue`, `owner_money_as_revenue`, `wrong_section`.
_BLIND_ROWS: dict[str, list[tuple]] = {   # (fecha, descripción, monto, entra?)
    "Rivera HVAC LLC": [
        (date(2026, 7, 8), "ONDECK CAPITAL FUNDING DEPOSIT", 15000.00, True),
        (date(2026, 7, 28), "UNITED RENTALS #C47 CONCORD CA", 1180.00, False),
        (date(2026, 7, 30), "MONTHLY SERVICE FEE", 95.00, False),
        (date(2026, 8, 3), "CHASE CREDIT CRD AUTOPAY PPD ID 4760039224", 1262.18, False),
        (date(2026, 8, 22), "SEPHORA #1123 WALNUT CREEK CA", 78.40, False),
        (date(2026, 9, 8), "GRAINGER 1043 HAYWARD CA", 318.77, False),
        (date(2026, 9, 8), "GRAINGER 1043 HAYWARD CA", 318.77, False),
    ],
    "Sparkle Cleaning Co.": [
        (date(2026, 7, 7), "GOOGLE *WORKSPACE SPARKLEC", 14.40, False),
        (date(2026, 8, 7), "GOOGLE *WORKSPACE SPARKLEC", 14.40, False),
        (date(2026, 9, 7), "GOOGLE *WORKSPACE SPARKLEC", 14.40, False),
        (date(2026, 7, 19), "SPOTIFY USA", 11.99, False),
        (date(2026, 8, 19), "SPOTIFY USA", 11.99, False),
        (date(2026, 9, 19), "SPOTIFY USA", 11.99, False),
        (date(2026, 7, 27), "AMEX EPAYMENT ACH PMT W3492", 842.33, False),
        (date(2026, 8, 20), "SQ *BRIGHT MOPS CO", 214.60, False),
        (date(2026, 8, 20), "SQ *BRIGHT MOPS CO", 214.60, False),
        (date(2026, 9, 1), "THUMBTACK LEAD CREDITS", 96.00, False),
        (date(2026, 9, 26), "PETCO 1432 OAKLAND CA", 64.18, False),
    ],
    "Casa Lupita Taqueria": [
        (date(2026, 7, 2), "ONLINE TRANSFER FROM LUPITA ORTEGA PERSONAL", 5000.00, True),
        (date(2026, 7, 13), "UBER EATS PAYOUT 7KQ2", 418.36, True),
        (date(2026, 8, 10), "UBER EATS PAYOUT 7KQ2", 377.91, True),
        (date(2026, 7, 29), "SFPUC WATER PAYMENT", 212.44, False),
        (date(2026, 7, 29), "SFPUC WATER PAYMENT", 212.44, False),
        (date(2026, 8, 4), "WEBSTAURANTSTORE.COM", 236.90, False),
        (date(2026, 8, 31), "WEBSTAURANTSTORE.COM", 158.25, False),
        (date(2026, 9, 21), "WEBSTAURANTSTORE.COM", 301.12, False),
        (date(2026, 8, 13), "HOBART SERVICE 4471 SAN JOSE", 486.00, False),
        (date(2026, 9, 12), "ULTA BEAUTY #741 EMERYVILLE", 63.70, False),
        (date(2026, 7, 6), "COMCAST BUSINESS 8155 ACH", 119.95, False),
        (date(2026, 8, 6), "COMCAST BUSINESS 8155 ACH", 119.95, False),
        (date(2026, 8, 25), "COMCAST BUSINESS 8155 ACH", 642.80, False),
        (date(2026, 9, 7), "COMCAST BUSINESS 8155 ACH", 119.95, False),
    ],
}


def _blind_rows(b: "Book", name: str) -> None:
    for d, desc, amount, inflow in _BLIND_ROWS[name]:
        b.add([d], desc, amount, inflow=inflow)


ERRORS_BLIND: dict[str, list[Planted]] = {
    "Rivera HVAC LLC": [
        Planted("loan_as_revenue", "Loan money received (OnDeck) typed as job revenue: it is a liability, not income.",
                "ONDECK CAPITAL", action=("manual", REVENUE, "Gross receipts or sales", "OnDeck"), expected=(NON_PL, "Loan Proceeds")),
        Planted("wrong_section", "Equipment rented for a job typed as overhead office expense; it is a direct job cost.",
                "UNITED RENTALS", action=("manual", OPEX, "Office expense", "United Rentals"), expected=(COGS, "Rent or lease – job equipment")),
        Planted("unusual_amount", "The monthly bank fee is $15; one month shows $95 (overdraft or wire fees?).",
                "MONTHLY SERVICE FEE", lambda r: -r[2] > 50),
        Planted("transfer_as_expense", "Paying the credit card typed as office expense: the purchases are already expenses (double count).",
                "CHASE CREDIT CRD AUTOPAY", action=("manual", OPEX, "Office expense", "Chase Card"), expected=(NON_PL, "Credit Card Payment")),
        Planted("personal_expense", "Sephora typed as office expense.",
                "SEPHORA", action=("manual", OPEX, "Office expense", "Sephora"), expected=(NON_PL, "Owner Draw")),
        Planted("duplicate_charge", "Grainger charged the same $318.77 twice on the same day: one is a duplicate to dispute.",
                "GRAINGER", [1], action=("manual", OPEX, "Supplies", "Grainger")),
        Planted("rule_deviation", "Decided by the STATE FARM rule but carries Car and truck expenses (the rule changed later).",
                "STATE FARM", [1], action=("drift", "Car and truck expenses"), expected=(OPEX, "Insurance")),
        Planted("setup", "", "GRAINGER", [0], action=("manual_ok", OPEX, "Supplies", "Grainger"), truth=False),
    ],
    "Sparkle Cleaning Co.": [
        Planted("merchant_two_categories", "Google Workspace is software; one month was typed as telephone & internet.",
                "GOOGLE *WORKSPACE", [1], action=("manual", OPEX, "Utilities", "Google Workspace"), expected=(OPEX, "Software")),
        Planted("personal_expense", "Spotify booked as business software.",
                "SPOTIFY", action=("manual", OPEX, "Software", "Spotify"), expected=(NON_PL, "Owner Draw")),
        Planted("transfer_as_expense", "Paying the Amex card typed as cleaning supplies (double count).",
                "AMEX EPAYMENT", action=("manual", COGS, "Supplies – cleaning supplies", "Amex"), expected=(NON_PL, "Credit Card Payment")),
        Planted("duplicate_charge", "Bright Mops charged the same $214.60 twice on the same day.",
                "SQ *BRIGHT MOPS", [1], action=("manual", COGS, "Supplies – cleaning supplies", "Bright Mops")),
        Planted("sign_mismatch", "Thumbtack lead credits (money OUT, advertising) typed as cleaning revenue.",
                "THUMBTACK", action=("manual", REVENUE, "Gross receipts or sales", "Thumbtack"), expected=(OPEX, "Advertising")),
        Planted("personal_expense", "Pet supplies typed as cleaning supplies.",
                "PETCO", action=("manual", COGS, "Supplies – cleaning supplies", "Petco"), expected=(NON_PL, "Owner Draw")),
        Planted("rule_deviation", "Decided by the NEXT INSURANCE rule but carries Office expense (the rule changed later).",
                "NEXT INSURANCE", [1], action=("drift", "Office expense"), expected=(OPEX, "Insurance")),
        Planted("rule_deviation", "Decided by the bank-fee rule but carries Office expense (the rule changed later).",
                "MONTHLY SERVICE FEE", [2], action=("drift", "Office expense"), expected=(OPEX, "Bank fees")),
        Planted("setup", "", "GOOGLE *WORKSPACE", [0, 2], action=("manual_ok", OPEX, "Software", "Google Workspace"), truth=False),
        Planted("setup", "", "SQ *BRIGHT MOPS", [0], action=("manual_ok", COGS, "Supplies – cleaning supplies", "Bright Mops"), truth=False),
    ],
    "Casa Lupita Taqueria": [
        Planted("owner_money_as_revenue", "The owner put her own money in; it was typed as sales.",
                "ONLINE TRANSFER FROM LUPITA ORTEGA", action=("manual", REVENUE, "Gross receipts or sales", "Lupita Ortega"),
                expected=(NON_PL, "Owner Contribution")),
        Planted("sign_mismatch", "Uber Eats payouts (money IN, delivery sales) typed as food purchases.",
                "UBER EATS PAYOUT", action=("manual", COGS, "Purchases – food", "Uber Eats"), expected=(REVENUE, "Gross receipts or sales")),
        Planted("duplicate_charge", "The water bill was paid twice on the same day.",
                "SFPUC WATER", [1], action=("manual", OPEX, "Utilities", "SFPUC")),
        Planted("merchant_two_categories", "WebstaurantStore is smallwares; one order was typed as food purchases.",
                "WEBSTAURANTSTORE", [1], action=("manual", COGS, "Purchases – food", "WebstaurantStore"), expected=(OPEX, "Supplies – smallwares")),
        Planted("wrong_section", "Hobart kitchen-equipment repair typed as food purchases (COGS); it is repairs & maintenance.",
                "HOBART SERVICE", action=("manual", COGS, "Purchases – food", "Hobart"), expected=(OPEX, "Repairs and maintenance")),
        Planted("personal_expense", "Ulta Beauty typed as smallwares.",
                "ULTA BEAUTY", action=("manual", OPEX, "Supplies – smallwares", "Ulta Beauty"), expected=(NON_PL, "Owner Draw")),
        Planted("unusual_amount", "Comcast is $119.95 a month; one extra $642.80 charge (equipment? a past-due balance?).",
                "COMCAST BUSINESS", lambda r: -r[2] > 300, action=("manual", OPEX, "Utilities", "Comcast")),
        Planted("setup", "", "COMCAST BUSINESS", lambda r: -r[2] < 300, action=("manual_ok", OPEX, "Utilities", "Comcast"), truth=False),
        Planted("setup", "", "SFPUC WATER", [0], action=("manual_ok", OPEX, "Utilities", "SFPUC"), truth=False),
        Planted("setup", "", "WEBSTAURANTSTORE", [0, 2], action=("manual_ok", OPEX, "Supplies – smallwares", "WebstaurantStore"), truth=False),
    ],
}

BOOKS = [
    ("Rivera HVAC LLC", "construction", "****4471", _rivera, _RIVERA_RULES, _plant_rivera),
    ("Sparkle Cleaning Co.", "cleaning", "****3390", _sparkle, _SPARKLE_RULES, _plant_sparkle),
    ("Casa Lupita Taqueria", "restaurant", "****7712", _lupita, _LUPITA_RULES, _plant_lupita),
]


def seed_demo_books(session: Session, firm: Firm, owner: User) -> list[Company]:
    """Los 3 libros del demo dentro de `firm`. Solo `flush()`: el commit es de quien llama."""
    books = [_seed_book(session, firm, owner, name, sector, account, make(), rules, plant)
             for name, sector, account, make, rules, plant in BOOKS]
    # Las verificaciones de comercios (agente ②, F11) ya buscadas UNA vez: cada visitante las recibe en la
    # caché de SU oficina y "Verificar comercios" no gasta créditos de Tavily (deuda #24, 13-oct).
    demo_web_evidence.seed_lookups(session, firm)
    session.flush()
    return books


# ── LA RESPUESTA ESCONDIDA (13-oct, para MEDIR al auditor donde la IA aporta) ───────────────────────
# El agente NUNCA ve esto: no está en la base ni en ningún prompt. Lo leen solo las mediciones de la F11.
#
# DEMO_TRUTH_PENDING: para cada comercio que queda Pendiente, la categoría correcta y su sección.
# `status`: "known" (hay respuesta) · "ambiguous" (de verdad no se sabe sin el cliente: no se inventa) ·
# "split" (la fila mezcla dos cosas y hay que partirla).
def _t(at: Optional[AccountType], category: Optional[str], status: str, why: str) -> dict:
    return {"account_type": at.value if at else None, "category": category, "status": status, "why": why}


DEMO_TRUTH_PENDING: dict[str, dict[str, dict]] = {
    "Rivera HVAC LLC": {
        "ACH DEBIT BAYSIDE PROPERTIES LLC RENT": _t(OPEX, "Rent or lease – other business property", "known", "Shop / office rent."),
        "CHEVRON": _t(OPEX, "Car and truck expenses", "known", "Service-van fuel."),
        "FERGUSON HVAC SUPPLY": _t(COGS, "Materials", "known", "HVAC parts for jobs."),
        "JOHNSTONE SUPPLY": _t(COGS, "Materials", "known", "HVAC parts for jobs."),
        "THE HOME DEPOT": _t(COGS, "Materials", "known", "Job materials (one $4,850 row is a planted unusual amount)."),
        "GUSTO PAYROLL NET PAY": _t(OPEX, "Wages", "known", "Technicians' net pay (the gross is not in the bank)."),
        "IRS USATAXPYMT": _t(OPEX, "Payroll taxes", "split",
                             "Payroll-tax remittance: employee withholding (part of wages) + employer tax."),
        "MOBILE DEPOSIT REF": _t(None, None, "ambiguous", "A deposited check: probably a customer, but could be a loan or the owner."),
        "ZELLE PAYMENT TO DANIEL RIVERA": _t(NON_PL, "Owner Draw", "known", "The owner paying himself."),
    },
    "Sparkle Cleaning Co.": {
        "ACH CREDIT OAKMONT PROPERTY MGMT": _t(REVENUE, "Gross receipts or sales", "known", "A client's monthly contract."),
        "SQ DEP": _t(REVENUE, "Gross receipts or sales", "known", "Card sales paid out by Square."),
        "PAYPAL *ULINE": _t(COGS, "Supplies – cleaning supplies", "known", "Supplies."),
        "PAYPAL *ZOROTOOLS": _t(OPEX, "Supplies", "known", "Tools."),
        "SQ *BAYCITY UNIFORMS": _t(OPEX, "Uniforms", "known", "Staff uniforms."),
        "SQ *JANITEX SUPPLY": _t(COGS, "Supplies – cleaning supplies", "known", "Janitorial supplies."),
        "T-MOBILE PCS SVC": _t(OPEX, "Utilities", "known", "Business phone line."),
        "TST* EL FAROLITO": _t(OPEX, "Deductible meals", "known", "Crew lunch."),
        "ZELLE PAYMENT TO MARIA LOPEZ": _t(None, None, "ambiguous", "A person: subcontractor or employee? Only the client knows."),
        "ZELLE PAYMENT TO ANA GUTIERREZ": _t(None, None, "ambiguous", "A person: subcontractor or employee? Only the client knows."),
        "ZELLE PAYMENT TO JOSE MARTINEZ": _t(None, None, "ambiguous", "A person: subcontractor or employee? Only the client knows."),
    },
    "Casa Lupita Taqueria": {
        "ACH DEBIT MISSION ST PROPERTIES RENT": _t(OPEX, "Rent or lease – other business property", "known", "Restaurant rent."),
        "ADP PAYROLL FEES NET PAY": _t(OPEX, "Wages", "known", "Kitchen staff net pay (the gross and the taxes are not in the bank)."),
        "CINTAS CORP LINEN SVC": _t(OPEX, "Linens and laundry", "known", "Linen service."),
        "DOORDASH INC PAYMENT": _t(REVENUE, "Gross receipts or sales", "known", "Delivery sales paid out net of DoorDash fees."),
        "ECOLAB PEST ELIM": _t(OPEX, "Repairs and maintenance", "known", "Pest control service."),
        "MONTHLY SERVICE FEE": _t(OPEX, "Bank fees", "known", "Bank fee (this book has no rule for it)."),
        "RESTAURANT DEPOT": _t(COGS, "Purchases – food", "known", "Food for the kitchen."),
        "SMART & FINAL": _t(COGS, "Purchases – food", "known", "Food for the kitchen."),
    },
}


def _truth_errors(errors: dict[str, list[Planted]]) -> list[dict]:
    """Las filas EXACTAS de cada error plantado, sacadas de los mismos generadores (deterministas):
    fecha, descripción, monto y dirección, en el orden del libro."""
    out = []
    for name, _sector, _account, make, _rules, _plant in BOOKS:
        rows = sorted(make().rows, key=lambda r: (r[0], r[1]))
        for spec in errors.get(name, []):
            if not spec.truth:
                continue
            picked = [rows[i] for i in _pick(rows, spec)]
            out.append({
                "book": name, "type": spec.type, "detail": spec.detail, "story": spec.story,
                "expected": ({"account_type": spec.expected[0].value, "category": spec.expected[1]}
                             if spec.expected else None),
                "rows": [{"date": d.isoformat(), "description": desc, "amount": float(abs(a)), "is_inflow": a > 0}
                         for d, desc, a, _tag in picked],
            })
    return out


# DEMO_TRUTH_ERRORS: los errores plantados para el revisor ①, uno por entrada (puede cubrir varias filas).
DEMO_TRUTH_ERRORS: list[dict] = _truth_errors(ERRORS)

# DEMO_TRUTH_ERRORS_BLIND: el segundo juego, a ciegas (14-oct). Misma forma. La F11 lo mide sin abrirlo antes.
DEMO_TRUTH_ERRORS_BLIND: list[dict] = _truth_errors(ERRORS_BLIND)


# ── EL TIPO DE NEGOCIO DE CADA COMERCIO (16-oct, para el experimento del ② / Tavily) ─────────────────
# La respuesta correcta para cada comercio de GASTO de los 3 libros: las salidas, sin personas, cheques,
# transferencias ni cargos del banco. La escribió Movimientos ANTES de mirar lo que devolvió Tavily (no se leyó
# `demo_web_evidence.json`). Formato del pedido de la F11 (Buzón, 16-oct): {libro: {comienzo de la descripción:
# tipo}}, con la misma clase de llave que `DEMO_TRUTH_PENDING`.
#   tipo = uno de `merchant_verifier.BUSINESS_TYPES` (la lista cerrada, sin cambiarla), o
#   "no_type"   → un negocio claro, pero ningún tipo de la lista le corresponde;
#   "ambiguous" → no hay una respuesta honesta (más de un tipo es verdad, o vende de todo).
# Cada llave caza SOLO las líneas de su comercio en su libro (comprobado al escribirla). El porqué, en el comentario.
DEMO_TRUTH_MERCHANT_TYPES: dict[str, dict[str, str]] = {
    "Rivera HVAC LLC": {
        "AMAZON": "ambiguous",                                 # Amazon.com y su Marketplace: venden de todo
        "ACH DEBIT BAYSIDE PROPERTIES LLC RENT": "property_management",  # la empresa del arrendador (el tipo más cercano)
        "CHEVRON": "gas_station",
        "FERGUSON HVAC SUPPLY": "building_supplies",           # distribuidor de plomería y HVAC
        "GRAINGER": "industrial_supplies",                     # W.W. Grainger: suministros industriales (MRO)
        "THE HOME DEPOT": "hardware_store",
        "IRS USATAXPYMT": "no_type",                           # un impuesto (EFTPS) al gobierno, no un negocio
        "JOHNSTONE SUPPLY": "building_supplies",               # repuestos y equipos de HVAC/R
        "LOWE'S": "hardware_store",
        "NETFLIX": "streaming_entertainment",
        "PGANDE WEB ONLINE": "utility",                        # Pacific Gas and Electric
        "SEPHORA": "no_type",                                  # belleza y cosméticos: no hay ese tipo
        "SHELL OIL": "gas_station",
        "STARBUCKS": "coffee_shop",                            # las tiendas de aquí y las del viaje
        "STATE FARM INSURANCE": "insurance",
        "TARGET": "department_store",                          # mercancía general ("discount retailer")
        "UNITED RENTALS": "no_type",                           # alquiler de equipos: no hay ese tipo
        "VERIZON WIRELESS": "telecom",
    },
    "Sparkle Cleaning Co.": {
        "24 HOUR FITNESS": "gym_fitness",
        "SQ *BAYCITY UNIFORMS": "uniforms_apparel",
        "SQ *BRIGHT MOPS CO": "cleaning_supplies",
        "COSTCO WHSE": "warehouse_club",
        "TST* EL FAROLITO": "restaurant",                      # taquería
        "SQ *JANITEX SUPPLY": "cleaning_supplies",             # suministros de limpieza (por el nombre)
        "NEXT INSURANCE": "insurance",
        "NORDSTROM RACK": "department_store",                  # tienda por departamentos "off-price"
        "PETCO": "no_type",                                    # mascotas: no hay ese tipo
        "SAFEWAY": "grocery",
        "SHELL OIL": "gas_station",
        "SPOTIFY": "streaming_entertainment",                  # música por streaming
        "T-MOBILE": "telecom",
        "THUMBTACK": "no_type",                                # vende clientes potenciales (publicidad): no hay ese tipo
        "PAYPAL *ULINE": "packaging_shipping",                 # empaque y envíos (también vende limpieza, pero ese es su negocio)
        "GOOGLE *WORKSPACE": "software_saas",
        "PAYPAL *ZOROTOOLS": "industrial_supplies",            # Zoro: herramientas y MRO en línea
    },
    "Casa Lupita Taqueria": {
        "ADP PAYROLL": "payroll_hr",
        "AMC": "streaming_entertainment",                      # cine (la lista pone ahí los cines)
        "BEST BUY": "consumer_electronics",
        "CINTAS": "linen_laundry",                             # renta de uniformes y mantelería (la línea dice LINEN SVC)
        "COMCAST BUSINESS": "telecom",                         # internet y teléfono
        "DISNEY PLUS": "streaming_entertainment",
        "ECOLAB PEST": "pest_control",                         # la división de control de plagas de Ecolab
        "HOBART SERVICE": "no_type",                           # reparación de equipo de cocina: no hay tipo de reparación
        "ACH DEBIT MISSION ST PROPERTIES RENT": "property_management",  # la empresa del arrendador (el tipo más cercano)
        "PGANDE WEB ONLINE": "utility",                        # Pacific Gas and Electric
        "RESTAURANT DEPOT": "restaurant_supply",               # mayorista para restaurantes
        "SAFEWAY": "grocery",
        "SFPUC WATER": "utility",                              # el agua de San Francisco
        "SMART & FINAL": "ambiguous",                          # supermercado tipo bodega: grocery y warehouse_club son verdad
        "SYSCO": "restaurant_supply",                          # distribuidor de alimentos para restaurantes
        "ULTA BEAUTY": "no_type",                              # belleza y cosméticos: no hay ese tipo
        "WALMART SUPERCENTER": "ambiguous",                    # vende de todo: tienda por departamentos y supermercado
        "WEBSTAURANTSTORE": "restaurant_supply",               # equipo y suministros para restaurantes, en línea
    },
}
