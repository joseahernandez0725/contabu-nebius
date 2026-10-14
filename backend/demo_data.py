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
   `transaction.company_id`, `categorizationchange.company_id/transaction_id`): la limpieza de 7 días
   la borra sola.
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
            elif spec.action[0] == "drift":   # la regla se editó y esta fila quedó atrás
                tx.category = spec.action[1]
                session.add(tx)


def _seed_book(session: Session, firm: Firm, owner: User, name: str, sector: str, account: str,
               book: Book, rules: list[tuple], plant: Callable) -> Company:
    company = Company(name=name, sector=sector, user_id=owner.id, firm_id=firm.id)
    session.add(company)
    session.flush()
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
    _apply_errors(session, owner, book.rows, ids, ERRORS.get(name, []))
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
    return b


_RIVERA_RULES = [
    ("INTUIT QB PAYMENTS", REVENUE, "Construction Revenue", "QuickBooks Payments"),
    ("STARBUCKS", OPEX, "Meals", "Starbucks"),
    ("STATE FARM", OPEX, "Insurance", "State Farm"),
    ("VERIZON", OPEX, "Telephone & Internet", "Verizon"),
    ("PG&E", OPEX, "Utilities", "PG&E"),
    ("MONTHLY SERVICE FEE", OPEX, "Bank Fees", "Bank"),
    # Dos reglas que chocan: "AMAZON MKTPL" cae también en "AMAZON" con otra categoría.
    ("AMAZON", OPEX, "Office Expense", "Amazon"),
    ("AMAZON MKTPL", COGS, "Small Tools & Equipment", "Amazon Marketplace"),
]


def _plant_rivera(session, company, rules, tagged, owner) -> None:
    # SHELL de dos formas: la mitad a mano como combustible del vehículo, la otra como del equipo.
    for i, tx in enumerate(tagged["shell"]):
        at, cat = (OPEX, "Vehicle Fuel") if i % 2 == 0 else (COGS, "Equipment Fuel")
        _manual(session, tx, at, cat, "Shell", owner, datetime.combine(tx.date + timedelta(days=3),
                                                                        datetime.min.time()))
    # LOWE'S: el bookkeeper lo puso en Office y lo corrigió a Materials, las 3 veces.
    for tx in tagged["lowes"]:
        first = datetime.combine(tx.date + timedelta(days=2), datetime.min.time())
        _manual(session, tx, OPEX, "Office Expense", "Lowe's", owner, first)
        session.flush()
        _manual(session, tx, COGS, "Materials", "Lowe's", owner, first + timedelta(days=9))
    # Una fila que SE SALIÓ de su regla: dice que la decidió "PG&E", pero su categoría ya no es la de
    # la regla (la regla se editó después y esta fila quedó atrás) → el auditor la marca.
    stray = tagged["pge"][0]
    stray.category = "Office Expense"
    session.add(stray)
    # El historial de un trimestre: lo que el bookkeeper fue resolviendo a mano. HOME DEPOT una vez
    # como herramienta (se contradice), la renta en julio y agosto, la nómina, los Zelle al dueño.
    _history(session, company, owner, [
        ("CHEVRON", [(0, OPEX, "Vehicle Fuel", "Chevron"), (4, OPEX, "Vehicle Fuel", "Chevron"),
                     (9, OPEX, "Vehicle Fuel", "Chevron")]),
        ("THE HOME DEPOT", [(0, COGS, "Materials", "Home Depot"), (3, COGS, "Small Tools & Equipment", "Home Depot"),
                            (7, COGS, "Materials", "Home Depot")]),
        ("FERGUSON", [(0, COGS, "Materials", "Ferguson"), (2, COGS, "Materials", "Ferguson")]),
        ("JOHNSTONE", [(1, COGS, "Materials", "Johnstone Supply")]),
        ("ACH DEBIT BAYSIDE", [(0, OPEX, "Office Rent", "Bayside Properties"), (1, OPEX, "Office Rent", "Bayside Properties")]),
        ("GUSTO PAYROLL", [(0, COGS, "Direct Labor", "Gusto"), (2, COGS, "Direct Labor", "Gusto"),
                           (5, OPEX, "Admin Wages", "Gusto")]),
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
    return b


_SPARKLE_RULES = [
    ("BAYVIEW HOA", REVENUE, "Cleaning Revenue", "Bayview HOA"),
    ("COSTCO", COGS, "Cleaning Supplies", "Costco"),
    ("SHELL", OPEX, "Vehicle Fuel", "Shell"),
    ("NEXT INSURANCE", OPEX, "Insurance", "Next Insurance"),
    ("MONTHLY SERVICE FEE", OPEX, "Bank Fees", "Bank"),
]


def _plant_sparkle(session, company, rules, tagged, owner) -> None:
    # El gimnasio, cargado a mano como gasto de oficina (un gasto personal camuflado).
    for tx in tagged["gym"]:
        _manual(session, tx, OPEX, "Office Expense", "24 Hour Fitness", owner,
                datetime.combine(tx.date + timedelta(days=4), datetime.min.time()), reason="planted_mistake")
    _history(session, company, owner, [
        ("TST* EL FAROLITO", [(0, OPEX, "Meals", "El Farolito"), (3, OPEX, "Meals", "El Farolito")]),
        ("SQ *JANITEX", [(0, COGS, "Cleaning Supplies", "Janitex Supply"), (2, COGS, "Cleaning Supplies", "Janitex Supply")]),
        ("PAYPAL *ULINE", [(0, COGS, "Cleaning Supplies", "Uline"), (2, OPEX, "Office Expense", "Uline")]),
        ("T-MOBILE", [(0, OPEX, "Telephone & Internet", "T-Mobile"), (2, OPEX, "Telephone & Internet", "T-Mobile")]),
        ("ZELLE PAYMENT TO MARIA LOPEZ", [(0, COGS, "Subcontractors", "Maria Lopez"), (2, COGS, "Subcontractors", "Maria Lopez"),
                                          (5, COGS, "Direct Labor", "Maria Lopez")]),
        ("SQ DEP", [(0, REVENUE, "Cleaning Revenue", "Square"), (6, REVENUE, "Cleaning Revenue", "Square")]),
    ])


# ── Casa Lupita Taqueria (restaurante) ──────────────────────────────────────────────────────────
# La nota "más de $50 en supermercado suele ser personal" y compras de supermercado que la cruzan
# (SAFEWAY y WALMART, por regla como Food Purchases), y una regla vieja que ya no agarra nada.

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
    return b


_LUPITA_RULES = [
    ("SQUARE INC", REVENUE, "Food & Beverage Sales", "Square"),
    ("SYSCO", COGS, "Food Purchases", "Sysco"),
    ("SAFEWAY", COGS, "Food Purchases", "Safeway"),
    ("WALMART", COGS, "Food Purchases", "Walmart"),
    ("PG&E", OPEX, "Utilities", "PG&E"),
    # La regla vieja: el proveedor de verduras cerró y ya no aparece en el banco.
    ("CHEVYS FRESH PRODUCE", COGS, "Food Purchases", "Chevys Fresh Produce"),
]


def _plant_lupita(session, company, rules, tagged, owner) -> None:
    _history(session, company, owner, [
        ("RESTAURANT DEPOT", [(0, COGS, "Food Purchases", "Restaurant Depot"), (4, COGS, "Food Purchases", "Restaurant Depot"),
                              (9, COGS, "Paper & Packaging", "Restaurant Depot")]),
        ("CINTAS", [(0, OPEX, "Linens", "Cintas"), (3, OPEX, "Linens", "Cintas")]),
        ("ADP PAYROLL", [(0, OPEX, "Kitchen Wages", "ADP"), (3, OPEX, "Kitchen Wages", "ADP")]),
        ("ECOLAB", [(0, OPEX, "Contract Cleaning", "Ecolab"), (2, OPEX, "Repairs & Maintenance", "Ecolab")]),
        ("ACH DEBIT MISSION ST", [(0, OPEX, "Rent", "Mission St Properties"), (1, OPEX, "Rent", "Mission St Properties")]),
        ("SMART & FINAL", [(1, COGS, "Food Purchases", "Smart & Final")]),
    ])


_FOREIGN = lambda r: r[1].split()[-1] in ("FR", "ES", "IT")  # noqa: E731
_OVER_50 = lambda r: -r[2] >= 50  # noqa: E731

ERRORS: dict[str, list[Planted]] = {
    "Rivera HVAC LLC": [
        Planted("rule_deviation", "Decided by the PG&E rule but no longer carries the rule's category (the rule was edited later).",
                "PGANDE WEB ONLINE PG&E", [0], expected=(OPEX, "Utilities")),
        Planted("merchant_two_categories", "Shell is service-van fuel; half the rows were typed as equipment fuel.",
                "SHELL OIL 57444", [1, 3, 5], expected=(OPEX, "Vehicle Fuel")),
        Planted("personal_expense", "Coffee bought in Europe during the owner's vacation (Aug 3–17): personal, not Meals.",
                "STARBUCKS", _FOREIGN, expected=(NON_PL, "Owner Draw"), story="vacation_note"),
        Planted("sign_mismatch", "QuickBooks Payments processing FEE (money out) caught by the revenue rule: it is a bank fee.",
                "INTUIT QB PAYMENTS FEE", expected=(OPEX, "Bank Fees")),
        Planted("sign_mismatch", "A customer paid by Zelle (money IN) and it was typed as a subcontractor expense.",
                "ZELLE PAYMENT FROM MARCUS OKAFOR", action=("manual", COGS, "Subcontractors", "Marcus Okafor"),
                expected=(REVENUE, "Construction Revenue")),
        Planted("unusual_amount", "$4,850 at Home Depot when it is usually $85–940: equipment? personal? a duplicate? Ask.",
                "THE HOME DEPOT #0628", lambda r: -r[2] > 2000, action=("manual", COGS, "Materials", "Home Depot")),
        Planted("unusual_amount", "Verizon is $148.32 every month; one extra charge of $612.40 (new phones?).",
                "VERIZON WIRELESS", lambda r: -r[2] > 300),
        Planted("personal_expense", "Netflix booked as a business subscription.",
                "NETFLIX.COM", action=("manual", OPEX, "Dues & Subscriptions", "Netflix"), expected=(NON_PL, "Owner Draw")),
        Planted("personal_expense", "A Target run (household items) typed as office expense.",
                "TARGET T-2771", action=("manual", OPEX, "Office Expense", "Target"), expected=(NON_PL, "Owner Draw")),
        Planted("payroll_net_only", "Payroll shows only NET pay (Gusto) and the IRS remittances: gross wages and the employer tax split are missing.",
                "GUSTO PAYROLL NET PAY"),
        Planted("payroll_net_only", "IRS payroll-tax remittances: part employee withholding (wages), part employer tax.",
                "IRS USATAXPYMT"),
    ],
    "Sparkle Cleaning Co.": [
        Planted("personal_expense", "Gym membership typed as office expense.",
                "24 HOUR FITNESS", expected=(NON_PL, "Owner Draw")),
        Planted("merchant_two_categories", "Uline is cleaning supplies; one row was typed as office expense.",
                "PAYPAL *ULINE", [2], expected=(COGS, "Cleaning Supplies")),
        Planted("sign_mismatch", "A client paid by Zelle (money IN) and it was typed as a subcontractor expense.",
                "ZELLE PAYMENT FROM BRIGHTWAY REALTY", action=("manual", COGS, "Subcontractors", "Brightway Realty"),
                expected=(REVENUE, "Cleaning Revenue")),
        Planted("unusual_amount", "T-Mobile is $95 a month; a $389 charge (a phone?).",
                "T-MOBILE PCS SVC", lambda r: -r[2] > 200, action=("manual", OPEX, "Telephone & Internet", "T-Mobile")),
        Planted("personal_expense", "Clothes from Nordstrom Rack typed as uniforms.",
                "NORDSTROM RACK", action=("manual", OPEX, "Uniforms", "Nordstrom Rack"), expected=(NON_PL, "Owner Draw")),
        Planted("personal_expense", "Groceries at Safeway typed as cleaning supplies (supplies come from Costco).",
                "SAFEWAY #2213", action=("manual", COGS, "Cleaning Supplies", "Safeway"), expected=(NON_PL, "Owner Draw")),
        Planted("rule_deviation", "Decided by the COSTCO rule but carries Office Expense (the rule changed later).",
                "COSTCO WHSE", [2], action=("drift", "Office Expense"), expected=(COGS, "Cleaning Supplies")),
        Planted("merchant_two_categories", "Shell is fuel for the van (rule: Vehicle Fuel); one row typed as Transportation.",
                "SHELL OIL 12093", [3], action=("manual", COGS, "Transportation", "Shell"), expected=(OPEX, "Vehicle Fuel")),
        Planted("unusual_amount", "$1,940 at Janitex when it is usually $140–520: a bulk order, or a mistake?",
                "SQ *JANITEX", lambda r: -r[2] > 1000, action=("manual", COGS, "Cleaning Supplies", "Janitex Supply")),
    ],
    "Casa Lupita Taqueria": [
        Planted("personal_expense", "Supermarket purchases over $50 (the bookkeeper's note): usually personal.",
                "SAFEWAY #1711", _OVER_50, expected=(NON_PL, "Owner Draw"), story="supermarket_note"),
        Planted("personal_expense", "Supermarket purchases over $50 (the bookkeeper's note): usually personal.",
                "WALMART SUPERCENTER", _OVER_50, expected=(NON_PL, "Owner Draw"), story="supermarket_note"),
        Planted("merchant_two_categories", "Restaurant Depot is food purchases; one row was typed as paper & packaging.",
                "RESTAURANT DEPOT", [9], expected=(COGS, "Food Purchases")),
        Planted("merchant_two_categories", "Ecolab pest control typed two ways; the contract-cleaning row is the odd one.",
                "ECOLAB", [0], expected=(OPEX, "Repairs & Maintenance")),
        Planted("sign_mismatch", "Square Capital LOAN payment (money out) caught by the SQUARE INC sales rule.",
                "SQUARE INC LOAN PMT", expected=(NON_PL, "Loan Principal Payment")),
        Planted("unusual_amount", "PG&E is $410–690 a month; one $1,840 bill (past-due catch-up?).",
                "PGANDE WEB ONLINE PG&E", lambda r: -r[2] > 1000),
        Planted("unusual_amount", "Sysco is $620–1,480 a week; one $4,920 order.",
                "SYSCO SAN FRANCISCO", lambda r: -r[2] > 3000),
        Planted("personal_expense", "Best Buy (a TV?) typed as smallwares.",
                "BEST BUY", action=("manual", OPEX, "Smallwares", "Best Buy"), expected=(NON_PL, "Owner Draw")),
        Planted("personal_expense", "Movie tickets typed as marketing.",
                "AMC METREON", action=("manual", OPEX, "Marketing", "AMC"), expected=(NON_PL, "Owner Draw")),
        Planted("personal_expense", "Disney+ booked as a business subscription.",
                "DISNEY PLUS", action=("manual", OPEX, "Dues & Subscriptions", "Disney+"), expected=(NON_PL, "Owner Draw")),
        Planted("rule_deviation", "Decided by the SYSCO rule but carries Paper & Packaging (the rule changed later).",
                "SYSCO SAN FRANCISCO", [6], action=("drift", "Paper & Packaging"), expected=(COGS, "Food Purchases")),
        Planted("payroll_net_only", "Payroll shows only NET pay (ADP) and NO tax remittance at all: gross wages and payroll taxes are missing.",
                "ADP PAYROLL"),
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
        "ACH DEBIT BAYSIDE PROPERTIES LLC RENT": _t(OPEX, "Office Rent", "known", "Shop / office rent."),
        "CHEVRON": _t(OPEX, "Vehicle Fuel", "known", "Service-van fuel."),
        "FERGUSON HVAC SUPPLY": _t(COGS, "Materials", "known", "HVAC parts for jobs."),
        "JOHNSTONE SUPPLY": _t(COGS, "Materials", "known", "HVAC parts for jobs."),
        "THE HOME DEPOT": _t(COGS, "Materials", "known", "Job materials (one $4,850 row is a planted unusual amount)."),
        "GUSTO PAYROLL NET PAY": _t(COGS, "Direct Labor", "known", "Technicians' net pay (the gross is not in the bank)."),
        "IRS USATAXPYMT": _t(OTHER_EXPENSE, "Taxes & Licenses", "split",
                             "Payroll-tax remittance: employee withholding (part of wages) + employer tax."),
        "MOBILE DEPOSIT REF": _t(None, None, "ambiguous", "A deposited check: probably a customer, but could be a loan or the owner."),
        "ZELLE PAYMENT TO DANIEL RIVERA": _t(NON_PL, "Owner Draw", "known", "The owner paying himself."),
    },
    "Sparkle Cleaning Co.": {
        "ACH CREDIT OAKMONT PROPERTY MGMT": _t(REVENUE, "Cleaning Revenue", "known", "A client's monthly contract."),
        "SQ DEP": _t(REVENUE, "Cleaning Revenue", "known", "Card sales paid out by Square."),
        "PAYPAL *ULINE": _t(COGS, "Cleaning Supplies", "known", "Supplies."),
        "PAYPAL *ZOROTOOLS": _t(COGS, "Small Tools & Equipment", "known", "Tools."),
        "SQ *BAYCITY UNIFORMS": _t(OPEX, "Uniforms", "known", "Staff uniforms."),
        "SQ *JANITEX SUPPLY": _t(COGS, "Cleaning Supplies", "known", "Janitorial supplies."),
        "T-MOBILE PCS SVC": _t(OPEX, "Telephone & Internet", "known", "Business phone line."),
        "TST* EL FAROLITO": _t(OPEX, "Meals", "known", "Crew lunch."),
        "ZELLE PAYMENT TO MARIA LOPEZ": _t(None, None, "ambiguous", "A person: subcontractor or employee? Only the client knows."),
        "ZELLE PAYMENT TO ANA GUTIERREZ": _t(None, None, "ambiguous", "A person: subcontractor or employee? Only the client knows."),
        "ZELLE PAYMENT TO JOSE MARTINEZ": _t(None, None, "ambiguous", "A person: subcontractor or employee? Only the client knows."),
    },
    "Casa Lupita Taqueria": {
        "ACH DEBIT MISSION ST PROPERTIES RENT": _t(OPEX, "Rent", "known", "Restaurant rent."),
        "ADP PAYROLL FEES NET PAY": _t(OPEX, "Kitchen Wages", "known", "Kitchen staff net pay (the gross and the taxes are not in the bank)."),
        "CINTAS CORP LINEN SVC": _t(OPEX, "Linens", "known", "Linen service."),
        "DOORDASH INC PAYMENT": _t(REVENUE, "Food & Beverage Sales", "known", "Delivery sales paid out net of DoorDash fees."),
        "ECOLAB PEST ELIM": _t(OPEX, "Repairs & Maintenance", "known", "Pest control service."),
        "MONTHLY SERVICE FEE": _t(OPEX, "Bank Fees", "known", "Bank fee (this book has no rule for it)."),
        "RESTAURANT DEPOT": _t(COGS, "Food Purchases", "known", "Food for the kitchen."),
        "SMART & FINAL": _t(COGS, "Food Purchases", "known", "Food for the kitchen."),
    },
}


def _truth_errors() -> list[dict]:
    """Las filas EXACTAS de cada error plantado, sacadas de los mismos generadores (deterministas):
    fecha, descripción, monto y dirección, en el orden del libro."""
    out = []
    for name, _sector, _account, make, _rules, _plant in BOOKS:
        rows = sorted(make().rows, key=lambda r: (r[0], r[1]))
        for spec in ERRORS.get(name, []):
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
DEMO_TRUTH_ERRORS: list[dict] = _truth_errors()
