// Términos CONTABLES — SIEMPRE en inglés, nunca se traducen.
// Regla de Oro: lo contable se mantiene en inglés; solo la interfaz se traduce.

// Etiquetas de account_type (sección del P&L)
export const SECTION_LABEL: Record<string, string> = {
  REVENUE: "Revenue",
  OTHER_INCOME: "Other income",
  COGS: "COGS",
  OPERATING_EXPENSE: "Operating expense",
  OTHER_EXPENSE: "Other expense",
  NON_PL: "Non-P&L",
};

// Estado de resultados (income statement)
export const PNL = {
  title: "Income statement",
  revenue: "Revenue",
  cogs: "− Cost of goods sold (COGS)",
  grossProfit: "Gross profit",
  opex: "− Operating expenses",
  operatingIncome: "Operating income",
  otherIncome: "+ Other income",
  otherExpense: "− Other expenses",
  netIncome: "Net income",
};

// KPIs / héroe
export const KPI = {
  revenue: "Revenue",
  totalExpenses: "Total expenses",
  netIncome: "Net income",
};
