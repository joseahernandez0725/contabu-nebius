// Tipos compartidos del frontend (espejo de lo que devuelve el backend).

export type AccountType =
  | "REVENUE"
  | "COGS"
  | "OPERATING_EXPENSE"
  | "OTHER_INCOME"
  | "OTHER_EXPENSE"
  | "NON_PL";

export interface Transaction {
  id: number;
  company_id: number;
  date: string;
  description: string;
  amount: string; // viene como string (Decimal); convertir con Number()
  is_inflow: boolean | null; // dirección real del banco: true = entró plata, false = salió
  bank_account: string | null; // cuenta bancaria de origen (la detecta la IA del archivo)
  check_number: string | null; // número de cheque (el banco lo da; el beneficiario no)
  account_type: AccountType | null;
  category: string | null;
  subcategory: string | null;
  confidence: number | null;
  needs_review: boolean;
  review_question: string | null;
  ai_categorized: boolean;
  // Quién categorizó (procedencia). Filas anteriores a FASE 9: banda/transferencia
  // quedaron guardadas como "rule" (eran indistinguibles).
  source: "rule" | "band" | "transfer" | "ai" | "manual" | null;
  /** Texto que el bookkeeper AGREGA a la descripción del banco. No la reemplaza: `description`
   *  es la evidencia del extracto y queda intacta. Entra al matcheo de reglas — por eso un cheque
   *  con destinatario escrito ("CHECK 1234" + "JOE'S PLUMBING") ya puede tener regla. */
  description_extra: string | null;
  /** Nota LIBRE del bookkeeper sobre esta línea. No es una `SheetLabel` (catálogo reutilizable)
   *  ni la observación (la escribe el agente): es lo que él quiere recordar de ESA transacción. */
  note: string | null;
  matched_rule_id: number | null; // QUÉ regla decidió (referencia suave; null si no fue una regla)
  /** Banco vinculado (Plaid): su id del movimiento, y si el banco lo RETIRÓ después de registrado
   *  (no se borra solo: el bookkeeper decide mantenerlo o eliminarlo). */
  plaid_transaction_id?: string | null;
  bank_removed_at?: string | null;
}

/** Etiqueta CON SIGNIFICADO que el bookkeeper define ("falta el recibo"). Es dato: se filtra
 *  y la IA la puede leer — a diferencia del formato, que son solo píxeles. */
export interface SheetLabel { id: number; name: string; color: string; }

/** Formato VISUAL de una celda (presentación pura, no entra al P&L). */
export interface CellFormat {
  bold?: boolean; italic?: boolean;
  bg?: string | null; fg?: string | null;
  font_size?: number | null; font_family?: string | null;
}

/** Fila de la hoja: la transacción + sus etiquetas + su formato.
 *  En `formats`, la clave es la columna; `_row` significa la fila entera. */
export type SheetRow = Transaction & {
  labels: SheetLabel[];
  formats: Record<string, CellFormat>;
};

export interface Company {
  id: number;
  name: string;
  sector: string;
  tax_form?: "schedule_c" | "1120s"; // cómo tributa el libro (Movimientos, 18-oct): decide su plan de cuentas
  plan: string;
  settlement_buffer_days: number | null; // días de retención; null = default del sistema
  subscription_status?: string | null; // "active" · "canceled" · null = plan gratis
  tx_quota?: number | null; // cupo mensual del tier pagado (100/200/400/1000); null = gratis
  created_at: string;
  role?: "OWNER" | "MEMBER" | "VIEWER" | "CLIENT"; // rol del usuario actual sobre esta empresa (CLIENT = dueño del negocio, FASE 13)
  profile?: CompanyProfile | null; // hechos ya guardados del negocio (la entrevista se eliminó el 2-oct; queda `trade`)
}

// Perfil de la empresa — hechos de la entrevista (valores SIEMPRE en inglés; la UI solo traduce).
export interface CompanyProfile {
  // Oficio en texto libre (sector "other"). Única excepción de idioma: va tal cual al prompt.
  trade?: string;
  uses_subcontractors?: "yes" | "no";
  client_payment_methods?: string[]; // "checks" | "zelle" | "cash" | "card" | "bank_transfer"
  payroll?: "none" | "w2" | "1099" | "both";
  work_vehicles?: "yes" | "no";
  personal_expenses_mixed?: "yes" | "sometimes" | "no";
  // Ronda 2: personas con rol confirmado por el dueño, por firma en mayúsculas.
  people?: Record<string, { name: string; role: string }>;
}

export interface CategoryAmount {
  category: string;
  amount: number;
}

// --- Entregables (FASE 12): lo PENDIENTE que el P&L tiene que declarar ---
// Transacciones sin `account_type`: NO entran en ninguna suma del P&L, pero se muestran siempre.
// Separado por la dirección real del banco porque sin sección no se sabe si suma o resta.
export interface Uncategorized {
  count: number;
  count_in: number;
  money_in: number;
  count_out: number;
  money_out: number;
  count_unknown: number;   // el banco no dijo la dirección: su propio renglón, no se adivina
  money_unknown: number;
  net_cash: number;        // entradas − salidas: efecto en CAJA, no "la utilidad que falta"
}

export interface IncomeStatement {
  revenue: number;
  cogs: number;
  gross_profit: number;
  operating_expenses: number;
  operating_income: number;
  other_income: number;
  other_expense: number;
  net_income: number;
  // Desglose por categoría de cada sección (clave = account_type: REVENUE, COGS, …).
  breakdown?: Record<string, CategoryAmount[]>;
  // Opcional por compatibilidad: un reporte CERRADO antes de la FASE 12 no lo tiene.
  uncategorized?: Uncategorized;
}
