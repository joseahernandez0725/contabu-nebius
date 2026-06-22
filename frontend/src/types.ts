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
  source: "rule" | "ai" | "manual" | null; // quién categorizó
}

export interface Company {
  id: number;
  name: string;
  sector: string;
  plan: string;
  supermarket_threshold: string | null; // Decimal → string; null = usar default del sistema
  gas_station_threshold: string | null;
  settlement_buffer_days: number | null; // días de retención; null = default del sistema
  created_at: string;
  role?: "OWNER" | "MEMBER" | "VIEWER"; // rol del usuario actual sobre esta empresa
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
