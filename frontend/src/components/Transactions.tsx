import { useEffect, useState } from "react";
import { getTransactions, money } from "../api";
import type { Transaction } from "../types";

// Etiqueta legible + color por sección del P&L.
const SECTION: Record<string, { label: string; cls: string }> = {
  REVENUE: { label: "Ingreso", cls: "bg-brand-green/10 text-brand-green" },
  OTHER_INCOME: { label: "Otro ingreso", cls: "bg-brand-green/10 text-brand-green" },
  COGS: { label: "Costo de ventas", cls: "bg-rose-100 text-rose-600" },
  OPERATING_EXPENSE: { label: "Gasto operativo", cls: "bg-amber-100 text-amber-700" },
  OTHER_EXPENSE: { label: "Otro gasto", cls: "bg-amber-100 text-amber-700" },
  NON_PL: { label: "Fuera del P&L", cls: "bg-slate-100 text-slate-500" },
};

export default function Transactions() {
  const [txs, setTxs] = useState<Transaction[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getTransactions()
      .then(setTxs)
      .catch((e) => setError(e instanceof Error ? e.message : "Error"));
  }, []);

  if (error) return <div className="alert alert-error">{error}</div>;
  if (!txs)
    return (
      <div className="flex justify-center py-16">
        <span className="loading loading-spinner loading-lg text-brand-green" />
      </div>
    );

  return (
    <div className="bg-white rounded-2xl shadow-sm border border-slate-100 overflow-hidden">
      <div className="px-5 py-3 border-b border-slate-100 flex items-center justify-between">
        <h2 className="font-semibold text-slate-900">Movimientos</h2>
        <span className="text-xs text-slate-400">{txs.length} en total</span>
      </div>
      <ul className="divide-y divide-slate-100">
        {txs.map((t) => {
          const s = SECTION[t.account_type ?? ""] ?? { label: "Sin clasificar", cls: "bg-slate-100 text-slate-500" };
          return (
            <li key={t.id} className="px-5 py-3 flex items-center gap-3">
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium text-slate-800 truncate">
                  {t.subcategory ?? t.description}
                </div>
                <div className="flex items-center gap-2 mt-1">
                  <span className={`text-[11px] font-medium px-2 py-0.5 rounded-full ${s.cls}`}>{s.label}</span>
                  <span className="text-xs text-slate-400 truncate">{t.category ?? "—"} · {t.date}</span>
                </div>
              </div>
              <div className="text-sm font-semibold tabular-nums text-slate-700 shrink-0">
                {money(Number(t.amount))}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
