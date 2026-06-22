import { useTranslation } from "react-i18next";
import { money } from "../api";
import { PNL } from "../accounting";
import type { CategoryAmount, IncomeStatement as IS } from "../types";

function Row({ label, value, strong, indent }: {
  label: string; value: number; strong?: boolean; indent?: boolean;
}) {
  return (
    <div
      className={[
        "flex justify-between py-1.5 text-sm",
        strong ? "font-semibold border-t border-slate-200 pt-2 text-slate-900" : "text-slate-600",
        indent ? "ps-3" : "",
      ].join(" ")}
    >
      <span>{label}</span>
      <span className="tabular-nums">{money(value)}</span>
    </div>
  );
}

// Líneas de categoría debajo de una sección. El nombre contable va en inglés (regla de oro);
// si el idioma ≠ inglés, se muestra una traducción translúcida al lado.
function CatLines({ rows }: { rows?: CategoryAmount[] }) {
  const { t, i18n } = useTranslation();
  const showTr = (i18n.language || "en").split("-")[0] !== "en";
  if (!rows || rows.length === 0) return null;
  return (
    <div className="ps-5 pb-1">
      {rows.map((r) => {
        const tr = showTr ? t(`cat.${r.category}`, { defaultValue: "" }) : "";
        return (
          <div key={r.category} className="flex justify-between py-1 text-xs text-slate-500">
            <span className="truncate">
              {r.category}
              {tr && <span className="ms-1 text-slate-400/60 italic">· {tr}</span>}
            </span>
            <span className="tabular-nums">{money(r.amount)}</span>
          </div>
        );
      })}
    </div>
  );
}

// Estado de resultados (P&L) DETALLADO: cada sección con su desglose por categoría.
// Términos contables en inglés (no se traducen); los montos los calcula SQL en el backend.
export default function IncomeStatement({ data }: { data: IS }) {
  const b = data.breakdown ?? {};
  return (
    <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-5">
      <h2 className="font-semibold text-slate-900 mb-3">{PNL.title}</h2>

      <Row label={PNL.revenue} value={data.revenue} />
      <CatLines rows={b.REVENUE} />

      <Row label={PNL.cogs} value={data.cogs} indent />
      <CatLines rows={b.COGS} />

      <Row label={PNL.grossProfit} value={data.gross_profit} strong />

      <Row label={PNL.opex} value={data.operating_expenses} indent />
      <CatLines rows={b.OPERATING_EXPENSE} />

      <Row label={PNL.operatingIncome} value={data.operating_income} strong />

      <Row label={PNL.otherIncome} value={data.other_income} indent />
      <CatLines rows={b.OTHER_INCOME} />

      <Row label={PNL.otherExpense} value={data.other_expense} indent />
      <CatLines rows={b.OTHER_EXPENSE} />

      <div className="flex justify-between items-center mt-3 pt-3 border-t-2 border-brand-green">
        <span className="font-bold text-brand-green">{PNL.netIncome}</span>
        <span className="font-bold text-brand-green text-lg tabular-nums">{money(data.net_income)}</span>
      </div>
    </div>
  );
}
