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
    - CLIENT: el DUEÑO DEL NEGOCIO (FASE 13). No es un VIEWER con permisos extra ni un MEMBER con
      menos: es otra superficie. APORTA datos crudos (su extracto; mañana, respuestas a preguntas),
      NO ve el libro en construcción (decisión del 30-sep: lo recibe cuando el bookkeeper se lo envía)
      y NO toca el criterio contable. Solo nace de una INVITACIÓN: registrarse crea una firma.
    """
    OWNER = "OWNER"
    MEMBER = "MEMBER"
    VIEWER = "VIEWER"
    CLIENT = "CLIENT"


class FirmRole(str, Enum):
    """Rol de un usuario dentro de la OFICINA (`Firm`). Aplica a TODOS los libros de la firma — es la
    diferencia con `Role`, que es por empresa: se invita UNA vez y se ven las 40, no 40 invitaciones.

    - OWNER: dueño de la oficina (factura, agrega gente, crea y elimina libros).
    - BOOKKEEPER: trabaja todos los libros (subir, categorizar, reglas); no elimina ni factura.
    - VIEWER: lee todos los libros (un socio, un revisor); el backend rechaza sus escrituras.
    """
    OWNER = "OWNER"
    BOOKKEEPER = "BOOKKEEPER"
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
    # OFICINA a la que pertenece el libro (FASE 13). Es el camino de acceso normal: quien está en la
    # firma entra a TODOS sus libros. Nullable solo por la migración; el backfill lo llena.
    firm_id: Optional[int] = Field(default=None, foreign_key="firm.id", index=True)
    # Dueño de la empresa (multi-tenant). Nullable por compatibilidad con datos sembrados
    # antes de la auth; al primer login se crea el User y se enlaza su empresa.
    # NO se borra al llegar `firm_id`: queda como el CREADOR del libro y como acceso de respaldo.
    user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    name: str
    sector: str  # construcción / limpieza / restaurante…
    plan: Plan = Field(default=Plan.FREE)
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
    # TEXTO QUE EL BOOKKEEPER AGREGA a la descripción del banco (FASE 10). NO la reemplaza: el
    # extracto es la EVIDENCIA y se conserva intacto en `description`. Se usa sobre todo en cheques
    # y líneas ACH crípticas ("CHECK 1234" → + "JOE'S PLUMBING"), y entra al MATCHEO DE REGLAS —
    # que es el punto: un cheque con destinatario escrito ya puede tener regla.
    description_extra: Optional[str] = Field(default=None)

    # NOTA del bookkeeper sobre ESTA línea (FASE 10). Distinta de `SheetLabel`, que es un catálogo
    # reutilizable: la nota es texto libre y única de esta transacción ("el cliente dijo que el
    # recibo lo manda el lunes"). Distinta también de `observation`, que la escribe el agente.
    note: Optional[str] = Field(default=None)
    matched_rule_id: Optional[int] = Field(default=None, index=True)
    # BANCO VINCULADO (FASE 10, 4-oct): el `transaction_id` de Plaid. Solo lo tienen las filas que
    # vinieron de un banco vinculado; con él, re-sincronizar deduplica exacto y los avisos del banco
    # (`modified` / `removed`) se aplican a LA fila que corresponde.
    plaid_transaction_id: Optional[str] = Field(default=None, index=True)
    # El banco RETIRÓ este movimiento después de registrarlo. No se borra en silencio: se marca, se
    # ve en la Hoja y el bookkeeper decide (mantenerlo o eliminarlo).
    bank_removed_at: Optional[datetime] = Field(default=None)

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
    # Prioridad MANUAL (1 = primero, como subir una fila en Excel). None → orden automático (la más
    # larga gana; empate → alfabético). Las que tienen número van ANTES que todas las automáticas.
    priority: Optional[int] = Field(default=None)
    # Cómo se VE su fila en la hoja de Reglas: {bg, bold, italic} (4-oct). Solo presentación.
    style: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    # VENTANA DE FECHAS (4-oct, "la fórmula del libro"): la regla vale solo para movimientos con fecha
    # entre estas dos, INCLUSIVAS. None = sin límite por ese lado. Con ventana, a igual palabra clave
    # gana la regla con ventana (es la excepción: "STARBUCKS del 3 al 17-ago → Owner Draw").
    valid_from: Optional[date] = Field(default=None)
    valid_to: Optional[date] = Field(default=None)
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


class SheetLabel(SQLModel, table=True):
    """Etiqueta CON SIGNIFICADO que el bookkeeper define para su empresa (la hoja, FASE 10).

    Es el "resaltar en amarillo" de Excel, pero como DATO: un bookkeeper no pinta una celda por
    decoración — la pinta porque significa algo ("falta el recibo", "preguntar al cliente"). Guardado
    como etiqueta se puede **filtrar**, la **IA la puede leer** y sale en reportes; guardado como
    color suelto serían solo píxeles. El formato libre de celda vive aparte, en `CellFormat`.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    name: str                      # lo que significa, escrito por el bookkeeper
    color: str = Field(default="#f4b400")   # hex, para pintar la fila/celda
    created_at: datetime = Field(default_factory=_utcnow)


class TransactionLabel(SQLModel, table=True):
    """Una `SheetLabel` aplicada a una transacción (una transacción puede tener varias)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    transaction_id: int = Field(foreign_key="transaction.id", index=True)
    label_id: int = Field(foreign_key="sheetlabel.id", index=True)
    created_at: datetime = Field(default_factory=_utcnow)


class CellFormat(SQLModel, table=True):
    """Formato VISUAL libre de una celda — negrilla, cursiva, colores, tamaño y tipo de letra.

    Es presentación, no dato contable: no entra al P&L ni lo lee la IA. Existe porque el bookkeeper
    viene de Excel y ahí marca cosas a su manera. Para lo que SÍ debe poder filtrarse o leer un
    agente está `SheetLabel`. `col` = `None` significa la fila entera.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    transaction_id: int = Field(foreign_key="transaction.id", index=True)
    # `col`, no `column`: "column" es palabra reservada en SQL y rompe cualquier consulta manual.
    col: Optional[str] = Field(default=None, index=True)  # date|description|amount|category|subcategory|None
    bold: bool = Field(default=False)
    italic: bool = Field(default=False)
    bg: Optional[str] = Field(default=None)          # color de fondo (hex)
    fg: Optional[str] = Field(default=None)          # color de texto (hex)
    font_size: Optional[int] = Field(default=None)   # px
    font_family: Optional[str] = Field(default=None)
    updated_at: datetime = Field(default_factory=_utcnow)


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


# ==================================================================================================
# FASE 13 — LA OFICINA (área `Firm / bookkeepers`). Ver docs/FASE 13.md.
# El nivel que FALTABA: hasta aquí la jerarquía era User → Company, así que los sectores, las reglas
# compartidas y las preferencias no tenían dónde separarse — o estaban pegados a una empresa, o eran
# globales para todos.
# ==================================================================================================


class Firm(SQLModel, table=True):
    """La oficina de bookkeeping: agrupa a los bookkeepers que trabajan y los libros que llevan.

    **Registrarse crea una firma de UN usuario** (FASE 13 Paso 4): todo el que llega por la puerta es
    bookkeeper de sí mismo, con todas las herramientas. El rol `CLIENT` no se puede alcanzar
    registrándose — solo llega por invitación (`CompanyMember`). Así "cliente" es una RELACIÓN, no un
    tipo de cuenta, y no existe un tercer estado que resolver.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    owner_user_id: int = Field(foreign_key="user.id", index=True)

    # --- EL SELLO de la firma (decisión de Jose, 27-sep-2026) ---
    # "si es cliente mío sí puede aparecer ContaBu; si no es cliente mío, va a aparecer la marca de la
    # persona que gestiona esa cuenta". Se implementa como UNA regla sin casos especiales: lo que el
    # cliente ve es la marca de SU firma. La firma de Jose se llama ContaBu, y por eso su cliente ve
    # ContaBu — no hay ningún `if` que pregunte "¿es la firma de Jose?".
    # None → se cae al `name` de la firma. Lo consume la plataforma del cliente (FASE 13 Paso 9) y el
    # encabezado del PDF/Excel de la FASE 12: un solo sello, dos usos.
    brand_name: Optional[str] = Field(default=None)
    brand_logo_url: Optional[str] = Field(default=None)
    brand_color: Optional[str] = Field(default=None)   # hex, acento de la UI del cliente
    # DEMO (3-oct): cuándo se BORRA esta oficina. Solo la tienen las oficinas nacidas en el despliegue
    # de demo (`DEMO_MODE`); una oficina real la tiene en NULL. La limpieza (`demo.py`) SOLO borra filas
    # con esta marca vencida — así un servicio de demo mal apuntado a la base real no puede borrar nada.
    demo_expires_at: Optional[datetime] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=_utcnow)


class FirmMember(SQLModel, table=True):
    """Quién trabaja en la oficina (y con qué rol sobre TODOS sus libros).

    Mismo patrón que `CompanyMember`, un nivel más arriba: `user_id` es null mientras la invitación
    está **pendiente** (solo se conoce el email) y se enlaza al hacer login con ese correo (ver
    `auth.current_user`). Único por (firm_id, email).
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    firm_id: int = Field(foreign_key="firm.id", index=True)
    user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    email: str = Field(index=True)  # email del invitado (para enlazar al hacer login)
    role: FirmRole = Field(default=FirmRole.BOOKKEEPER)
    created_at: datetime = Field(default_factory=_utcnow)


# ── Movimientos (FASE 10) — historial de correcciones ──────────────────────────────────────────

class CategorizationChange(SQLModel, table=True):
    """Una CORRECCIÓN de categorización: el par ANTES → DESPUÉS de una transacción ya registrada.

    Por qué existe (2-oct-2026): Jose va a operar el libro real como primer bookkeeper y a construir
    a mano la tabla de reglas; luego los agentes aprenden de lo que él corrigió. `source="manual"`
    dice QUE lo tocó un humano, no QUÉ decía antes — y eso es lo único que no se puede reconstruir
    después. (Los `ai_original_*` de la FASE 9 solo se llenan cuando categoriza la IA, que está
    apagada.)

    Se escribe una fila SOLO cuando cambia de verdad `account_type`, `category` o `subcategory`
    (`corrections.record_change`). La categorización INICIAL del motor al subir no se registra: eso
    ya lo dicen `source` y `matched_rule_id`.

    Sin claves foráneas, a propósito (como `matched_rule_id`): es historia, no debe impedir borrar
    una transacción o una regla. `account_type_*` se guarda como texto por la misma razón.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    transaction_id: int = Field(index=True)        # referencia SUAVE a transaction.id
    company_id: int = Field(index=True)            # para medir por libro
    changed_at: datetime = Field(default_factory=_utcnow, index=True)
    changed_by_user_id: Optional[int] = Field(default=None, index=True)  # None = lo cambió el sistema
    # Por dónde llegó el cambio (código estable, en inglés, para agrupar y medir):
    # manual · follow_rule · rule_update · rule_applied · rule_precedence · undo · bank_modified
    via: str = Field(index=True)
    source_before: Optional[str] = Field(default=None)   # rule | transfer | ai | manual | band | None
    source_after: Optional[str] = Field(default=None)
    account_type_before: Optional[str] = Field(default=None)
    account_type_after: Optional[str] = Field(default=None)
    category_before: Optional[str] = Field(default=None)
    category_after: Optional[str] = Field(default=None)
    subcategory_before: Optional[str] = Field(default=None)
    subcategory_after: Optional[str] = Field(default=None)
    matched_rule_id_before: Optional[int] = Field(default=None)  # qué regla había decidido: señal de regla mala
    matched_rule_id_after: Optional[int] = Field(default=None)   # qué regla la tomó (si fue una)
    reason: Optional[str] = Field(default=None)                  # texto libre, opcional


class BankConnection(SQLModel, table=True):
    """Un BANCO VINCULADO a un libro por Plaid (FASE 10, Movimientos · 3-oct-2026).

    Una fila por Item de Plaid (una institución, una o varias cuentas). Es POR LIBRO: el banco del
    cliente alimenta el libro de ese cliente, no la oficina.

    🔒 `access_token_enc` es el access_token de Plaid CIFRADO (Fernet, clave en `BANK_TOKEN_KEY`).
    Nunca se escribe en logs ni se devuelve al navegador: solo `bank.py` lo descifra, para llamar a
    Plaid desde el servidor. `next_cursor` = dónde quedó `/transactions/sync` (no es secreto).
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    environment: str                                   # sandbox | production
    item_id: str = Field(index=True)                   # Item de Plaid (único por libro)
    institution_name: Optional[str] = Field(default=None)
    access_token_enc: str
    # Las cuentas del Item: [{id, name, mask, type, subtype}]
    accounts: list = Field(default_factory=list, sa_column=Column(JSON))
    status: str = Field(default="active")              # active | disconnected
    last_error: Optional[str] = Field(default=None)    # error_code de Plaid, p. ej. ITEM_LOGIN_REQUIRED
    next_cursor: Optional[str] = Field(default=None)
    last_synced_at: Optional[datetime] = Field(default=None)
    created_by_user_id: Optional[int] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)


class BookNote(SQLModel, table=True):
    """Una NOTA DEL LIBRO (FASE 11, Auditoría / IA · 4-oct-2026): lo que el bookkeeper le escribe al
    agente sobre ESTE cliente — lo que hay que recordar y lo prioritario.

    Es la entrada del agente ④ (el redactor de "la fórmula del libro") y reemplaza a las preguntas
    del comienzo (la entrevista, eliminada el 2-oct). 🔴 Una nota NUNCA categoriza: el agente la lee
    y PROPONE filas de la fórmula que el bookkeeper aprueba.

    `kind`: "keep_in_mind" (📌 tenlo en cuenta) | "dont_report" (🚫 no me reportes).
    `priority`: "high" | "normal" — las altas van primero al agente y arriba en la pantalla.
    `merchant`: alcance opcional (huella o palabra del comercio). Con él, un 🚫 se puede filtrar en
    código sin depender de que el modelo obedezca.
    `valid_from` / `valid_to`: vigencia opcional, INCLUSIVA (p. ej. "vacaciones del 3 al 17-ago").
    `created_by_user_id` sin clave foránea, a propósito (como `matched_rule_id`): borrar un usuario no
    debe borrar ni impedir el criterio del libro.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    kind: str = Field(default="keep_in_mind")
    priority: str = Field(default="normal")
    text: str
    merchant: Optional[str] = Field(default=None)
    valid_from: Optional[date] = Field(default=None)
    valid_to: Optional[date] = Field(default=None)
    created_by_user_id: Optional[int] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class AgentCall(SQLModel, table=True):
    """UNA llamada de un agente a Nemotron (FASE 11, Auditoría / IA · 7-oct-2026): lo que costó y lo que
    salió. Existe para medir desde el principio (tokens, costo, latencia) y para el TOPE de llamadas
    por oficina del demo. Nunca guarda el contenido del prompt ni de la respuesta (privacidad).

    `agent`: "drafter" (④ redactor de la fórmula) | "reviewer" (① revisor).
    `outcome`: "ok" o el código de error (`out_of_tokens`, `not_json`, `http_500`…).
    `items_out` / `items_valid`: filas que propuso el modelo / las que pasaron el validador (④), o
    hallazgos revisados (①). `firm_id` y `user_id` sin clave foránea (historia, como `matched_rule_id`).
    `reasoning_tokens_est` es estimado (Nebius no los separa).
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    firm_id: Optional[int] = Field(default=None, index=True)
    user_id: Optional[int] = Field(default=None)
    agent: str = Field(index=True)
    model: str
    reasoning: bool = Field(default=False)
    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    reasoning_tokens_est: int = Field(default=0)
    cost_usd: Optional[float] = Field(default=None)
    latency_ms: int = Field(default=0)
    finish_reason: Optional[str] = Field(default=None)
    outcome: str = Field(default="ok")
    items_out: int = Field(default=0)
    items_valid: int = Field(default=0)
    credits: int = Field(default=0)   # créditos de Tavily (agente ② verificador); 0 en Nemotron
    created_at: datetime = Field(default_factory=_utcnow, index=True)


class MerchantLookup(SQLModel, table=True):
    """Lo que el ② VERIFICADOR DE COMERCIOS encontró en internet (Tavily) sobre UN comercio
    (FASE 11, Auditoría / IA · 8-oct-2026). Es la CACHÉ: una búsqueda por comercio, guardada.

    🔒 **Por oficina** (`firm_id`): nada cruza entre oficinas (decisión del 04-oct). Un libro sin
    oficina usa `company_id`.
    🔒 Se guarda un **DATO ESTRUCTURADO**, nunca el texto de la página:
    - `business_type`: de una lista cerrada (`merchant_verifier.BUSINESS_TYPES`);
    - `confidence`: high, medium o low;
    - `source_url`: el enlace.
    Ese texto nunca entra al prompt de los agentes (protección contra inyección de instrucciones).
    `query` es exactamente lo que se mandó a Tavily: la huella del comercio, sin montos ni nombres.
    `status`: "ok" | "not_found" | "error".
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    firm_id: Optional[int] = Field(default=None, index=True)
    company_id: Optional[int] = Field(default=None, index=True)
    fingerprint: str = Field(index=True)          # la frase buscada, en mayúsculas (una búsqueda por comercio)
    query: str
    status: str = Field(default="ok")
    business_type: Optional[str] = Field(default=None)
    confidence: Optional[str] = Field(default=None)
    source_url: Optional[str] = Field(default=None)
    source_domain: Optional[str] = Field(default=None)
    results_count: int = Field(default=0)
    credits: int = Field(default=0)
    created_at: datetime = Field(default_factory=_utcnow)
