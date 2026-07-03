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
  matched_rule_id: number | null; // QUÉ regla decidió (referencia suave; null si no fue una regla)
}

export interface Company {
  id: number;
  name: string;
  sector: string;
  plan: string;
  supermarket_threshold: string | null; // Decimal → string; null = usar default del sistema
  gas_station_threshold: string | null;
  settlement_buffer_days: number | null; // días de retención; null = default del sistema
  subscription_status?: string | null; // "active" · "canceled" · null = plan gratis
  tx_quota?: number | null; // cupo mensual del tier pagado (100/200/400/1000); null = gratis
  created_at: string;
  role?: "OWNER" | "MEMBER" | "VIEWER"; // rol del usuario actual sobre esta empresa
  profile?: CompanyProfile | null; // la ENTREVISTA del bookkeeper (FASE 9 Paso 5)
}

// Perfil de la empresa — hechos de la entrevista (valores SIEMPRE en inglés; la UI solo traduce).
export interface CompanyProfile {
  uses_subcontractors?: "yes" | "no";
  client_payment_methods?: string[]; // "checks" | "zelle" | "cash" | "card" | "bank_transfer"
  payroll?: "none" | "w2" | "1099" | "both";
  work_vehicles?: "yes" | "no";
  personal_expenses_mixed?: "yes" | "sometimes" | "no";
  // Ronda 2: personas con rol confirmado por el dueño, por firma en mayúsculas.
  people?: Record<string, { name: string; role: string }>;
}

// Pregunta de la RONDA 2 (anclada en los datos): una persona de pagos Zelle/Venmo sin rol confirmado.
export interface InterviewQuestion {
  key: string; // firma (mayúsculas) — se manda de vuelta como `name`
  name: string; // presentable ("Watson Yu")
  count: number;
  total_in: number;
  total_out: number;
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
