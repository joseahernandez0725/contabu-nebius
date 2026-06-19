"""
ContaBu — reportes financieros (cálculos en SQL).

REGLA DE ORO: los números los calcula PostgreSQL (SUM, COUNT, GROUP BY),
no la IA. Aquí se arma el estado de resultados (P&L) a partir de las
transacciones ya categorizadas (account_type).

Convención: `amount` es magnitud positiva; el signo lo decide account_type.
"""

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import func
from sqlmodel import Session, select

from db import engine
from models import AccountType, Company, Transaction

ZERO = Decimal("0.00")


def _section_totals(
    session: Session,
    company_id: int,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> dict[AccountType, Decimal]:
    """SUM(amount) agrupado por account_type (esto lo calcula SQL), opcional por rango de fechas."""
    stmt = (
        select(Transaction.account_type, func.sum(Transaction.amount))
        .where(Transaction.company_id == company_id)
        .group_by(Transaction.account_type)
    )
    if start is not None:
        stmt = stmt.where(Transaction.date >= start)
    if end is not None:
        stmt = stmt.where(Transaction.date <= end)
    return {at: total for at, total in session.exec(stmt).all() if at is not None}


def income_statement(
    session: Session,
    company_id: int,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> dict[str, Decimal]:
    """Construye el P&L con la aritmética estándar. NON_PL se ignora. Opcional: rango de fechas."""
    t = _section_totals(session, company_id, start, end)

    revenue = t.get(AccountType.REVENUE, ZERO)
    cogs = t.get(AccountType.COGS, ZERO)
    gross_profit = revenue - cogs

    opex = t.get(AccountType.OPERATING_EXPENSE, ZERO)
    operating_income = gross_profit - opex

    other_income = t.get(AccountType.OTHER_INCOME, ZERO)
    other_expense = t.get(AccountType.OTHER_EXPENSE, ZERO)
    net_income = operating_income + other_income - other_expense

    return {
        "revenue": revenue,
        "cogs": cogs,
        "gross_profit": gross_profit,
        "operating_expenses": opex,
        "operating_income": operating_income,
        "other_income": other_income,
        "other_expense": other_expense,
        "net_income": net_income,
    }


def monthly_transaction_count(session: Session, company_id: int, year: int, month: int) -> int:
    """COUNT de transacciones de un mes (base del gate del plan Free ≤ 50/mes)."""
    stmt = (
        select(func.count())
        .select_from(Transaction)
        .where(Transaction.company_id == company_id)
        .where(func.extract("year", Transaction.date) == year)
        .where(func.extract("month", Transaction.date) == month)
    )
    return session.scalar(stmt) or 0


if __name__ == "__main__":
    def money(x: Decimal) -> str:
        return f"${x:,.2f}"

    with Session(engine) as session:
        company = session.exec(
            select(Company).where(Company.name == "Demo Construction LLC")
        ).first()
        if company is None:
            print("No hay empresa demo. Corre primero:  python seed.py")
            raise SystemExit(1)

        pnl = income_statement(session, company.id)

        print(f"\n=== Income Statement — {company.name} ===")
        print(f"  Revenue                {money(pnl['revenue'])}")
        print(f"- Cost of Goods Sold     {money(pnl['cogs'])}")
        print(f"= Gross Profit           {money(pnl['gross_profit'])}")
        print(f"- Operating Expenses     {money(pnl['operating_expenses'])}")
        print(f"= Operating Income       {money(pnl['operating_income'])}")
        print(f"+ Other Income           {money(pnl['other_income'])}")
        print(f"- Other Expense          {money(pnl['other_expense'])}")
        print(f"= Net Income             {money(pnl['net_income'])}")

        count = monthly_transaction_count(session, company.id, 2026, 3)
        print(f"\nTransacciones en 2026-03: {count}  (gate Free: ≤ 50/mes)")
