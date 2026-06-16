import { money } from "../api";
import type { IncomeStatement as IS } from "../types";

function Row({ label, value, strong, indent }: {
  label: string; value: number; strong?: boolean; indent?: boolean;
}) {
  return (
    <div
      className={[
        "flex justify-between py-1.5 text-sm",
        strong ? "font-semibold border-t border-slate-200 pt-2 text-slate-900" : "text-slate-600",
        indent ? "pl-3" : "",
      ].join(" ")}
    >
      <span>{label}</span>
      <span className="tabular-nums">{money(value)}</span>
    </div>
  );
}

// Estado de resultados (P&L). Presentacional: recibe los datos ya calculados.
export default function IncomeStatement({ data }: { data: IS }) {
  return (
    <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-5">
      <h2 className="font-semibold text-slate-900 mb-3">Estado de resultados</h2>
      <Row label="Ingresos (Revenue)" value={data.revenue} />
      <Row label="− Costo de ventas (COGS)" value={data.cogs} indent />
      <Row label="Utilidad bruta" value={data.gross_profit} strong />
      <Row label="− Gastos operativos" value={data.operating_expenses} indent />
      <Row label="Utilidad operativa" value={data.operating_income} strong />
      <Row label="+ Otros ingresos" value={data.other_income} indent />
      <Row label="− Otros gastos" value={data.other_expense} indent />
      <div className="flex justify-between items-center mt-3 pt-3 border-t-2 border-brand-green">
        <span className="font-bold text-brand-green">Utilidad neta</span>
        <span className="font-bold text-brand-green text-lg tabular-nums">{money(data.net_income)}</span>
      </div>
    </div>
  );
}
