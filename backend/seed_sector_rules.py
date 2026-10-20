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
# Las categorías deben existir en el plan por defecto del sector (categories.py; nombres oficiales del
# IRS desde el 18-oct).
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
        ("Harbor Freight", OPEX, "Supplies"),
        ("Northern Tool", OPEX, "Supplies"),
        # --- Renta de equipo (COGS) ---
        ("Sunbelt Rentals", COGS, "Rent or lease – job equipment"),
        ("United Rentals", COGS, "Rent or lease – job equipment"),
        ("Herc Rentals", COGS, "Rent or lease – job equipment"),
        # --- Botadero / desechos (COGS) ---
        ("Waste Management", COGS, "Dumping and disposal"),
        ("Republic Services", COGS, "Dumping and disposal"),
        # --- Vehículo (OpEx): autopartes y peajes ---
        ("AutoZone", OPEX, "Car and truck expenses"),
        ("O'Reilly", OPEX, "Car and truck expenses"),
        ("NAPA Auto", OPEX, "Car and truck expenses"),
        ("FasTrak", OPEX, "Car and truck expenses"),       # peajes
        # --- Software (OpEx) ---
        ("QuickBooks", OPEX, "Software"),
        ("Intuit", OPEX, "Software"),
        ("Squarespace", OPEX, "Software"),
        ("Anthropic", OPEX, "Software"),
        # --- Teléfono e internet (OpEx) ---
        ("Verizon", OPEX, "Utilities"),
        ("T-Mobile", OPEX, "Utilities"),
        ("Comcast", OPEX, "Utilities"),
        ("Xfinity", OPEX, "Utilities"),
        ("Spectrum", OPEX, "Utilities"),
    ],

    # LIMPIEZA (cleaning).
    "cleaning": [
        # --- Insumos de limpieza (COGS): distribuidores janitoriales / químicos ---
        ("Ecolab", COGS, "Supplies – cleaning supplies"),
        ("Uline", COGS, "Supplies – cleaning supplies"),
        ("WAXIE", COGS, "Supplies – cleaning supplies"),
        ("Zep", COGS, "Supplies – cleaning supplies"),
        ("Grainger", COGS, "Supplies – cleaning supplies"),
        ("Staples", COGS, "Supplies – cleaning supplies"),
        # --- Uniformes (OpEx): servicios de uniformes ---
        ("Cintas", OPEX, "Uniforms"),       # mismo comercio, categoría DISTINTA por sector (vs. restaurant→Linens)
        ("UniFirst", OPEX, "Uniforms"),
        ("Aramark", OPEX, "Uniforms"),
        # --- Software (OpEx): agendamiento de limpieza + contabilidad ---
        ("QuickBooks", OPEX, "Software"),
        ("Intuit", OPEX, "Software"),
        ("Jobber", OPEX, "Software"),
        ("ZenMaid", OPEX, "Software"),
        # --- Teléfono e internet (OpEx) ---
        ("Verizon", OPEX, "Utilities"),
        ("T-Mobile", OPEX, "Utilities"),
        ("Comcast", OPEX, "Utilities"),
        ("Xfinity", OPEX, "Utilities"),
        ("Spectrum", OPEX, "Utilities"),
    ],

    # RESTAURANTE (restaurant).
    "restaurant": [
        # --- Compras de comida (COGS): distribuidores de food service ---
        ("Sysco", COGS, "Purchases – food"),
        ("US Foods", COGS, "Purchases – food"),
        ("Restaurant Depot", COGS, "Purchases – food"),
        ("Gordon Food", COGS, "Purchases – food"),
        ("Performance Food", COGS, "Purchases – food"),
        ("Shamrock Foods", COGS, "Purchases – food"),
        ("Cheney Brothers", COGS, "Purchases – food"),
        # --- Alcohol (COGS) ---
        ("Southern Glazer's", COGS, "Purchases – beer, wine, and liquor"),
        ("Breakthru Beverage", COGS, "Purchases – beer, wine, and liquor"),
        # --- Bebidas (COGS) ---
        ("Coca-Cola", COGS, "Purchases – beverages"),
        ("Pepsi", COGS, "Purchases – beverages"),
        # --- Papel y empaques (COGS) ---
        ("Uline", COGS, "Materials and supplies"),
        # --- Utensilios de cocina (OpEx) ---
        ("WebstaurantStore", OPEX, "Supplies – smallwares"),
        # --- Mantelería (OpEx): mismo comercio que en limpieza, categoría distinta ---
        ("Cintas", OPEX, "Linens and laundry"),
        ("Aramark", OPEX, "Linens and laundry"),
        # --- Software (OpEx) ---
        ("QuickBooks", OPEX, "Software"),
        ("Intuit", OPEX, "Software"),
        ("Toast", OPEX, "Software"),        # POS de restaurantes
        # --- Teléfono e internet (OpEx) ---
        ("Verizon", OPEX, "Utilities"),
        ("Comcast", OPEX, "Utilities"),
        ("Xfinity", OPEX, "Utilities"),
        ("Spectrum", OPEX, "Utilities"),
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
