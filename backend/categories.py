"""
ContaBu — plan de cuentas CERRADO por sector (categorías canónicas).

La IA debe mapear cada transacción a UNA de estas categorías (no inventar texto libre),
para que el reporte sea consistente y alineado a IRS Schedule C. Cada categoría vive bajo
su `account_type` (sección del P&L). Fuente: `docs/REFERENCIA CONTABLE.md` §8.

Nota de diseño: la categoría es **contexto cerrado**, no una heurística prescriptiva — la IA
sigue razonando desde la descripción/monto (no le decimos "tal cosa es X"); solo le acotamos el
vocabulario de salida. Si algo genuinamente no encaja, la IA usa "Other" (no fuerza un mal mapeo).
"""

# Categorías comunes a TODOS los sectores (no dependen del giro).
COMMON: dict[str, list[str]] = {
    "OPERATING_EXPENSE": ["Office Expense", "Software", "Bank Fees", "Legal & Professional",
                          "Meals", "Dues & Subscriptions", "Telephone & Internet", "Travel"],
    "OTHER_INCOME": ["Interest Income", "Refunds & Rebates"],
    "OTHER_EXPENSE": ["Interest Expense", "Taxes & Licenses"],
    "NON_PL": ["Owner Draw", "Owner Contribution", "Transfer", "Loan Proceeds",
               "Loan Principal Payment", "Credit Card Payment"],
}

SECTOR_CATEGORIES: dict[str, dict[str, list[str]]] = {
    "construction": {
        "REVENUE": ["Construction Revenue"],
        # COGS = costos DIRECTOS de obra (Schedule C "Supplies"/"Contract labor"). Small tools,
        # trucking y disposal son COGS estándar del contratista (ver REFERENCIA CONTABLE §8).
        "COGS": ["Materials", "Subcontractors", "Direct Labor", "Equipment Rental",
                 "Small Tools & Equipment", "Permits", "Equipment Fuel",
                 "Trucking & Hauling", "Dumping & Disposal"],   # Equipment Fuel = combustible de maquinaria
        "OPERATING_EXPENSE": ["Office Rent", "Insurance", "Marketing", "Admin Wages",
                              "Vehicle Fuel", "Vehicle Expenses", "Utilities",
                              "Repairs & Maintenance"],  # Vehicle Fuel = gasolina de auto/camión
    },
    "cleaning": {
        "REVENUE": ["Cleaning Revenue"],
        "COGS": ["Cleaning Supplies", "Direct Labor", "Subcontractors", "Transportation",
                 "Small Tools & Equipment"],
        "OPERATING_EXPENSE": ["Insurance", "Marketing", "Equipment", "Admin Wages",
                              "Uniforms", "Vehicle Fuel", "Utilities"],
    },
    "restaurant": {
        "REVENUE": ["Food & Beverage Sales"],
        "COGS": ["Food Purchases", "Beverages", "Beer/Wine/Liquor", "Paper & Packaging"],
        "OPERATING_EXPENSE": ["Rent", "Utilities", "Contract Cleaning", "Linens", "Smallwares",
                              "Repairs & Maintenance", "Marketing", "Front-of-House Wages",
                              "Kitchen Wages"],
    },
    # --- Segunda ola (3-jul-2026): los oficios inmigrantes más comunes fuera de los 3 verticales.
    # Planes alineados a IRS Schedule C + planes de cuentas estándar por industria (QuickBooks).
    # Detalle contable en docs/REFERENCIA CONTABLE.md §8.
    "landscaping": {
        "REVENUE": ["Landscaping Revenue"],
        "COGS": ["Plants & Materials", "Subcontractors", "Direct Labor", "Equipment Rental",
                 "Small Tools & Equipment", "Equipment Fuel", "Dumping & Disposal"],
        "OPERATING_EXPENSE": ["Insurance", "Marketing", "Admin Wages", "Vehicle Fuel",
                              "Vehicle Expenses", "Repairs & Maintenance", "Uniforms"],
    },
    "painting": {
        "REVENUE": ["Painting Revenue"],
        "COGS": ["Paint & Materials", "Subcontractors", "Direct Labor", "Equipment Rental",
                 "Small Tools & Equipment", "Permits"],
        "OPERATING_EXPENSE": ["Insurance", "Marketing", "Admin Wages", "Vehicle Fuel",
                              "Vehicle Expenses", "Utilities", "Repairs & Maintenance"],
    },
    "trucking": {
        # Owner-operator: el combustible y el mantenimiento del camión SON el costo directo del
        # flete (no overhead).
        "REVENUE": ["Freight Revenue"],
        "COGS": ["Fuel", "Truck Repairs & Maintenance", "Tires", "Tolls & Scales",
                 "Truck Lease", "Driver Pay", "Dispatch & Broker Fees"],
        "OPERATING_EXPENSE": ["Insurance", "Parking & Storage", "Marketing", "Admin Wages"],
    },
    "beauty": {  # salón de belleza / barbería
        "REVENUE": ["Service Revenue", "Product Sales"],
        "COGS": ["Products & Supplies", "Contract Stylists", "Direct Labor"],
        "OPERATING_EXPENSE": ["Salon Rent", "Utilities", "Insurance", "Marketing", "Equipment",
                              "Uniforms & Laundry", "Admin Wages"],
    },
    "auto_repair": {  # taller mecánico
        "REVENUE": ["Repair Revenue", "Parts Sales"],
        "COGS": ["Parts", "Shop Supplies", "Direct Labor", "Subcontractors",
                 "Small Tools & Equipment"],
        "OPERATING_EXPENSE": ["Shop Rent", "Utilities", "Insurance", "Marketing", "Equipment",
                              "Uniforms", "Admin Wages", "Vehicle Fuel"],
    },
    "retail": {  # tienda / bodega / minimercado
        "REVENUE": ["Sales Revenue"],
        "COGS": ["Merchandise Purchases", "Freight-In", "Packaging & Bags"],
        "OPERATING_EXPENSE": ["Store Rent", "Utilities", "Insurance", "Marketing", "Wages",
                              "Equipment"],
    },
}

# Para sectores no reconocidos: un set genérico mínimo.
GENERIC: dict[str, list[str]] = {
    "REVENUE": ["Sales Revenue", "Service Revenue"],
    "COGS": ["Materials", "Direct Labor", "Subcontractors", "Small Tools & Equipment"],
    "OPERATING_EXPENSE": ["Rent", "Insurance", "Marketing", "Wages", "Utilities",
                          "Vehicle Fuel", "Repairs & Maintenance"],
}

# Orden estable de las secciones del P&L.
_SECTIONS = ["REVENUE", "COGS", "OPERATING_EXPENSE", "OTHER_INCOME", "OTHER_EXPENSE", "NON_PL"]


def categories_for(sector: str) -> dict[str, list[str]]:
    """Plan de cuentas cerrado de un sector: {account_type: [categorías]}.
    Mezcla las del sector (o GENERIC si no se reconoce) con las COMMON, sin duplicar."""
    base = SECTOR_CATEGORIES.get((sector or "").strip().lower(), GENERIC)
    out: dict[str, list[str]] = {}
    for sec in _SECTIONS:
        out[sec] = list(dict.fromkeys(base.get(sec, []) + COMMON.get(sec, [])))
    return out


def format_for_prompt(sector: str) -> str:
    """Renderiza el plan de cuentas para el prompt: una línea por sección (account_type: a | b | c)."""
    cats = categories_for(sector)
    return "\n".join(
        f"  {sec}: " + " | ".join(cats[sec]) for sec in _SECTIONS if cats[sec]
    )


def categories_for_company(session, company_id: int, sector: str) -> dict[str, list[str]]:
    """Plan de cuentas EFECTIVO de una empresa: el base del sector + sus categorías personalizadas
    (P&L personalizable v1). Las personalizadas se agregan al final de su sección, sin duplicar.

    Se usa en el endpoint de categorías (selector de corrección) y, así, en el P&L detallado.
    """
    from sqlmodel import select  # import local: categories.py se mantiene sin dependencias de DB salvo aquí
    from models import CompanyCategory

    out = {sec: list(cats) for sec, cats in categories_for(sector).items()}
    customs = session.exec(
        select(CompanyCategory).where(CompanyCategory.company_id == company_id)
    ).all()
    for c in customs:
        sec = c.account_type.value
        bucket = out.setdefault(sec, [])
        if c.name not in bucket:
            bucket.append(c.name)
    return out


if __name__ == "__main__":
    for s in ("construction", "cleaning", "restaurant", "unknown"):
        print(f"\n=== {s} ===\n{format_for_prompt(s)}")
