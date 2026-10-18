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

// Familia de la sección para colorear la tabla de reglas: ingreso (verde), egreso (rojo),
// no-P&L (gris). Refuerza visualmente el signo: una regla "roja" con dinero que ENTRÓ es la
// contradicción que el backend manda a revisión (detector de signo).
export type SectionFamily = "income" | "expense" | "nonpl";

export function sectionFamily(accountType: string | null | undefined): SectionFamily {
  if (accountType === "REVENUE" || accountType === "OTHER_INCOME") return "income";
  if (accountType === "COGS" || accountType === "OPERATING_EXPENSE" || accountType === "OTHER_EXPENSE")
    return "expense";
  return "nonpl"; // NON_PL o sin tipo
}

export const FAMILY_STYLE: Record<SectionFamily, { dot: string; text: string; label: string }> = {
  income: { dot: "bg-emerald-500", text: "text-emerald-700", label: "Income" },
  expense: { dot: "bg-rose-500", text: "text-rose-700", label: "Expense" },
  nonpl: { dot: "bg-slate-400", text: "text-slate-500", label: "Non-P&L" },
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
  other: "Other", // FASE 12 (18-oct): cabecera plegada del `compact`, agrupa Other income + Other expense
  netIncome: "Net income",
};

// KPIs / héroe
export const KPI = {
  revenue: "Revenue",
  totalExpenses: "Total expenses",
  netIncome: "Net income",
};
