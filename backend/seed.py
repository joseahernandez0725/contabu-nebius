"""
Fase 4, Paso 2: sembrar datos de ejemplo para poder calcular el P&L.

Crea una empresa demo de construcción + 8 transacciones YA categorizadas
(con account_type), incluida una NON_PL (transferencia) que debe quedar fuera del P&L.
Los montos se guardan como MAGNITUD positiva (el signo lo decide account_type).
Idempotente: si la empresa demo ya existe, no hace nada.
"""

from datetime import date
from decimal import Decimal

from sqlmodel import Session, select

from db import engine
from models import AccountType, Company, Plan, Transaction

DEMO_NAME = "Demo Construction LLC"

# (account_type, category, subcategory, amount, date)  — amount siempre positivo. Nombres del plan
# oficial (18-oct); el libro recibe su plan al leerlo por primera vez (`categories.chart_rows`).
ROWS = [
    (AccountType.REVENUE,           "Gross receipts or sales", "Client ABC",     "5000.00", date(2026, 3, 5)),
    (AccountType.REVENUE,           "Gross receipts or sales", "Client XYZ",     "3000.00", date(2026, 3, 18)),
    (AccountType.COGS,              "Materials",      "Home Depot",      "342.18", date(2026, 3, 14)),
    (AccountType.COGS,              "Contract labor – subcontractors", "Joe's Framing",  "1200.00", date(2026, 3, 20)),
    (AccountType.OPERATING_EXPENSE, "Rent or lease – other business property", "Office Park",     "800.00", date(2026, 3, 1)),
    (AccountType.OPERATING_EXPENSE, "Utilities",      "City Power",      "150.00", date(2026, 3, 10)),
    (AccountType.OTHER_EXPENSE,     "Interest – other", "Bank Loan",        "75.00", date(2026, 3, 28)),
    (AccountType.NON_PL,            "Transfer",       "Savings #4471",   "500.00", date(2026, 3, 22)),
]


def seed() -> None:
    with Session(engine) as session:
        existing = session.exec(
            select(Company).where(Company.name == DEMO_NAME)
        ).first()
        if existing:
            print(f"La empresa demo ya existe (id {existing.id}). Nada que sembrar.")
            return

        company = Company(name=DEMO_NAME, sector="construction", plan=Plan.FREE)
        session.add(company)
        session.commit()
        session.refresh(company)

        for account_type, category, subcategory, amount, d in ROWS:
            session.add(Transaction(
                company_id=company.id,
                date=d,
                description=f"{subcategory} - {category}",
                amount=Decimal(amount),
                account_type=account_type,
                category=category,
                subcategory=subcategory,
                confidence=1.0,
                ai_categorized=True,
            ))
        session.commit()
        print(f"Sembradas {len(ROWS)} transacciones para '{company.name}' (id {company.id}).")


if __name__ == "__main__":
    seed()
