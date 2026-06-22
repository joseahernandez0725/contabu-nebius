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
from models import AccountType, Company, CompanyCategory, Transaction

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


# Secciones que SÍ se desglosan por categoría en el P&L (NON_PL se excluye del estado de resultados).
_PL_SECTIONS = (
    AccountType.REVENUE, AccountType.COGS, AccountType.OPERATING_EXPENSE,
    AccountType.OTHER_INCOME, AccountType.OTHER_EXPENSE,
)


def _category_breakdown(
    session: Session,
    company_id: int,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> dict[str, list[dict]]:
    """SUM(amount) agrupado por (account_type, category) — el desglose de cada sección del P&L.

    Devuelve {section: [{"category": str, "amount": Decimal}, ...]} con las categorías
    ordenadas de mayor a menor monto. Lo calcula SQL (regla de oro), no la IA.
    """
    stmt = (
        select(Transaction.account_type, Transaction.category, func.sum(Transaction.amount))
        .where(Transaction.company_id == company_id)
        .group_by(Transaction.account_type, Transaction.category)
    )
    if start is not None:
        stmt = stmt.where(Transaction.date >= start)
    if end is not None:
        stmt = stmt.where(Transaction.date <= end)

    out: dict[str, list[dict]] = {s.value: [] for s in _PL_SECTIONS}
    seen: dict[str, set[str]] = {s.value: set() for s in _PL_SECTIONS}
    for at, category, total in session.exec(stmt).all():
        if at in _PL_SECTIONS:
            name = category or "Other"
            out[at.value].append({"category": name, "amount": total or ZERO})
            seen[at.value].add(name)

    # Categorías PERSONALIZADAS de la empresa: aparecen SIEMPRE en el P&L (aunque estén en 0.00),
    # para que el dueño pueda estructurar/personalizar su estado de resultados a su gusto.
    customs = session.exec(
        select(CompanyCategory).where(CompanyCategory.company_id == company_id)
    ).all()
    for c in customs:
        sec = c.account_type.value
        if sec in out and c.name not in seen[sec]:
            out[sec].append({"category": c.name, "amount": ZERO})
            seen[sec].add(c.name)

    for rows in out.values():
        rows.sort(key=lambda r: r["amount"], reverse=True)
    return out


def income_statement(
    session: Session,
    company_id: int,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> dict[str, object]:
    """Construye el P&L con la aritmética estándar + el desglose por categoría de cada sección.
    NON_PL se ignora. Opcional: rango de fechas."""
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
        # Desglose por categoría de cada sección (para el P&L detallado).
        "breakdown": _category_breakdown(session, company_id, start, end),
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
