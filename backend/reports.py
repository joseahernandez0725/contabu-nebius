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


def _uncategorized(
    session: Session,
    company_id: int,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> dict[str, object]:
    """Lo **PENDIENTE**: transacciones sin `account_type`. Un solo `GROUP BY` en SQL.

    REGLA: *lo Pendiente se declara, nunca se omite* (`CLAUDE.md`). El P&L solo puede sumar lo que
    tiene sección, así que estas filas **no entran al resultado** — pero tienen que SALIR en el
    reporte, o el total se ve perfecto y está incompleto.

    Se separa por `is_inflow` (la dirección REAL del banco, hecho objetivo) y **no se adivina el
    signo contable**: sin `account_type` no se sabe si una entrada es ingreso o un aporte del dueño,
    ni si una salida es gasto, pago de tarjeta o retiro. Por eso aquí hay **caja**, no resultado.

    `is_inflow IS NULL` (dirección desconocida) va a su propio renglón en vez de caer en uno de los
    otros dos: redondear eso hacia cualquier lado sería inventar.
    """
    stmt = (
        select(Transaction.is_inflow, func.count(Transaction.id), func.sum(Transaction.amount))
        .where(Transaction.company_id == company_id, Transaction.account_type.is_(None))
        .group_by(Transaction.is_inflow)
    )
    if start is not None:
        stmt = stmt.where(Transaction.date >= start)
    if end is not None:
        stmt = stmt.where(Transaction.date <= end)

    out = {
        "count": 0,
        "count_in": 0, "money_in": ZERO,          # entró plata (crédito/depósito)
        "count_out": 0, "money_out": ZERO,        # salió plata (débito/pago)
        "count_unknown": 0, "money_unknown": ZERO,  # el banco no dijo la dirección
    }
    for is_inflow, n, total in session.exec(stmt).all():
        n = n or 0
        total = total or ZERO
        suffix = "in" if is_inflow is True else "out" if is_inflow is False else "unknown"
        out[f"count_{suffix}"] = n
        out[f"money_{suffix}"] = total
        out["count"] += n

    # Efecto en CAJA de lo pendiente (entradas − salidas). NO es "la utilidad que falta": a dónde
    # va cada fila lo decide su `account_type`, y justamente eso es lo que todavía no existe.
    out["net_cash"] = out["money_in"] - out["money_out"]
    return out


def income_statement(
    session: Session,
    company_id: int,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> dict[str, object]:
    """Construye el P&L con la aritmética estándar + el desglose por categoría de cada sección.
    NON_PL se ignora. Opcional: rango de fechas.

    `uncategorized` NO entra en ninguna suma: viaja aparte para que la pantalla, el PDF y el Excel
    lo **declaren**. Un consumidor que lo ignore muestra un número incompleto."""
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
        # PENDIENTE: lo que no tiene sección. Fuera de la aritmética, pero visible en el reporte.
        "uncategorized": _uncategorized(session, company_id, start, end),
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
        u = pnl["uncategorized"]
        label = "= Net Income" if u["count"] == 0 else "= Net Income (before pending)"
        print(f"{label:24}{money(pnl['net_income'])}")

        # Lo Pendiente se declara SIEMPRE que exista, también en la salida de consola.
        if u["count"]:
            print(f"\n  PENDING — not classified yet ({u['count']} movements, NOT in the result above)")
            print(f"    Money in   ({u['count_in']:>3})      {money(u['money_in'])}")
            print(f"    Money out  ({u['count_out']:>3})      {money(u['money_out'])}")
            if u["count_unknown"]:
                print(f"    Unknown direction ({u['count_unknown']})  {money(u['money_unknown'])}")
            print(f"    Net effect on cash     {money(u['net_cash'])}")

        count = monthly_transaction_count(session, company.id, 2026, 3)
        print(f"\nTransacciones en 2026-03: {count}  (gate Free: ≤ 50/mes)")


# ─────────────────────────────────────────────────────────────────────────────────────────────
# EL PIVOTE (FASE 10, paso 7) — el drill-down de 4 niveles que pidió Jose:
#   categoría → (+) subcategoría → (+) meses → (+) día exacto + descripción
#
# REGLA DE ORO: los totales los calcula SQL (`func.sum` con `GROUP BY`), nunca Python ni la IA.
# Python solo ANIDA lo que SQL ya sumó — no re-suma montos sueltos.
# ─────────────────────────────────────────────────────────────────────────────────────────────

def pivot(
    session: Session,
    company_id: int,
    start: Optional[date] = None,
    end: Optional[date] = None,
    account: "Optional[list[str] | str]" = None,
) -> list[dict]:
    """Árbol del pivote con los totales agregados en SQL.

    Un solo `GROUP BY` al nivel MÁS PROFUNDO (sección, categoría, subcategoría, mes) y luego se
    acumulan esos subtotales hacia arriba: así el número de un padre es siempre la suma exacta de
    sus hijos y no hay dos caminos para calcular lo mismo.

    `NON_PL` se excluye: transferencias, retiros del dueño y pagos de tarjeta no son resultados
    (misma decisión que el estado de resultados).
    """
    month = func.to_char(Transaction.date, "YYYY-MM")
    stmt = (
        select(
            Transaction.account_type, Transaction.category, Transaction.subcategory,
            month.label("month"), func.sum(Transaction.amount), func.count(Transaction.id),
        )
        .where(Transaction.company_id == company_id, Transaction.account_type != AccountType.NON_PL)
        .group_by(Transaction.account_type, Transaction.category, Transaction.subcategory, month)
    )
    if start is not None:
        stmt = stmt.where(Transaction.date >= start)
    if end is not None:
        stmt = stmt.where(Transaction.date <= end)
    accounts = [account] if isinstance(account, str) else (account or [])
    if accounts:  # una o varias cuentas (el filtro con casillas de la hoja, 4-oct)
        stmt = stmt.where(Transaction.bank_account.in_(accounts))

    tree: dict[tuple, dict] = {}
    for at, category, subcategory, m, total, n in session.exec(stmt).all():
        section = at.value if at else "UNCATEGORIZED"
        cat = category or "Other"
        sub = subcategory or "—"
        total = total or ZERO

        node = tree.setdefault((section, cat), {
            "key": f"{section}|{cat}", "level": "category",
            "section": section, "label": cat,
            "amount": ZERO, "count": 0, "children": {},
        })
        node["amount"] += total; node["count"] += n

        sub_node = node["children"].setdefault(sub, {
            "key": f"{section}|{cat}|{sub}", "level": "subcategory", "label": sub,
            "amount": ZERO, "count": 0, "children": {},
        })
        sub_node["amount"] += total; sub_node["count"] += n

        sub_node["children"][m] = {
            "key": f"{section}|{cat}|{sub}|{m}", "level": "month", "label": m,
            "amount": total, "count": n, "children": None,   # None = las hojas se piden aparte
        }

    def listify(node: dict) -> dict:
        kids = node.get("children")
        if isinstance(kids, dict):
            node["children"] = sorted(
                (listify(k) for k in kids.values()),
                key=lambda k: k["label"], reverse=(node["level"] == "subcategory"),
            )
        return node

    return sorted((listify(n) for n in tree.values()),
                  key=lambda n: (n["section"], -(n["amount"] or ZERO)))


def pivot_leaves(
    session: Session, company_id: int, section: str, category: str,
    subcategory: str, month: str, account: Optional[list[str]] = None,
) -> list[dict]:
    """Las transacciones de UNA hoja del pivote (el último `+`: día exacto + descripción).

    Se piden aparte y solo cuando el bookkeeper abre ese nodo — un árbol con todas las
    transacciones de todos los meses sería enorme y casi siempre desperdiciado.
    """
    stmt = (
        select(Transaction)
        .where(
            Transaction.company_id == company_id,
            func.to_char(Transaction.date, "YYYY-MM") == month,
            Transaction.category == (None if category == "Other" else category),
        )
        .order_by(Transaction.date)
    )
    if category == "Other":
        stmt = select(Transaction).where(
            Transaction.company_id == company_id,
            func.to_char(Transaction.date, "YYYY-MM") == month,
            Transaction.category.is_(None),
        ).order_by(Transaction.date)

    if account:  # el mismo filtro de cuentas que el árbol (antes las hojas mostraban todas)
        stmt = stmt.where(Transaction.bank_account.in_(account))
    rows = session.exec(stmt).all()
    want_sub = None if subcategory == "—" else subcategory
    return [{
        "id": t.id, "date": t.date.isoformat(), "description": t.description,
        "amount": t.amount, "is_inflow": t.is_inflow, "subcategory": t.subcategory,
    } for t in rows
        if (t.subcategory or None) == want_sub
        and (t.account_type.value if t.account_type else "UNCATEGORIZED") == section]
