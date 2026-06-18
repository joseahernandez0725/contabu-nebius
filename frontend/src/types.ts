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
  created_at: string;
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
}
