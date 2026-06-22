"""ContaBu — siembra de REGLAS POR SECTOR (curadas por el admin, invisibles al cliente).

Las reglas de SECTOR (`CategoryRule.scope = SECTOR`, sin `company_id`) son el conocimiento contable
base que arranca a cada empresa nueva con buena categorización desde el día 1. La regla de EMPRESA
SIEMPRE tiene prioridad (ver `rules.load_rules`/`match_rule`), así el cliente nunca queda atrapado en
un default. El cliente NO ve estas reglas (la tabla `/rules` filtra `scope==COMPANY`). Ver IDEAS.md #16.

Solo se incluyen comercios con mapeo CONFIABLE para el sector; se evitan:
- ambiguos (Amazon, Walmart, Costco, Target…) → los decide la IA o la corrección del dueño,
- gasolineras/supermercados → ya los desambigua la banda de monto (merchants.py / idea #14).

El `match_text` se compara por LÍMITE DE PALABRA (ver `rules.match_rule`): "Home Depot" matchea
"THE HOME DEPOT #0644" pero no rompe palabras sueltas.

Uso (admin, vía Cloud SQL Auth Proxy):
    ./cloud-sql-proxy --port 5433 contabu-prod:us-central1:contabu-db &
    DATABASE_URL='postgresql+psycopg://contabu:***@127.0.0.1:5433/contabu' \
        .venv/bin/python seed_sector_rules.py            # idempotente: crea o actualiza, no duplica
"""

from sqlmodel import Session, select

from db import engine
from models import AccountType, CategoryRule, RuleScope
from rules import _phrase

COGS = AccountType.COGS
OPEX = AccountType.OPERATING_EXPENSE

# Catálogo: sector -> lista de (match_text, account_type, category).
# Las categorías deben existir en el plan del sector (categories.py).
SECTOR_RULES: dict[str, list[tuple[str, AccountType, str]]] = {
    # CONSTRUCCIÓN (paraguas de los oficios: electricistas, plomeros, pintores, HVAC…).
    "construction": [
        # --- Materiales (COGS): proveedores de construcción / ferreterías / pintura / plomería ---
        ("Home Depot", COGS, "Materials"),
        ("Lowe's", COGS, "Materials"),
        ("Menards", COGS, "Materials"),
        ("84 Lumber", COGS, "Materials"),
        ("Ferguson", COGS, "Materials"),            # plomería / HVAC
        ("Johnstone Supply", COGS, "Materials"),    # HVAC
        ("White Cap", COGS, "Materials"),
        ("HD Supply", COGS, "Materials"),
        ("ABC Supply", COGS, "Materials"),          # techos
        ("SiteOne", COGS, "Materials"),             # paisajismo / riego
        ("Fastenal", COGS, "Materials"),
        ("Grainger", COGS, "Materials"),
        ("Sherwin Williams", COGS, "Materials"),    # pintura
        ("Benjamin Moore", COGS, "Materials"),      # pintura
        ("Floor & Decor", COGS, "Materials"),
        ("Ace Hardware", COGS, "Materials"),
        ("True Value", COGS, "Materials"),
        # --- Herramientas / equipo menor (COGS) ---
        ("Harbor Freight", COGS, "Small Tools & Equipment"),
        ("Northern Tool", COGS, "Small Tools & Equipment"),
        # --- Renta de equipo (COGS) ---
        ("Sunbelt Rentals", COGS, "Equipment Rental"),
        ("United Rentals", COGS, "Equipment Rental"),
        ("Herc Rentals", COGS, "Equipment Rental"),
        # --- Botadero / desechos (COGS) ---
        ("Waste Management", COGS, "Dumping & Disposal"),
        ("Republic Services", COGS, "Dumping & Disposal"),
        # --- Vehículo (OpEx): autopartes y peajes ---
        ("AutoZone", OPEX, "Vehicle Expenses"),
        ("O'Reilly", OPEX, "Vehicle Expenses"),
        ("NAPA Auto", OPEX, "Vehicle Expenses"),
        ("FasTrak", OPEX, "Vehicle Expenses"),       # peajes
        # --- Software (OpEx) ---
        ("QuickBooks", OPEX, "Software"),
        ("Intuit", OPEX, "Software"),
        ("Squarespace", OPEX, "Software"),
        ("Anthropic", OPEX, "Software"),
        # --- Teléfono e internet (OpEx) ---
        ("Verizon", OPEX, "Telephone & Internet"),
        ("T-Mobile", OPEX, "Telephone & Internet"),
        ("Comcast", OPEX, "Telephone & Internet"),
        ("Xfinity", OPEX, "Telephone & Internet"),
        ("Spectrum", OPEX, "Telephone & Internet"),
    ],
}


def seed_sector_rules() -> None:
    created = 0
    updated = 0
    with Session(engine) as session:
        for sector, rules in SECTOR_RULES.items():
            existing = session.exec(
                select(CategoryRule)
                .where(CategoryRule.scope == RuleScope.SECTOR)
                .where(CategoryRule.sector == sector)
            ).all()
            by_phrase = {_phrase(r.match_text): r for r in existing}

            for match_text, account_type, category in rules:
                key = _phrase(match_text)
                rule = by_phrase.get(key)
                if rule is not None:
                    if (rule.account_type, rule.category) != (account_type, category):
                        rule.account_type = account_type
                        rule.category = category
                        session.add(rule)
                        updated += 1
                else:
                    session.add(CategoryRule(
                        scope=RuleScope.SECTOR, sector=sector, company_id=None,
                        match_text=match_text, account_type=account_type, category=category,
                    ))
                    created += 1
            print(f"  {sector}: {len(rules)} reglas en el catálogo")
        session.commit()
    print(f"\n✅ Reglas de sector: {created} creadas, {updated} actualizadas (idempotente).")


if __name__ == "__main__":
    seed_sector_rules()
