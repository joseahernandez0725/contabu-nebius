import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { getTransactions, money } from "../api";
import { SECTION_LABEL } from "../accounting";
import type { Transaction } from "../types";

// Color del badge por sección del P&L (la etiqueta se traduce con i18n).
const BADGE: Record<string, string> = {
  REVENUE: "bg-brand-green/10 text-brand-green",
  OTHER_INCOME: "bg-brand-green/10 text-brand-green",
  COGS: "bg-rose-100 text-rose-600",
  OPERATING_EXPENSE: "bg-amber-100 text-amber-700",
  OTHER_EXPENSE: "bg-amber-100 text-amber-700",
  NON_PL: "bg-slate-100 text-slate-500",
};

export default function Transactions() {
  const { t } = useTranslation();
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

  if (txs.length === 0)
    return (
      <div className="text-center py-12 text-slate-500">
        <p className="font-medium">{t("tx.empty")}</p>
        <p className="text-sm mt-1">{t("tx.emptyHint")}</p>
      </div>
    );

  return (
    <div className="bg-white rounded-2xl shadow-sm border border-slate-100 overflow-hidden">
      <div className="px-5 py-3 border-b border-slate-100 flex items-center justify-between">
        <h2 className="font-semibold text-slate-900">{t("tx.title")}</h2>
        <span className="text-xs text-slate-400">{t("tx.total", { count: txs.length })}</span>
      </div>
      <ul className="divide-y divide-slate-100">
        {txs.map((tx) => {
          const key = tx.account_type ?? "";
          const badge = BADGE[key] ?? "bg-slate-100 text-slate-500";
          const label = SECTION_LABEL[key] ?? "Unclassified"; // contable: inglés
          return (
            <li key={tx.id} className="px-5 py-3 flex items-center gap-3">
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium text-slate-800 truncate">
                  {tx.subcategory ?? tx.description}
                </div>
                <div className="flex items-center gap-2 mt-1">
                  <span className={`text-[11px] font-medium px-2 py-0.5 rounded-full ${badge}`}>{label}</span>
                  <span className="text-xs text-slate-400 truncate">{tx.category ?? "—"} · {tx.date}</span>
                </div>
              </div>
              <div className="text-sm font-semibold tabular-nums text-slate-700 shrink-0">
                {money(Number(tx.amount))}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
