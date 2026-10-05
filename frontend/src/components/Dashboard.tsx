import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { getIncomeStatement, getTransactions, money } from "../api";
import { KPI, SECTION_LABEL } from "../accounting";
import type { IncomeStatement as IS, Transaction } from "../types";

type View = "transactions" | "audit" | "upload" | "pnl" | "settings";

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

// Iconos de los atajos del hub.
const HUB_ICON: Record<View, ReactNode> = {
  transactions: <path d="M8 7h12M8 12h12M8 17h12M4 7h.01M4 12h.01M4 17h.01" />,
  audit: <path d="M9 11l3 3 6-6M5 4h14a1 1 0 011 1v14a1 1 0 01-1 1H5a1 1 0 01-1-1V5a1 1 0 011-1z" />,
  upload: <path d="M3 10l9-6 9 6M5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 21h18" />, // "Bancos"
  pnl: <path d="M4 19V5M4 19h16M8 16v-5M12 16V8M16 16v-3" />,
  settings: <path d="M12 15a3 3 0 100-6 3 3 0 000 6zM3 12h2M19 12h2M12 3v2M12 19v2" />,
};

export default function Dashboard({ onNavigate }: {
  onNavigate: (v: View) => void;
}) {
  const { t } = useTranslation();
  const [is, setIs] = useState<IS | null>(null);
  const [txs, setTxs] = useState<Transaction[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getTransactions().then(setTxs).catch((e) => setError(e instanceof Error ? e.message : "Error"));
    getIncomeStatement().then(setIs).catch((e) => setError(e instanceof Error ? e.message : "Error"));
  }, []);

  if (error) return <div className="alert alert-error">{error}</div>;
  if (!is || !txs)
    return (
      <div className="flex justify-center py-16">
        <span className="loading loading-spinner loading-lg text-brand-green" />
      </div>
    );

  if (txs.length === 0) {
    return (
      <div className="text-center py-12">
        <div className="size-16 rounded-2xl mx-auto flex items-center justify-center text-white"
          style={{ background: "linear-gradient(135deg, #15803d, #f4b400)" }}>
          <svg viewBox="0 0 24 24" className="size-8" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 16V4M7 9l5-5 5 5" /><path d="M4 17v2a1 1 0 001 1h14a1 1 0 001-1v-2" />
          </svg>
        </div>
        <h2 className="text-xl font-bold text-slate-900 mt-4">{t("empty.welcome")}</h2>
        <p className="text-slate-500 mt-2 max-w-xs mx-auto">{t("empty.subtitle")}</p>
        {/* Las dos entradas de un libro nuevo; las dos viven en Bancos (banco vinculado arriba, CSV abajo). */}
        <div className="mt-6 flex flex-wrap justify-center gap-3">
          <button onClick={() => onNavigate("upload")} className="btn bg-brand-green text-white border-none hover:bg-brand-green-dark">
            {t("empty.cta")}
          </button>
          <button onClick={() => onNavigate("upload")} className="btn bg-white text-brand-green border-brand-green hover:bg-brand-green/5">
            {t("empty.linkBank")}
          </button>
        </div>
      </div>
    );
  }

  const expenses = is.cogs + is.operating_expenses + is.other_expense;
  const positive = is.net_income >= 0;
  // FASE 12: lo Pendiente (sin `account_type`) no entra en ninguna de estas sumas — se declara.
  const u = is.uncategorized;
  const pending = u?.count ?? 0;
  // Un libro recién subido: hay movimientos, pero ninguno tiene sección todavía.
  const nothingClassified = pending > 0 && is.revenue === 0 && expenses === 0 && is.other_income === 0;
  const hub: View[] = ["pnl", "transactions", "audit", "upload", "settings"];
  const hubLabel = (v: View) => (v === "pnl" ? t("nav.pnl", { defaultValue: "P&L" })
    : v === "settings" ? t("settings.title") : t(`nav.${v}`));

  return (
    <div className="space-y-4">
      {/* Héroe (resumen, todo el periodo) */}
      <div className="text-white rounded-3xl shadow-md p-6" style={{ background: "linear-gradient(135deg, #15803d, #14532d)" }}>
        <div className="text-sm opacity-80">
          {KPI.netIncome}
          {pending > 0 && <span className="ms-1 opacity-80">· {t("pnl.beforePending")}</span>}
        </div>
        <div className="text-4xl font-bold tabular-nums mt-2">{money(is.net_income)}</div>
        <div className="text-sm mt-1 opacity-80">
          {/* Sin NADA clasificado, el resultado es 0 por falta de datos, no por equilibrio: decir
              "tu negocio está en ganancia" ahí sería celebrar un número vacío. */}
          {nothingClassified
            ? t("pnl.nothingClassified")
            : positive ? t("dashboard.profitPositive") : t("dashboard.profitNegative")}
        </div>
        {/* Lo Pendiente se declara también aquí: este número grande es el que más se mira, y sin
            esta línea un libro 100% Pendiente muestra $0.00 como si estuviera cuadrado. */}
        {pending > 0 && (
          <div className="mt-3 flex items-start gap-2 rounded-xl bg-black/20 px-3 py-2 text-xs">
            <span className="size-2 rounded-full bg-amber-300 mt-1 shrink-0" aria-hidden />
            <span>{t("pnl.pendingBadge", { count: pending })}</span>
          </div>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <Kpi label={KPI.revenue} value={is.revenue} tone="green" />
        <Kpi label={KPI.totalExpenses} value={expenses} tone="rose" />
      </div>

      <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-5 space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold text-slate-900">{t("dashboard.whereMoneyGoes")}</h2>
          <button onClick={() => onNavigate("pnl")} className="text-sm text-brand-green font-medium hover:underline">
            {t("dashboard.seePnl", { defaultValue: "See full P&L →" })}
          </button>
        </div>
        <BreakdownBar label={SECTION_LABEL.COGS} value={is.cogs} total={expenses} color="bg-rose-400" />
        <BreakdownBar label={SECTION_LABEL.OPERATING_EXPENSE} value={is.operating_expenses} total={expenses} color="bg-amber-400" />
        <BreakdownBar label={SECTION_LABEL.OTHER_EXPENSE} value={is.other_expense} total={expenses} color="bg-slate-400" />
        {/* Los porcentajes de arriba son sobre lo YA clasificado. Lo que salió sin clasificar va
            aparte: meterlo en una barra exigiría decidir a qué sección pertenece, y no se sabe. */}
        {u && u.count_out > 0 && (
          <div className="flex justify-between text-xs text-amber-700 border-t border-amber-100 pt-2">
            <span>{t("pnl.pendingTitle")} · {t("pnl.pendingOut")} · {u.count_out}</span>
            <span className="tabular-nums">{money(u.money_out)}</span>
          </div>
        )}
      </div>

      {/* Hub de atajos: ir a cada ventana de trabajo */}
      <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-4">
        <h2 className="font-semibold text-slate-900 mb-3">{t("dashboard.workOn", { defaultValue: "Work on" })}</h2>
        <div className="grid grid-cols-3 gap-2">
          {hub.map((v) => (
            <button key={v} onClick={() => onNavigate(v)}
              className="flex flex-col items-center gap-1.5 py-3 rounded-xl border border-slate-100 hover:bg-slate-50 transition-colors">
              <svg viewBox="0 0 24 24" className="size-6 text-brand-green" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
                {HUB_ICON[v]}
              </svg>
              <span className="text-xs text-slate-600 text-center">{hubLabel(v)}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
