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
    "OPERATING_EXPENSE": ["Office Expense", "Software", "Bank Fees", "Legal & Professional", "Meals"],
    "OTHER_INCOME": ["Interest Income", "Refunds & Rebates"],
    "OTHER_EXPENSE": ["Interest Expense", "Taxes & Licenses"],
    "NON_PL": ["Owner Draw", "Owner Contribution", "Transfer", "Loan Proceeds",
               "Loan Principal Payment", "Credit Card Payment"],
}

SECTOR_CATEGORIES: dict[str, dict[str, list[str]]] = {
    "construction": {
        "REVENUE": ["Construction Revenue"],
        "COGS": ["Materials", "Subcontractors", "Direct Labor", "Equipment Rental",
                 "Permits", "Equipment Fuel"],   # Equipment Fuel = combustible de maquinaria (en obra)
        "OPERATING_EXPENSE": ["Office Rent", "Insurance", "Marketing", "Admin Wages",
                              "Vehicle Fuel", "Vehicle Expenses", "Utilities",
                              "Repairs & Maintenance"],  # Vehicle Fuel = gasolina de auto/camión
    },
    "cleaning": {
        "REVENUE": ["Cleaning Revenue"],
        "COGS": ["Cleaning Supplies", "Direct Labor", "Transportation"],
        "OPERATING_EXPENSE": ["Insurance", "Marketing", "Equipment", "Admin Wages",
                              "Uniforms", "Vehicle Fuel", "Utilities"],
    },
    "restaurant": {
        "REVENUE": ["Food & Beverage Sales"],
        "COGS": ["Food Purchases", "Beverages", "Beer/Wine/Liquor", "Paper & Packaging"],
        "OPERATING_EXPENSE": ["Rent", "Utilities", "Contract Cleaning", "Linens",
                              "Repairs & Maintenance", "Marketing", "Front-of-House Wages"],
    },
}

# Para sectores no reconocidos: un set genérico mínimo.
GENERIC: dict[str, list[str]] = {
    "REVENUE": ["Sales Revenue", "Service Revenue"],
    "COGS": ["Materials", "Direct Labor", "Subcontractors"],
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


if __name__ == "__main__":
    for s in ("construction", "cleaning", "restaurant", "unknown"):
        print(f"\n=== {s} ===\n{format_for_prompt(s)}")
