"""
ContaBu — EL PLAN DE CUENTAS: los planes POR DEFECTO y el plan de CADA LIBRO.

El plan cierra el vocabulario: la categoría de una fila tiene que estar en el plan de SU libro, para
que el P&L sume una sola cuenta por concepto (agrupa por el texto) y para que el agente que redacta
reglas (④) solo proponga cuentas que existen. Ni la IA ni nadie categoriza desde aquí: la categoría la
ponen una regla o el bookkeeper (`CLAUDE.md`, regla 2).

Dos piezas (18-oct-2026, Movimientos; decisiones 🧾 y 🏛️ del tablero):

1. **Los planes por defecto** (este archivo). Cada libro dice CÓMO TRIBUTA (`Company.tax_form`):
   - `schedule_c` — "Sole proprietor / LLC — Schedule C": un plan por sector (`SCHEDULE_C`);
   - `1120s` — "S corp / LLC — Form 1120-S": UN plan general para cualquier S corp (`FORM_1120S`).
   El NOMBRE es solo lo que la cuenta es (Jose, 19-oct): el de su línea oficial, con "– detalle" solo si
   tiene línea PROPIA y la comparte con otra cuenta ("Taxes and licenses – permits"; "Payroll taxes", que
   comparte la de Taxes and licenses, se llama solo así: Jose, 19-oct); lo que no tiene
   línea propia se llama por lo que es ("Software", "Bank fees") y su línea guardada dice dónde va (la
   Parte V en Schedule C, la 20 en 1120-S). Sin prefijos "Other expenses –" / "Other deductions –": la
   línea va aparte. La SECCIÓN la decide el bookkeeper. Cada categoría trae su LÍNEA DEL IRS con el formulario (`SchC:9`, `1120S:20`, `1125A:5`,
   `K:16d`, `L:19`…), o ninguna si va fuera del P&L.

2. **El plan de cada libro** (tabla `companycategory`): al elegir sector y formulario el libro recibe SU
   copia del plan por defecto, y desde ahí el bookkeeper renombra, agrega, mueve de sección o quita
   (archiva si está en uso). `categories_for_company` es lo que leen el motor, el selector de la Hoja,
   el validador de la fórmula y los agentes: `{sección: [categorías activas]}`.

Fuente contable: `docs/REFERENCIA CONTABLE.md` (la mantiene el chat central).
"""

from datetime import datetime, timezone
from typing import Optional

TAX_FORMS = ("schedule_c", "1120s")
DEFAULT_TAX_FORM = "schedule_c"

# Orden estable de las secciones del P&L.
_SECTIONS = ["REVENUE", "COGS", "OPERATING_EXPENSE", "OTHER_INCOME", "OTHER_EXPENSE", "NON_PL"]

# ── Las LÍNEAS del IRS que puede llevar una categoría, por formulario ─────────────────────────────
# (código, nombre en el formulario). El nombre es un dato del IRS: va en inglés, como las categorías.
# ✅ Las del pedido del 18-oct y la Parte III (36 Purchases less cost of items withdrawn for personal use ·
# 37 Cost of labor · 38 Materials and supplies · 39 Other costs), leídas por el chat central en los PDF 2025.
# 🟡 De memoria, sin leer el PDF: SchC 2, 10, 14, 16a y 19. Lo que no sale del banco (depreciación,
# agotamiento, deudas incobrables en base caja, la deducción de eficiencia energética) no entra.
LINES: dict[str, list[tuple[str, str]]] = {
    "schedule_c": [
        ("SchC:1", "Gross receipts or sales"),
        ("SchC:2", "Returns and allowances"),
        ("SchC:6", "Other income"),
        ("SchC:8", "Advertising"),
        ("SchC:9", "Car and truck expenses"),
        ("SchC:10", "Commissions and fees"),
        ("SchC:11", "Contract labor"),
        ("SchC:14", "Employee benefit programs"),
        ("SchC:15", "Insurance (other than health)"),
        ("SchC:16a", "Interest: mortgage"),
        ("SchC:16b", "Interest: other"),
        ("SchC:17", "Legal and professional services"),
        ("SchC:18", "Office expense"),
        ("SchC:19", "Pension and profit-sharing plans"),
        ("SchC:20a", "Rent or lease: vehicles, machinery, and equipment"),
        ("SchC:20b", "Rent or lease: other business property"),
        ("SchC:21", "Repairs and maintenance"),
        ("SchC:22", "Supplies"),
        ("SchC:23", "Taxes and licenses"),
        ("SchC:24a", "Travel"),
        ("SchC:24b", "Deductible meals"),
        ("SchC:25", "Utilities"),
        ("SchC:26", "Wages"),
        ("SchC:PartV", "Other expenses"),
        ("SchC:36", "Purchases"),
        ("SchC:37", "Cost of labor"),
        ("SchC:38", "Materials and supplies"),
        ("SchC:39", "Other costs"),
    ],
    "1120s": [
        ("1120S:1a", "Gross receipts or sales"),
        ("1120S:1b", "Returns and allowances"),
        ("1120S:5", "Other income (loss)"),
        ("1120S:7", "Compensation of officers"),
        ("1120S:8", "Salaries and wages"),
        ("1120S:9", "Repairs and maintenance"),
        ("1120S:11", "Rents"),
        ("1120S:12", "Taxes and licenses"),
        ("1120S:13", "Interest"),
        ("1120S:16", "Advertising"),
        ("1120S:17", "Pension, profit-sharing, etc., plans"),
        ("1120S:18", "Employee benefit programs"),
        ("1120S:20", "Other deductions"),
        ("1125A:2", "Purchases"),
        ("1125A:3", "Cost of labor"),
        ("1125A:5", "Other costs"),
        ("K:4", "Interest income"),
        ("K:12a", "Charitable contributions"),
        ("K:16c", "Nondeductible expenses"),
        ("K:16d", "Distributions"),
        ("L:7", "Loans to shareholders"),
        ("L:19", "Loans from shareholders"),
        ("L:23", "Additional paid-in capital"),
    ],
}
_VALID_LINES = {code: form for form, lines in LINES.items() for code, _ in lines}


def line_form(code: Optional[str]) -> Optional[str]:
    """El formulario (`schedule_c` | `1120s`) al que pertenece una línea; None si no es válida."""
    return _VALID_LINES.get(code or "")


def suggested_line(tax_form: str, section: str) -> Optional[str]:
    """La línea que el editor propone al AGREGAR una categoría (el bookkeeper la cambia a mano).
    Schedule C: un gasto sin línea propia → la Parte V (el formulario lo lista con su nombre y lo suma
    en la 27b); un ingreso → la 1 o la 6. 1120-S: un gasto → la 20 (Other deductions); un ingreso → la
    1a o la 5; y un costo directo → la 5 del 1125-A (Other costs). Fuera del P&L: ninguna."""
    if tax_form == "1120s":
        return {"REVENUE": "1120S:1a", "OTHER_INCOME": "1120S:5", "COGS": "1125A:5",
                "OPERATING_EXPENSE": "1120S:20", "OTHER_EXPENSE": "1120S:20"}.get(section)
    return {"REVENUE": "SchC:1", "OTHER_INCOME": "SchC:6", "COGS": "SchC:PartV",
            "OPERATING_EXPENSE": "SchC:PartV", "OTHER_EXPENSE": "SchC:PartV"}.get(section)


# ── Los PLANES POR DEFECTO ─────────────────────────────────────────────────────────────────────────
# Cada entrada: (nombre, línea). Por sección. Ver la regla del NOMBRE arriba.

# NON_PL: lo que nunca entra al P&L. `Transfer`, `Credit Card Payment` y `Loan Principal Payment` los
# escribe el detector de transferencias (`rules.classify_transfer`): por eso no se renombran, no se
# mueven ni se quitan (`LOCKED`).
_NON_PL_COMMON = [("Transfer", None), ("Loan Proceeds", None), ("Loan Principal Payment", None),
                  ("Credit Card Payment", None),
                  # Lo que pase de $2,500 (el de minimis del IRS): se deprecia en el Form 4562.
                  ("Equipment purchase (depreciable asset)", None)]
LOCKED = frozenset({"Transfer", "Credit Card Payment", "Loan Principal Payment"})

# Schedule C, lo COMÚN a todos los sectores (gastos de operación, otros ingresos y gastos, fuera del P&L).
_SC_COMMON: dict[str, list[tuple[str, Optional[str]]]] = {
    "OPERATING_EXPENSE": [
        ("Advertising", "SchC:8"),
        ("Car and truck expenses", "SchC:9"),
        ("Contract labor", "SchC:11"),                   # 1099 que no son de obra
        ("Employee benefit programs", "SchC:14"),        # seguro médico y beneficios de los empleados
        ("Insurance", "SchC:15"),                        # la línea dice "(other than health)": eso va en la ayuda
        ("Legal and professional services", "SchC:17"),
        ("Office expense", "SchC:18"),
        ("Rent or lease – vehicles, machinery, and equipment", "SchC:20a"),
        ("Rent or lease – other business property", "SchC:20b"),
        ("Repairs and maintenance", "SchC:21"),
        ("Supplies", "SchC:22"),
        ("Taxes and licenses", "SchC:23"),
        ("Payroll taxes", "SchC:23"),                    # la línea es la de Taxes and licenses (Jose, 19-oct)
        ("Travel", "SchC:24a"),
        ("Deductible meals", "SchC:24b"),
        ("Utilities", "SchC:25"),
        # La nómina va en gastos de operación: llega NETA en un solo pago y no se puede dividir entre
        # cuadrilla y oficina (quien quiera el estándar de obra, la mueve a COGS).
        ("Wages", "SchC:26"),
        ("Software", "SchC:PartV"),
        ("Bank fees", "SchC:PartV"),
        ("Dues and subscriptions", "SchC:PartV"),
        ("Miscellaneous", "SchC:PartV"),  # la usan las oficinas; no es línea del IRS
    ],
    "OTHER_INCOME": [("Interest income", "SchC:6"),
                     ("Refunds and rebates", "SchC:6")],
    "OTHER_EXPENSE": [("Interest – other", "SchC:16b")],
    "NON_PL": [("Owner Draw", None), ("Owner Contribution", None)] + _NON_PL_COMMON,
}

# Lo PROPIO de cada sector: ingresos, costos directos (COGS) y gastos de operación extra. En COGS va lo
# que se revende, queda en el trabajo o el banco deja reconocer como costo directo (un restaurante y una
# tienda sí tienen inventario → la Parte III).
# ✅ construction: aprobado por Jose (18-oct, opción C). 🟡 Los demás: PROPUESTA de Movimientos, a la
# espera de que Jose la apruebe (tabla antes → después en `docs/FASE 10.A.md`, 18-oct).
_SC_SECTOR: dict[str, dict[str, list[tuple[str, Optional[str]]]]] = {
    "construction": {
        "REVENUE": [("Gross receipts or sales", "SchC:1")],
        "COGS": [
            ("Materials", "SchC:22"),                      # o la Parte III, línea 38, si el preparador la usa
            ("Contract labor – subcontractors", "SchC:11"),
            ("Rent or lease – job equipment", "SchC:20a"),
            ("Taxes and licenses – permits", "SchC:23"),
            ("Equipment fuel", "SchC:PartV"),
            ("Trucking and hauling", "SchC:PartV"),
            ("Dumping and disposal", "SchC:PartV"),
        ],
    },
    "cleaning": {
        "REVENUE": [("Gross receipts or sales", "SchC:1")],
        "COGS": [("Supplies – cleaning supplies", "SchC:22"),
                 ("Contract labor – subcontractors", "SchC:11")],
        "OPERATING_EXPENSE": [("Uniforms", "SchC:PartV")],
    },
    "restaurant": {
        "REVENUE": [("Gross receipts or sales", "SchC:1")],
        "COGS": [("Purchases – food", "SchC:36"),
                 ("Purchases – beverages", "SchC:36"),
                 ("Purchases – beer, wine, and liquor", "SchC:36"),
                 ("Materials and supplies", "SchC:38")],          # papel y empaques para llevar
        "OPERATING_EXPENSE": [("Supplies – smallwares", "SchC:22"),
                              ("Linens and laundry", "SchC:PartV"),
                              ("Cleaning services", "SchC:PartV")],
    },
    "landscaping": {
        "REVENUE": [("Gross receipts or sales", "SchC:1")],
        "COGS": [("Materials", "SchC:22"),
                 ("Contract labor – subcontractors", "SchC:11"),
                 ("Rent or lease – job equipment", "SchC:20a"),
                 ("Equipment fuel", "SchC:PartV"),
                 ("Dumping and disposal", "SchC:PartV")],
        "OPERATING_EXPENSE": [("Uniforms", "SchC:PartV")],
    },
    "painting": {
        "REVENUE": [("Gross receipts or sales", "SchC:1")],
        "COGS": [("Materials", "SchC:22"),
                 ("Contract labor – subcontractors", "SchC:11"),
                 ("Rent or lease – job equipment", "SchC:20a"),
                 ("Taxes and licenses – permits", "SchC:23")],
    },
    "trucking": {
        # Dueño-operador: el combustible y el mantenimiento del camión SON el costo directo del flete.
        "REVENUE": [("Gross receipts or sales", "SchC:1")],
        "COGS": [("Car and truck expenses – fuel", "SchC:9"),
                 ("Repairs and maintenance – trucks", "SchC:21"),
                 ("Repairs and maintenance – tires", "SchC:21"),
                 ("Car and truck expenses – tolls and scales", "SchC:9"),
                 ("Rent or lease – trucks", "SchC:20a"),
                 ("Contract labor – drivers", "SchC:11"),
                 ("Commissions and fees", "SchC:10")],             # despacho y brokers
        "OPERATING_EXPENSE": [("Parking and storage", "SchC:PartV")],
    },
    "beauty": {
        "REVENUE": [("Gross receipts or sales – services", "SchC:1"),
                    ("Gross receipts or sales – products", "SchC:1")],
        "COGS": [("Supplies – salon products", "SchC:22"),
                 ("Contract labor – stylists", "SchC:11")],
        "OPERATING_EXPENSE": [("Uniforms and laundry", "SchC:PartV")],
    },
    "auto_repair": {
        "REVENUE": [("Gross receipts or sales – repairs", "SchC:1"),
                    ("Gross receipts or sales – parts", "SchC:1")],
        "COGS": [("Purchases", "SchC:36"),                         # repuestos que se revenden
                 ("Materials and supplies", "SchC:38"),           # insumos del taller
                 ("Contract labor – subcontractors", "SchC:11")],
        "OPERATING_EXPENSE": [("Uniforms", "SchC:PartV")],
    },
    "retail": {
        "REVENUE": [("Gross receipts or sales", "SchC:1")],
        "COGS": [("Purchases", "SchC:36"),
                 ("Materials and supplies", "SchC:38"),           # bolsas y empaques
                 ("Other costs", "SchC:39")],                     # flete de entrada
    },
}
# Para sectores no reconocidos ("other" y los viejos): un set genérico mínimo.
_SC_GENERIC: dict[str, list[tuple[str, Optional[str]]]] = {
    "REVENUE": [("Gross receipts or sales", "SchC:1")],
    "COGS": [("Materials", "SchC:22"), ("Contract labor – subcontractors", "SchC:11")],
}

# 1120-S: UN plan general para cualquier S corp (✅ aprobado por Jose, 18-oct). No entran como
# categoría: Bad debts (10; en base caja no aplica), Depreciation (14), Depletion (15) y Energy
# efficient… (19): no salen del banco y los calcula el preparador.
FORM_1120S: dict[str, list[tuple[str, Optional[str]]]] = {
    # ⚠️ "Returns and allowances" es una SALIDA dentro de REVENUE: el P&L suma magnitudes por sección y
    # hoy la sumaría en vez de restarla (📬 a Entregables, 18-oct).
    "REVENUE": [("Gross receipts or sales", "1120S:1a"),
                ("Returns and allowances", "1120S:1b"),
                ("Other income", "1120S:5")],
    "COGS": [("Purchases", "1125A:2"),
             ("Cost of labor", "1125A:3"),
             ("Subcontractors (1099)", "1125A:5"),
             ("Freight and shipping", "1125A:5")],
    "OPERATING_EXPENSE": [
        ("Compensation of officers", "1120S:7"),   # el sueldo del dueño y su seguro médico (va en su W-2)
        ("Salaries and wages", "1120S:8"),
        ("Repairs and maintenance", "1120S:9"),
        ("Rents", "1120S:11"),
        ("Taxes and licenses", "1120S:12"),
        ("Payroll taxes", "1120S:12"),                  # la línea es la de Taxes and licenses
        ("Advertising", "1120S:16"),
        ("Pension, profit-sharing, etc., plans", "1120S:17"),
        ("Employee benefit programs", "1120S:18"),
        ("Car and truck expenses", "1120S:20"),
        ("Insurance", "1120S:20"),
        ("Legal and professional fees", "1120S:20"),
        ("Office expense", "1120S:20"),
        ("Supplies", "1120S:20"),
        ("Utilities", "1120S:20"),
        ("Telephone and internet", "1120S:20"),
        ("Travel", "1120S:20"),
        ("Meals", "1120S:20"),
        ("Contract labor", "1120S:20"),
        ("Software", "1120S:20"),
        ("Bank and merchant fees", "1120S:20"),
        ("Dues and subscriptions", "1120S:20"),
        ("Miscellaneous", "1120S:20"),
    ],
    "OTHER_INCOME": [("Interest income", "K:4")],
    "OTHER_EXPENSE": [("Interest", "1120S:13"),
                      ("Charitable contributions", "K:12a"),
                      ("Penalties and fines", "K:16c"),
                      ("Nondeductible expenses", "K:16c")],   # cuotas de club, contribuciones políticas
    "NON_PL": [("Shareholder distributions", "K:16d"),
               ("Capital contributions", "L:23"),
               ("Loans from shareholders", "L:19"),
               ("Loans to shareholders", "L:7")] + _NON_PL_COMMON,
}

# ── Textos de ayuda: viajan con la categoría (por su llave por defecto) aunque la renombren ─────────
# El texto vive en los locales (`chart.help.<clave>`, EN y ES); aquí solo qué categoría lleva cuál.
HELP: dict[str, str] = {
    "Car and truck expenses": "car_truck",
    "Insurance": "insurance_sc",            # 1120-S: "insurance_1120s" (HELP_1120S)
    "Employee benefit programs": "employee_benefits",
    "Wages": "payroll_wages_sc",          # Schedule C: lo que saca el dueño NO va aquí (línea 26: Owner Draw)
    "Salaries and wages": "payroll_wages",
    "Compensation of officers": "payroll_officers",
    "Payroll taxes": "payroll_taxes",
    "Materials": "materials",
    "Equipment purchase (depreciable asset)": "equipment_purchase",
    # Nombres de una versión anterior del plan (19-oct): los guarda como llave la base que ya corrió
    # `a1f0c2d4e6b8` con ellos. Así la cuenta conserva su ayuda aunque el bookkeeper la renombre.
    "Insurance (other than health)": "insurance_sc",
    "Taxes and licenses – payroll taxes": "payroll_taxes",
}
# Mismo nombre, otra ayuda en 1120-S: el seguro médico del dueño va en Compensation of officers (no es Owner Draw).
HELP_1120S: dict[str, str] = {"Insurance": "insurance_1120s"}


def help_for(default_key: Optional[str], tax_form: str) -> Optional[str]:
    """La clave del texto de ayuda (`chart.help.<clave>`) de una cuenta del plan por defecto."""
    if not default_key:
        return None
    return (HELP_1120S.get(default_key) if tax_form == "1120s" else None) or HELP.get(default_key)


def default_plan(sector: str, tax_form: str = DEFAULT_TAX_FORM) -> dict[str, list[tuple[str, Optional[str]]]]:
    """El plan por defecto de un libro: `{sección: [(nombre, línea)…]}`, en orden.
    1120-S es uno solo para cualquier sector; Schedule C mezcla lo del sector (o el genérico) con lo común."""
    if tax_form == "1120s":
        out = {sec: list(FORM_1120S.get(sec, [])) for sec in _SECTIONS}
        # "Materials" en 1120-S (20-oct-2026): el Form 1125-A no tiene esa línea, así que el plan
        # general no la traía. Los libros cuyo SECTOR sí la trae en su lista de Schedule C
        # (construction, landscaping, painting… — se mira el COGS de ESE sector, no el genérico: un
        # sector sin plan propio, "other" incluido, cae en `_SC_GENERIC` para Schedule C, pero ESE
        # genérico no cuenta aquí) la reciben también en 1120-S, como "Other costs" (1125A:5). Solo
        # libros NUEVOS: `seed_chart` nunca toca una categoría que ya existe, así que esto no mueve
        # nada ya sembrado.
        sc_cogs = _SC_SECTOR.get((sector or "").strip().lower(), {}).get("COGS", [])
        if any(name == "Materials" for name, _ in sc_cogs):
            out["COGS"] = [("Materials", "1125A:5")] + out["COGS"]
        return out
    base = _SC_SECTOR.get((sector or "").strip().lower(), _SC_GENERIC)
    out: dict[str, list[tuple[str, Optional[str]]]] = {}
    for sec in _SECTIONS:
        seen: set[str] = set()
        out[sec] = [(n, ln) for n, ln in base.get(sec, []) + _SC_COMMON.get(sec, [])
                    if not (n in seen or seen.add(n))]
    return out


def categories_for(sector: str, tax_form: str = DEFAULT_TAX_FORM) -> dict[str, list[str]]:
    """Solo los NOMBRES del plan por defecto: `{sección: [categorías]}`."""
    return {sec: [n for n, _ in rows] for sec, rows in default_plan(sector, tax_form).items()}


def format_for_prompt(sector: str, tax_form: str = DEFAULT_TAX_FORM) -> str:
    """El plan por defecto en texto: una línea por sección (account_type: a | b | c)."""
    cats = categories_for(sector, tax_form)
    return "\n".join(f"  {sec}: " + " | ".join(cats[sec]) for sec in _SECTIONS if cats[sec])


# ── EL PLAN DE CADA LIBRO (tabla `companycategory`) ────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def seed_chart(session, company) -> int:
    """Le da al libro SU copia del plan por defecto (sector + formulario). Solo agrega lo que falta
    (por nombre, sin distinguir mayúsculas): nunca toca una categoría que ya existe. Sin commit.
    Devuelve cuántas agregó."""
    from sqlmodel import select
    from models import AccountType, CompanyCategory

    rows = session.exec(select(CompanyCategory).where(CompanyCategory.company_id == company.id)).all()
    have = {r.name.lower() for r in rows}
    next_pos: dict[str, int] = {}
    for r in rows:
        next_pos[r.account_type.value] = max(next_pos.get(r.account_type.value, 0), (r.position or 0) + 1)
    added = 0
    for sec, entries in default_plan(company.sector or "", company.tax_form or DEFAULT_TAX_FORM).items():
        for name, line in entries:
            if name.lower() in have:
                continue
            pos = next_pos.get(sec, 0)
            next_pos[sec] = pos + 1
            session.add(CompanyCategory(company_id=company.id, account_type=AccountType(sec), name=name,
                                        irs_line=line, position=pos, default_key=name))
            have.add(name.lower())
            added += 1
    if added:
        session.flush()
    return added


def chart_rows(session, company_id: int, *, include_archived: bool = True) -> list:
    """Las filas del plan del libro, en orden (sección → posición). Si el libro todavía no tiene plan y ya
    tiene sector, recibe aquí su copia (un libro creado por un camino que no la sembró: `seed.py`, pruebas)."""
    from sqlmodel import select
    from models import Company, CompanyCategory

    q = select(CompanyCategory).where(CompanyCategory.company_id == company_id)
    rows = session.exec(q).all()
    if not rows:
        company = session.get(Company, company_id)
        if company is not None and company.sector:
            seed_chart(session, company)
            rows = session.exec(q).all()
    order = {s: i for i, s in enumerate(_SECTIONS)}
    rows = sorted(rows, key=lambda r: (order.get(r.account_type.value, 99), r.position or 0, r.id or 0))
    return [r for r in rows if include_archived or r.archived_at is None]


def categories_for_company(session, company_id: int, sector: Optional[str] = None) -> dict[str, list[str]]:
    """El plan EFECTIVO del libro: `{sección: [categorías activas]}` (las archivadas no salen: no se
    eligen para una fila nueva, pero lo ya categorizado con ellas se queda). Lo leen el selector de la
    Hoja, la validación de una celda y de una regla, el import de reglas y el agente ④ (vía `formula`).
    `sector` queda en la firma por compatibilidad: el plan sale de la base."""
    out: dict[str, list[str]] = {sec: [] for sec in _SECTIONS}
    for r in chart_rows(session, company_id, include_archived=False):
        out.setdefault(r.account_type.value, []).append(r.name)
    return out


def all_chart_names(session, company_id: int) -> set[str]:
    """Todos los nombres del plan del libro, archivados incluidos."""
    return {r.name for r in chart_rows(session, company_id)}


def switch_plan(session, company) -> dict:
    """El libro cambió de formulario (o de sector): pasa al plan por defecto nuevo SIN perder nada.

    - lo que el plan nuevo trae y el libro no tiene → se agrega;
    - lo que se llama igual en los dos planes → se queda y solo cambia su línea a la del formulario nuevo;
    - lo que venía del plan viejo y NADIE usa (ni una fila ni una regla) → se va;
    - lo que está en uso o lo creó el bookkeeper → se queda, como categoría propia: el bookkeeper la
      renombra, la fusiona con una del plan nuevo o la quita (se archiva) desde Ajustes. Si su línea era
      del otro formulario, toma la que se sugiere para su sección.
    Sin commit. Devuelve conteos {added, removed, kept}."""
    from sqlmodel import select
    from models import CategoryRule, CompanyCategory, Transaction

    form = company.tax_form or DEFAULT_TAX_FORM
    new_line = {n: ln for rows in default_plan(company.sector or "", form).values() for n, ln in rows}
    new_names = set(new_line)
    used = set(session.exec(select(Transaction.category).where(Transaction.company_id == company.id)
                            .where(Transaction.category.is_not(None)).distinct()).all())
    used |= set(session.exec(select(CategoryRule.category).where(CategoryRule.company_id == company.id)
                             .distinct()).all())
    removed = kept = 0
    for r in session.exec(select(CompanyCategory).where(CompanyCategory.company_id == company.id)).all():
        if r.name in new_names:
            # Se llama igual en los dos planes (Utilities, Supplies, Travel…): se QUEDA como cuenta del plan,
            # en su sección, y solo toma la línea del formulario nuevo (Jose, 19-oct). Nada que fusionar.
            r.default_key = r.name
            r.irs_line = new_line[r.name]
            session.add(r)
            continue
        if r.default_key is not None and r.name not in used and r.name not in LOCKED:
            session.delete(r)
            removed += 1
            continue
        r.default_key = None   # ya no es del plan por defecto: queda como propia del libro
        if line_form(r.irs_line) != form:
            r.irs_line = suggested_line(form, r.account_type.value)
        session.add(r)
        kept += 1
    session.flush()
    added = seed_chart(session, company)
    return {"added": added, "removed": removed, "kept": kept}


# ── Del plan VIEJO (nombres en código hasta el 18-oct) al nuevo — para la migración `a1f0c2d4e6b8` ──
# Por sector, porque un mismo nombre viejo cae distinto según el giro (Equipment → Supplies; Rent →
# Rent or lease – other business property). Donde dos se fusionan (Direct Labor y Admin Wages → Wages),
# las filas y las reglas pasan a la nueva.
_RENAME_COMMON = {
    "Office Expense": "Office expense",
    "Software": "Software",
    "Bank Fees": "Bank fees",
    "Legal & Professional": "Legal and professional services",
    "Meals": "Deductible meals",
    "Dues & Subscriptions": "Dues and subscriptions",
    "Telephone & Internet": "Utilities",
    "Interest Income": "Interest income",
    "Refunds & Rebates": "Refunds and rebates",
    "Interest Expense": "Interest – other",
    "Taxes & Licenses": "Taxes and licenses",      # PASA de OTHER_EXPENSE a OPERATING_EXPENSE
    "Insurance": "Insurance",                       # la línea 15 dice "(other than health)"; el nombre, no
    "Marketing": "Advertising",
    "Repairs & Maintenance": "Repairs and maintenance",
    "Payroll tax": "Payroll taxes",                 # una cuenta propia con ese nombre se fusiona con la del plan
    "Admin Wages": "Wages",
    "Direct Labor": "Wages",
    "Subcontractors": "Contract labor – subcontractors",
    "Vehicle Fuel": "Car and truck expenses",
    "Vehicle Expenses": "Car and truck expenses",
    "Small Tools & Equipment": "Supplies",
    "Equipment Rental": "Rent or lease – job equipment",
    "Equipment": "Supplies",
    "Uniforms": "Uniforms",
    "Rent": "Rent or lease – other business property",
    "Office Rent": "Rent or lease – other business property",
    "Shop Rent": "Rent or lease – other business property",
    "Store Rent": "Rent or lease – other business property",
    "Salon Rent": "Rent or lease – other business property",
    "Permits": "Taxes and licenses – permits",
    "Equipment Fuel": "Equipment fuel",
    "Trucking & Hauling": "Trucking and hauling",
    "Dumping & Disposal": "Dumping and disposal",
    "Construction Revenue": "Gross receipts or sales",
    "Cleaning Revenue": "Gross receipts or sales",
    "Landscaping Revenue": "Gross receipts or sales",
    "Painting Revenue": "Gross receipts or sales",
    "Freight Revenue": "Gross receipts or sales",
    "Sales Revenue": "Gross receipts or sales",
    "Service Revenue": "Gross receipts or sales",
    "Food & Beverage Sales": "Gross receipts or sales",
}
_RENAME_SECTOR = {
    "cleaning": {"Cleaning Supplies": "Supplies – cleaning supplies", "Transportation": "Car and truck expenses"},
    "restaurant": {"Food Purchases": "Purchases – food", "Beverages": "Purchases – beverages",
                   "Beer/Wine/Liquor": "Purchases – beer, wine, and liquor",
                   "Paper & Packaging": "Materials and supplies",
                   "Contract Cleaning": "Cleaning services",
                   "Linens": "Linens and laundry", "Smallwares": "Supplies – smallwares",
                   "Front-of-House Wages": "Wages", "Kitchen Wages": "Wages"},
    "landscaping": {"Plants & Materials": "Materials"},
    "painting": {"Paint & Materials": "Materials"},
    "trucking": {"Fuel": "Car and truck expenses – fuel", "Truck Repairs & Maintenance": "Repairs and maintenance – trucks",
                 "Tires": "Repairs and maintenance – tires", "Tolls & Scales": "Car and truck expenses – tolls and scales",
                 "Truck Lease": "Rent or lease – trucks", "Driver Pay": "Contract labor – drivers",
                 "Dispatch & Broker Fees": "Commissions and fees", "Parking & Storage": "Parking and storage"},
    "beauty": {"Service Revenue": "Gross receipts or sales – services", "Product Sales": "Gross receipts or sales – products",
               "Products & Supplies": "Supplies – salon products", "Contract Stylists": "Contract labor – stylists",
               "Uniforms & Laundry": "Uniforms and laundry"},
    "auto_repair": {"Repair Revenue": "Gross receipts or sales – repairs", "Parts Sales": "Gross receipts or sales – parts",
                    "Parts": "Purchases", "Shop Supplies": "Materials and supplies"},
    "retail": {"Merchandise Purchases": "Purchases", "Freight-In": "Other costs", "Packaging & Bags": "Materials and supplies"},
}


def schedule_c_renames(sector: str) -> dict[str, str]:
    """Nombre viejo → nombre nuevo (Schedule C) para un sector."""
    return {**_RENAME_COMMON, **_RENAME_SECTOR.get((sector or "").strip().lower(), {})}


if __name__ == "__main__":
    for form, sectors in (("schedule_c", ("construction", "cleaning", "restaurant", "other")), ("1120s", ("",))):
        for s in sectors:
            print(f"\n=== {form} · {s or 'any'} ===\n{format_for_prompt(s, form)}")
