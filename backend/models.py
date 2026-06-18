"""
Modelo de datos de ContaBu (SQLModel).

Cada clase con `table=True` es una tabla en PostgreSQL.
Reglas de oro reflejadas aquí:
- `amount` es Decimal (dinero EXACTO, nunca float).
- `description`, `content` y `category` se guardan en INGLÉS.
- La IA solo RELLENA `category` (no calcula nada).
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    """Fecha/hora actual en UTC (se guarda todo en UTC)."""
    return datetime.now(timezone.utc)


class Plan(str, Enum):
    """Plan de suscripción de la empresa."""
    FREE = "FREE"
    PRO = "PRO"
    PREMIUM = "PREMIUM"


class RuleScope(str, Enum):
    """Alcance de una regla de categorización."""
    SECTOR = "SECTOR"   # compartida por industria (plan Free); crea el moat de datos
    COMPANY = "COMPANY"  # privada de una empresa (plan Pro); la IA la propone


class AccountType(str, Enum):
    """Sección del estado de resultados (P&L). Conjunto ESTÁNDAR y cerrado.

    Es el nivel más alto del plan de cuentas: con él se calcula el P&L
    (Revenue − COGS = Gross Profit; − Operating Expenses = Operating Income…).
    """
    REVENUE = "REVENUE"                       # ingresos / ventas
    COGS = "COGS"                             # Cost of Goods Sold: costos directos
    OPERATING_EXPENSE = "OPERATING_EXPENSE"   # gastos operativos / overhead
    OTHER_INCOME = "OTHER_INCOME"             # otros ingresos (intereses, reembolsos…)
    OTHER_EXPENSE = "OTHER_EXPENSE"           # otros gastos (intereses pagados, impuestos…)
    NON_PL = "NON_PL"                         # NO entra al P&L: transferencias, retiro/aporte del
                                              # dueño, capital de préstamo, pago de tarjeta…


class Company(SQLModel, table=True):
    """La empresa del usuario (un negocio inmigrante)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    sector: str  # construcción / limpieza / restaurante…
    plan: Plan = Field(default=Plan.FREE)
    # Umbrales editables para desambiguar por monto (idea #14). None = usar el default del sistema.
    supermarket_threshold: Optional[Decimal] = Field(default=None, max_digits=14, decimal_places=2)
    gas_station_threshold: Optional[Decimal] = Field(default=None, max_digits=14, decimal_places=2)
    created_at: datetime = Field(default_factory=_utcnow)


class Transaction(SQLModel, table=True):
    """Un movimiento bancario.

    Lo que SUBE el usuario (del archivo bancario, en inglés): date, description, amount.
    Lo que RELLENA ContaBu (la IA): category, subcategory.
    El sector NO va aquí: es un atributo constante de la empresa (Company.sector).
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)

    # --- Datos que SUBE el usuario (vienen del banco) ---
    date: date
    description: str  # en inglés
    # MAGNITUD positiva (valor absoluto). El signo en los cálculos lo determina account_type
    # (REVENUE/OTHER_INCOME suman; COGS/OPERATING_EXPENSE/OTHER_EXPENSE restan; NON_PL se excluye).
    amount: Decimal = Field(max_digits=14, decimal_places=2)

    # --- Datos que RELLENA ContaBu / la IA ---
    account_type: Optional[AccountType] = Field(default=None)  # sección del P&L (o NON_PL)
    category: Optional[str] = Field(default=None)              # cuenta GENÉRICA (Materials, Rent…)
    subcategory: Optional[str] = Field(default=None)           # comercio/empresa específico (Home Depot…)
    confidence: Optional[float] = Field(default=None)          # confianza de la IA (0-1)
    needs_review: bool = Field(default=False)                  # la IA dudó → atención humana
    review_question: Optional[str] = Field(default=None)       # pregunta para el usuario (inglés)
    ai_categorized: bool = Field(default=False)
    source: Optional[str] = Field(default=None)               # quién categorizó: "rule" | "ai" | "manual"

    created_at: datetime = Field(default_factory=_utcnow)


class MonthlyInsight(SQLModel, table=True):
    """Insight mensual pre-calculado (se guarda en inglés; el frontend traduce)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    period: str = Field(index=True)  # "YYYY-MM"
    content: str  # en inglés
    created_at: datetime = Field(default_factory=_utcnow)


class CategoryRule(SQLModel, table=True):
    """Regla de categorización. El `scope` implementa el moat SECTOR vs COMPANY."""
    id: Optional[int] = Field(default=None, primary_key=True)
    scope: RuleScope
    company_id: Optional[int] = Field(
        default=None, foreign_key="company.id", index=True
    )  # null si la regla es de SECTOR
    sector: Optional[str] = Field(default=None)  # se usa en reglas de SECTOR
    match_text: str  # texto/comercio que dispara la regla (ej. "MIKE SMITH")
    account_type: Optional[AccountType] = Field(default=None)  # sección a asignar
    category: str    # categoría a asignar
    created_at: datetime = Field(default_factory=_utcnow)
