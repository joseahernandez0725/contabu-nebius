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

from sqlalchemy import JSON, Column
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


class Role(str, Enum):
    """Rol de un usuario sobre una empresa (roles y permisos por empresa).

    - OWNER: control total (gestiona el equipo, renombra, elimina). Es `Company.user_id` (implícito).
    - MEMBER: puede editar (subir, categorizar, reglas, P&L), pero NO gestionar equipo ni eliminar.
    - VIEWER: solo lectura (ver P&L/movimientos/reglas); el backend rechaza sus escrituras.
    """
    OWNER = "OWNER"
    MEMBER = "MEMBER"
    VIEWER = "VIEWER"


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


class User(SQLModel, table=True):
    """Usuario autenticado (vía Firebase Auth). Es el dueño de una o varias `Company`.

    `firebase_uid` es la identidad estable que emite Firebase; el backend la saca del ID token
    verificado y nunca confía en datos de la URL para saber quién es (multi-tenant, deuda #2).
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    firebase_uid: str = Field(index=True, unique=True)
    email: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)


class Company(SQLModel, table=True):
    """La empresa del usuario (un negocio inmigrante)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    # Dueño de la empresa (multi-tenant). Nullable por compatibilidad con datos sembrados
    # antes de la auth; al primer login se crea el User y se enlaza su empresa.
    user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    name: str
    sector: str  # construcción / limpieza / restaurante…
    plan: Plan = Field(default=Plan.FREE)
    # Umbrales editables para desambiguar por monto (idea #14). None = usar el default del sistema.
    supermarket_threshold: Optional[Decimal] = Field(default=None, max_digits=14, decimal_places=2)
    gas_station_threshold: Optional[Decimal] = Field(default=None, max_digits=14, decimal_places=2)
    # Ventana de asentamiento (días): se retienen las transacciones recientes/pending hasta que
    # liquidan (base caja). None = usar el default del sistema. Ver docs/NOTAS IMPORTANTES.md.
    settlement_buffer_days: Optional[int] = Field(default=None)
    # --- Suscripción / pagos (Stripe, modelo M5 por volumen de transacciones) ---
    stripe_customer_id: Optional[str] = Field(default=None, index=True)      # cliente en Stripe
    stripe_subscription_id: Optional[str] = Field(default=None, index=True)  # suscripción activa
    # Estado de la suscripción (lo actualiza el webhook): "active" · "past_due" · "canceled" · None=free.
    subscription_status: Optional[str] = Field(default=None)
    # Cupo mensual de transacciones del tier pagado (100/200/400/1000). None = plan gratis (sin IA).
    tx_quota: Optional[int] = Field(default=None)
    # Cuándo ARRANCÓ la suscripción vigente. El cupo mensual cuenta solo desde aquí: lo consumido
    # ANTES (la prueba gratis de 100) no se descuenta del primer mes pagado. Se re-marca solo al
    # pasar a "active" desde otro estado o al cambiar de suscripción — NO en renovaciones.
    subscription_started_at: Optional[datetime] = Field(default=None)
    # PERFIL de la empresa — la ENTREVISTA del bookkeeper (FASE 9 Paso 5). Hechos que solo el dueño
    # sabe, guardados SIEMPRE en inglés (Regla de Oro): uses_subcontractors, client_payment_methods,
    # payroll, work_vehicles, personal_expenses_mixed, y people {firma: {name, role}} de la ronda 2.
    # Se inyectan como HECHOS (no heurísticas) a los prompts de categorización/clarify/proponedor.
    profile: Optional[dict] = Field(default=None, sa_column=Column(JSON))
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
    # Dirección REAL del movimiento en el banco (hecho objetivo, no depende de la categorización):
    # True = entró plata (crédito/depósito); False = salió (débito/pago). Sirve para mostrar el
    # signo +/- y para redactar la pregunta de revisión ("te pagaron" vs "pagaste").
    is_inflow: Optional[bool] = Field(default=None)
    # Cuenta BANCARIA de origen (ej. "Chase Checking", "Savings #4471"). La detecta la IA del
    # propio archivo al subirlo. Una empresa puede tener varias; permite ver/filtrar por cuenta.
    bank_account: Optional[str] = Field(default=None, index=True)
    # Huella de contenido para DEDUPLICAR re-subidas/solapamientos: identifica el movimiento por
    # (date | description normalizada | monto con signo | cuenta). Ver docs/NOTAS IMPORTANTES.md.
    fingerprint: Optional[str] = Field(default=None, index=True)
    # Número de CHEQUE (si el movimiento es un cheque). El banco da el número pero NO el beneficiario;
    # se guarda para identificar el cheque (tu registro) y preguntar en revisión a qué corresponde.
    check_number: Optional[str] = Field(default=None, index=True)

    # --- Datos que RELLENA ContaBu / la IA ---
    account_type: Optional[AccountType] = Field(default=None)  # sección del P&L (o NON_PL)
    category: Optional[str] = Field(default=None)              # cuenta GENÉRICA (Materials, Rent…)
    subcategory: Optional[str] = Field(default=None)           # comercio/empresa específico (Home Depot…)
    confidence: Optional[float] = Field(default=None)          # confianza de la IA (0-1)
    needs_review: bool = Field(default=False)                  # la IA dudó → atención humana
    review_question: Optional[str] = Field(default=None)       # pregunta para el usuario (inglés)
    ai_categorized: bool = Field(default=False)
    # Quién categorizó (procedencia): "rule" (regla de la tabla) | "band" (banda de monto) |
    # "transfer" (transferencia detectada) | "ai" | "manual". Filas viejas: band/transfer
    # quedaron como "rule" (eran indistinguibles antes de FASE 9).
    source: Optional[str] = Field(default=None)
    # Predicción ORIGINAL de la IA (FASE 9 Paso 6): se escribe UNA vez cuando la IA categoriza y
    # NUNCA se toca al corregir. Comparar contra la categoría final (la verdad del dueño) da la
    # precisión ONLINE del cerebro en producción — sin necesidad de un set de oro aparte.
    ai_original_account_type: Optional[str] = Field(default=None)
    ai_original_category: Optional[str] = Field(default=None)
    # QUÉ regla decidió esta transacción (procedencia exacta, FASE 9 Paso 1). Referencia SUAVE
    # (sin FK a propósito): si la regla se edita/borra, el historial de "qué actuó en su momento"
    # se conserva; el frontend muestra "regla eliminada" si ya no existe. Alimenta al auditor
    # (señal de desvío exacta) y la higiene de reglas muertas.
    matched_rule_id: Optional[int] = Field(default=None, index=True)

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
    # Subcategoría (el comercio/contraparte presentable) que asigna la regla. None → se usa el
    # match_text (comportamiento histórico). Permite keyword técnico + nombre limpio ("HD SUPPLY
    # CONST" → subcategoría "Home Depot Supply").
    subcategory: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)


class LearnedMerchant(SQLModel, table=True):
    """Comercio (gasolinera/supermercado) que la IA DESCUBRIÓ y se aprende para que la **banda de
    monto** lo reconozca en las próximas subidas SIN volver a llamar a la IA (ahorro + consistencia).

    Igual que `CategoryRule`, el `scope` separa lo privado de la empresa (`COMPANY`) de lo compartido
    por sector (`SECTOR`). Aprender en la **DB** (no en la lista hardcodeada de Python) es a propósito:
    así un error de la IA NO contamina a TODAS las empresas en silencio — por defecto es `COMPANY`
    (privado), y promover a `SECTOR` (compartido) es un paso aparte y confirmado.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    scope: RuleScope = Field(default=RuleScope.COMPANY)
    company_id: Optional[int] = Field(
        default=None, foreign_key="company.id", index=True
    )  # null si es de SECTOR
    sector: Optional[str] = Field(default=None)  # se usa cuando scope == SECTOR
    match_text: str  # frase normalizada del comercio (aparece en la descripción del banco)
    kind: str        # "gas_station" | "supermarket"
    created_at: datetime = Field(default_factory=_utcnow)


class CompanyCategory(SQLModel, table=True):
    """Categoría PERSONALIZADA que una empresa agrega a su plan de cuentas (P&L personalizable, v1).

    Se MEZCLA con el plan base del sector (ver `categories.categories_for_company`): aparece en el
    selector de corrección y en el desglose del P&L. v1 = solo AGREGAR (no renombrar/ocultar/reordenar).
    El `name` es el término contable (en inglés, regla de oro); el frontend lo traduce visualmente.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    account_type: AccountType  # sección del P&L a la que pertenece la categoría
    name: str                  # nombre de la cuenta (inglés)
    created_at: datetime = Field(default_factory=_utcnow)


class CompanyMember(SQLModel, table=True):
    """Miembro INVITADO de una empresa (roles y permisos por empresa).

    El **dueño** (OWNER) es `Company.user_id` y NO se guarda aquí. Esta tabla son los invitados
    (MEMBER/VIEWER) — el caso de una firma de bookkeeping que da acceso a su equipo. `user_id` es
    null mientras la invitación está **pendiente** (solo se conoce el email); al hacer login con ese
    email se enlaza el `user_id` (ver `auth.current_user`). Único por (company_id, email).
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    email: str = Field(index=True)  # email del invitado (para enlazar al hacer login)
    role: Role = Field(default=Role.MEMBER)  # MEMBER | VIEWER (OWNER es el Company.user_id)
    created_at: datetime = Field(default_factory=_utcnow)


class PnlReport(SQLModel, table=True):
    """Reporte de P&L guardado como 'proyecto'. Flujo de DOS estados:
    - **draft**: se RECALCULA en vivo al abrirlo (refleja los datos actuales); `snapshot` = None.
    - **closed**: CONGELADO; se guarda el P&L ya calculado en `snapshot` (JSON) y no cambia aunque
      se suban más transacciones del período. Es el cierre profesional para presentar/declarar.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    name: str
    start_date: Optional[date] = Field(default=None)
    end_date: Optional[date] = Field(default=None)
    status: str = Field(default="draft")             # "draft" | "closed"
    snapshot: Optional[str] = Field(default=None)    # JSON del P&L congelado (solo si closed)
    created_at: datetime = Field(default_factory=_utcnow)
    closed_at: Optional[datetime] = Field(default=None)
