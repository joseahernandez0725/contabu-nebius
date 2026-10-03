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
}

/** Observación del AGENTE AUDITOR para UNA fila — la 6ª columna de la hoja (FASE 10, paso 6).
 *  Llega con `GET /transactions?with_audit=true`. El `text` viene en INGLÉS (regla de oro);
 *  la UI traduce por `type` usando los campos estructurados, no el texto crudo. */
export interface Observation {
  type: "rule_deviation" | "merchant_inconsistency" | "sign_mismatch" | "uncategorized" | string;
  severity: "high" | "medium" | "low";
  text: string | null;
  account_type?: string | null;   // sign_mismatch
  direction?: "in" | "out";       // sign_mismatch
  merchant?: string | null;       // merchant_inconsistency
  suggested_account_type?: string | null; // rule_deviation → fix de 1 toque
  suggested_category?: string | null;
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

/** Fila de la hoja: la transacción + su observación + sus etiquetas + su formato.
 *  En `formats`, la clave es la columna; `_row` significa la fila entera. */
export type SheetRow = Transaction & {
  observation: Observation | null;
  labels: SheetLabel[];
  formats: Record<string, CellFormat>;
};

export interface Company {
  id: number;
  name: string;
  sector: string;
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
}
