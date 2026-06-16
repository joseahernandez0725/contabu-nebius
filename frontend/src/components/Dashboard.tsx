import { useEffect, useState } from "react";
import { getIncomeStatement, getTransactions, money } from "../api";
import type { IncomeStatement as IS, Transaction } from "../types";
import IncomeStatementCard from "./IncomeStatement";

// Tarjeta KPI pequeña.
function Kpi({ label, value, tone }: { label: string; value: number; tone: "green" | "rose" }) {
  const color = tone === "green" ? "text-brand-green" : "text-rose-600";
  return (
    <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-4">
      <div className="text-xs text-slate-500">{label}</div>
      <div className={`text-xl font-bold tabular-nums mt-1 ${color}`}>{money(value)}</div>
    </div>
  );
}

// Una barra del desglose de gastos.
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

export default function Dashboard({ onUpload, onSeeAll }: {
  onUpload: () => void; onSeeAll: () => void;
}) {
  const [is, setIs] = useState<IS | null>(null);
  const [txs, setTxs] = useState<Transaction[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([getIncomeStatement(), getTransactions()])
      .then(([isData, txData]) => { setIs(isData); setTxs(txData); })
      .catch((e) => setError(e instanceof Error ? e.message : "Error"));
  }, []);

  if (error) return <div className="alert alert-error">{error}</div>;
  if (!is || !txs)
    return (
      <div className="flex justify-center py-16">
        <span className="loading loading-spinner loading-lg text-brand-green" />
      </div>
    );

  const expenses = is.cogs + is.operating_expenses + is.other_expense;
  const positive = is.net_income >= 0;

  return (
    <div className="space-y-4">
      {/* Héroe: utilidad neta */}
      <div
        className="text-white rounded-3xl shadow-md p-6"
        style={{ background: "linear-gradient(135deg, #15803d, #14532d)" }}
      >
        <div className="text-sm opacity-80">Utilidad neta del periodo</div>
        <div className="text-4xl font-bold tabular-nums mt-2">{money(is.net_income)}</div>
        <div className="text-sm mt-1 opacity-80">
          {positive ? "Tu negocio está en ganancia 🎉" : "Tu negocio está en pérdida este periodo"}
        </div>
      </div>

      {/* KPIs */}
      <div className="grid grid-cols-2 gap-3">
        <Kpi label="Ingresos" value={is.revenue} tone="green" />
        <Kpi label="Gastos totales" value={expenses} tone="rose" />
      </div>

      {/* Desglose de gastos */}
      <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-5 space-y-3">
        <h2 className="font-semibold text-slate-900">¿En qué se va el dinero?</h2>
        <BreakdownBar label="Costo de ventas (COGS)" value={is.cogs} total={expenses} color="bg-rose-400" />
        <BreakdownBar label="Gastos operativos" value={is.operating_expenses} total={expenses} color="bg-amber-400" />
        <BreakdownBar label="Otros gastos" value={is.other_expense} total={expenses} color="bg-slate-400" />
      </div>

      {/* Estado de resultados */}
      <IncomeStatementCard data={is} />

      {/* Movimientos recientes */}
      <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-5">
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-semibold text-slate-900">Movimientos recientes</h2>
          <button onClick={onSeeAll} className="text-sm text-brand-green font-medium hover:underline">
            Ver todos
          </button>
        </div>
        <ul className="divide-y divide-slate-100">
          {txs.slice(0, 5).map((t) => (
            <li key={t.id} className="flex items-center justify-between py-2.5">
              <div className="min-w-0">
                <div className="text-sm font-medium text-slate-800 truncate">{t.subcategory ?? t.description}</div>
                <div className="text-xs text-slate-400">{t.date} · {t.category ?? "—"}</div>
              </div>
              <div className="text-sm font-semibold tabular-nums text-slate-700 shrink-0 ml-3">
                {money(Number(t.amount))}
              </div>
            </li>
          ))}
        </ul>
      </div>

      {/* CTA subir */}
      <button
        onClick={onUpload}
        className="w-full rounded-2xl border-2 border-dashed border-brand-green/40 text-brand-green font-medium py-4 hover:bg-brand-green/5 transition-colors"
      >
        + Subir un nuevo extracto bancario
      </button>
    </div>
  );
}
