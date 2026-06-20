import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { getIncomeStatement, getTransactions, money } from "../api";
import { KPI, SECTION_LABEL } from "../accounting";
import { exportIncomeStatementPdf } from "../pdf";
import type { IncomeStatement as IS, Transaction } from "../types";
import IncomeStatementCard from "./IncomeStatement";

function Kpi({ label, value, tone }: { label: string; value: number; tone: "green" | "rose" }) {
  const color = tone === "green" ? "text-brand-green" : "text-rose-600";
  return (
    <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-4">
      <div className="text-xs text-slate-500">{label}</div>
      <div className={`text-xl font-bold tabular-nums mt-1 ${color}`}>{money(value)}</div>
    </div>
  );
}

function BreakdownBar({ label, value, total, color }: {
  label: string; value: number; total: number; color: string;
}) {
  const pct = total > 0 ? Math.round((value / total) * 100) : 0;
  return (
    <div>
      <div className="flex justify-between text-xs text-slate-600 mb-1">
        <span>{label}</span>
        <span className="tabular-nums">{money(value)} · {pct}%</span>
      </div>
      <div className="h-2 rounded-full bg-slate-100 overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export default function Dashboard({ onUpload, companyName }: {
  onUpload: () => void; companyName: string;
}) {
  const { t } = useTranslation();
  const [is, setIs] = useState<IS | null>(null);
  const [txs, setTxs] = useState<Transaction[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");

  // Las transacciones se cargan una vez (para el empty state y los recientes).
  useEffect(() => {
    getTransactions().then(setTxs).catch((e) => setError(e instanceof Error ? e.message : "Error"));
  }, []);

  // El P&L se recalcula cuando cambia el rango de fechas (no se resetea a null → no parpadea).
  useEffect(() => {
    getIncomeStatement(start || undefined, end || undefined)
      .then(setIs)
      .catch((e) => setError(e instanceof Error ? e.message : "Error"));
  }, [start, end]);

  if (error) return <div className="alert alert-error">{error}</div>;
  if (!is || !txs)
    return (
      <div className="flex justify-center py-16">
        <span className="loading loading-spinner loading-lg text-brand-green" />
      </div>
    );

  // Empty state
  if (txs.length === 0) {
    return (
      <div className="text-center py-12">
        <div
          className="size-16 rounded-2xl mx-auto flex items-center justify-center text-white"
          style={{ background: "linear-gradient(135deg, #15803d, #f4b400)" }}
        >
          <svg viewBox="0 0 24 24" className="size-8" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 16V4M7 9l5-5 5 5" /><path d="M4 17v2a1 1 0 001 1h14a1 1 0 001-1v-2" />
          </svg>
        </div>
        <h2 className="text-xl font-bold text-slate-900 mt-4">{t("empty.welcome")}</h2>
        <p className="text-slate-500 mt-2 max-w-xs mx-auto">{t("empty.subtitle")}</p>
        <button
          onClick={onUpload}
          className="btn mt-6 bg-brand-green text-white border-none hover:bg-brand-green-dark"
        >
          {t("empty.cta")}
        </button>
      </div>
    );
  }

  const expenses = is.cogs + is.operating_expenses + is.other_expense;
  const positive = is.net_income >= 0;

  return (
    <div className="space-y-4">
      {/* Filtro por rango de fechas (acota el P&L) */}
      <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-3 flex items-center gap-2 text-sm flex-wrap">
        <span className="text-slate-500 shrink-0">{t("dashboard.period")}</span>
        <input
          type="date"
          value={start}
          max={end || undefined}
          onChange={(e) => setStart(e.target.value)}
          className="rounded-lg border border-slate-200 px-2 py-1 tabular-nums focus:outline-none focus:ring-2 focus:ring-brand-green/40"
        />
        <span className="text-slate-400">–</span>
        <input
          type="date"
          value={end}
          min={start || undefined}
          onChange={(e) => setEnd(e.target.value)}
          className="rounded-lg border border-slate-200 px-2 py-1 tabular-nums focus:outline-none focus:ring-2 focus:ring-brand-green/40"
        />
        {(start || end) && (
          <button
            onClick={() => { setStart(""); setEnd(""); }}
            className="ms-auto text-brand-green font-medium hover:underline"
          >
            {t("dashboard.allTime")}
          </button>
        )}
      </div>

      {/* Héroe */}
      <div
        className="text-white rounded-3xl shadow-md p-6"
        style={{ background: "linear-gradient(135deg, #15803d, #14532d)" }}
      >
        <div className="text-sm opacity-80">{KPI.netIncome}</div>
        <div className="text-4xl font-bold tabular-nums mt-2">{money(is.net_income)}</div>
        <div className="text-sm mt-1 opacity-80">
          {positive ? t("dashboard.profitPositive") : t("dashboard.profitNegative")}
        </div>
      </div>

      {/* KPIs */}
      <div className="grid grid-cols-2 gap-3">
        <Kpi label={KPI.revenue} value={is.revenue} tone="green" />
        <Kpi label={KPI.totalExpenses} value={expenses} tone="rose" />
      </div>

      {/* Desglose de gastos */}
      <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-5 space-y-3">
        <h2 className="font-semibold text-slate-900">{t("dashboard.whereMoneyGoes")}</h2>
        <BreakdownBar label={SECTION_LABEL.COGS} value={is.cogs} total={expenses} color="bg-rose-400" />
        <BreakdownBar label={SECTION_LABEL.OPERATING_EXPENSE} value={is.operating_expenses} total={expenses} color="bg-amber-400" />
        <BreakdownBar label={SECTION_LABEL.OTHER_EXPENSE} value={is.other_expense} total={expenses} color="bg-slate-400" />
      </div>

      {/* Estado de resultados */}
      <IncomeStatementCard data={is} />

      {/* Exportar el P&L a PDF (respeta el rango de fechas seleccionado) */}
      <button
        onClick={() => exportIncomeStatementPdf(companyName, is, start || undefined, end || undefined)}
        className="w-full flex items-center justify-center gap-2 rounded-2xl border border-slate-200 bg-white text-slate-700 font-medium py-3 hover:bg-slate-50 transition-colors"
      >
        <svg viewBox="0 0 24 24" className="size-5 text-brand-green" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /><path d="M12 18v-6M9 15l3 3 3-3" />
        </svg>
        {t("dashboard.exportPdf")}
      </button>

      {/* CTA subir */}
      <button
        onClick={onUpload}
        className="w-full rounded-2xl border-2 border-dashed border-brand-green/40 text-brand-green font-medium py-4 hover:bg-brand-green/5 transition-colors"
      >
        {t("dashboard.uploadNew")}
      </button>
    </div>
  );
}
