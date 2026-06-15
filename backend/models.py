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


class Company(SQLModel, table=True):
    """La empresa del usuario (un negocio inmigrante)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    sector: str  # construcción / limpieza / restaurante…
    plan: Plan = Field(default=Plan.FREE)
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
    amount: Decimal = Field(max_digits=14, decimal_places=2)  # dinero exacto

    # --- Datos que RELLENA ContaBu / la IA ---
    category: Optional[str] = Field(default=None)
    subcategory: Optional[str] = Field(default=None)  # más detalle que la categoría
    ai_categorized: bool = Field(default=False)

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
    match_text: str  # texto de la descripción que dispara la regla
    category: str    # categoría a asignar
    created_at: datetime = Field(default_factory=_utcnow)
