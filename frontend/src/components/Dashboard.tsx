import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { getAuditSummary, getBankConnections, getIncomeStatement, getTransactions, money, type AuditBoard, type BankConnection } from "../api";
import { KPI, SECTION_LABEL } from "../accounting";
import type { IncomeStatement as IS, Transaction } from "../types";
import { BTN_PRIMARY, DOT, EmptyRow, ListCard, ListRow, PageHeader, Stat, TONE, agoText, secsSince } from "./PageKit";

type View = "transactions" | "audit" | "upload" | "pnl" | "settings";

// Un banco que no se actualiza hace más de esto sale en "lo que necesita atención".
const STALE_DAYS = 3;

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

// EL RESUMEN DEL LIBRO (13-oct, Firm: diseño de página compartido — `PageKit.tsx`).
// Arriba la línea de estado (el resultado y lo Pendiente, las cifras que más se miran); debajo, LO QUE
// NECESITA ATENCIÓN — cada renglón lleva a la pantalla donde se resuelve — y a dónde se va el dinero.
// Los números salen de SQL (`/income-statement`) y del auditor (`/audit/summary`); aquí no se calcula nada.
export default function Dashboard({ onNavigate, onOpenPending }: {
  onNavigate: (v: View) => void;
  onOpenPending?: () => void;   // Pendientes → la Hoja filtrada, en la primera Pendiente (14-oct)
}) {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language || "en").split("-")[0];
  const [is, setIs] = useState<IS | null>(null);
  const [txs, setTxs] = useState<Transaction[] | null>(null);
  const [audit, setAudit] = useState<AuditBoard["stats"] | null>(null);
  const [banks, setBanks] = useState<BankConnection[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getTransactions().then(setTxs).catch((e) => setError(e instanceof Error ? e.message : "Error"));
    getIncomeStatement().then(setIs).catch((e) => setError(e instanceof Error ? e.message : "Error"));
    getAuditSummary().then(setAudit).catch(() => setAudit(null));
    getBankConnections().then((r) => setBanks(r.config.enabled ? r.connections : [])).catch(() => setBanks([]));
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
  // FASE 12: lo Pendiente (sin `account_type`) no entra en ninguna de estas sumas — se declara.
  const u = is.uncategorized;
  const pending = u?.count ?? 0;
  // Un libro recién subido: hay movimientos, pero ninguno tiene sección todavía. Sin NADA clasificado el
  // resultado es 0 por falta de datos, no por equilibrio: no se pinta como ganancia.
  const nothingClassified = pending > 0 && is.revenue === 0 && expenses === 0 && is.other_income === 0;

  // LO QUE NECESITA ATENCIÓN, de lo más grave a lo menos. Cada renglón lleva a donde se resuelve.
  const down = banks.filter((b) => b.status === "disconnected");
  const stale = banks.filter((b) => b.status !== "disconnected" &&
    (!b.last_synced_at || secsSince(b.last_synced_at) > STALE_DAYS * 86400));
  const items: { key: string; dot: string; title: string; sub?: string; go: View; count?: number }[] = [];
  if (down.length) items.push({ key: "down", dot: DOT.problem, go: "upload", count: down.length,
    title: t("page.summary.banksDown", { count: down.length }), sub: down.map((b) => b.institution_name ?? t("bank.unnamed")).join(" · ") });
  if (audit && audit.problems > 0) items.push({ key: "problems", dot: DOT.problem, go: "audit", count: audit.problems,
    title: t("page.summary.problems", { count: audit.problems }), sub: t("page.summary.problemsSub") });
  if (pending > 0) items.push({ key: "pending", dot: DOT.pending, go: "transactions", count: pending,
    title: t("page.summary.pending", { count: pending }), sub: u && u.count_out > 0 ? `${t("pnl.pendingOut")} · ${money(u.money_out)}` : undefined });
  if (audit && audit.to_review - audit.problems > 0) items.push({ key: "review", dot: DOT.review, go: "audit",
    count: audit.to_review - audit.problems, title: t("page.summary.toReview", { count: audit.to_review - audit.problems }),
    sub: audit.reviewed_seconds_ago != null ? t("auditView.reviewed", { when: agoText(lang, audit.reviewed_seconds_ago) }) : t("auditView.neverReviewed") });
  if (stale.length) items.push({ key: "stale", dot: DOT.info, go: "upload", count: stale.length,
    title: t("page.summary.stale", { count: stale.length }),
    sub: stale.map((b) => `${b.institution_name ?? t("bank.unnamed")} · ${b.last_synced_at ? agoText(lang, secsSince(b.last_synced_at)) : t("bank.neverSynced")}`).join(" · ") });

  return (
    <div className="space-y-3">
      {/* 1. LA LÍNEA DE ESTADO */}
      <PageHeader
        meta={nothingClassified ? t("pnl.nothingClassified") : t("page.summary.allTime")}
        primary={<button onClick={() => onNavigate("transactions")} className={BTN_PRIMARY}>{t("page.summary.openBook")}</button>}
        more={[
          { label: t("dashboard.seePnl", { defaultValue: "See full P&L →" }), onClick: () => onNavigate("pnl") },
          { label: t("nav.audit"), onClick: () => onNavigate("audit") },
          { label: t("nav.upload"), onClick: () => onNavigate("upload") },
        ]}
        moreLabel={t("page.more")}
      >
        <Stat value={money(is.net_income)}
              label={pending > 0 ? `${KPI.netIncome} · ${t("pnl.beforePending")}` : KPI.netIncome}
              tone={is.net_income < 0 ? TONE.problem : TONE.good} />
        <Stat value={money(is.revenue)} label={KPI.revenue} />
        <Stat value={money(expenses)} label={KPI.totalExpenses} />
        {pending > 0 && <Stat value={pending} label={t("page.pnl.pending")} tone={TONE.pending} />}
      </PageHeader>

      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_420px] items-start">
        {/* 2. LO QUE NECESITA ATENCIÓN */}
        <ListCard header={<div className="px-3 py-2 border-b border-slate-200 text-xs font-semibold uppercase tracking-wide text-slate-400">
          {t("page.summary.attention")}</div>}>
          {items.length === 0 && <EmptyRow text={t("page.summary.allClear")} />}
          {items.map((it) => (
            <ListRow key={it.key} active={false} dot={it.dot} title={it.title} sub={it.sub}
                     onClick={() => (it.key === "pending" && onOpenPending ? onOpenPending() : onNavigate(it.go))}
                     right={<span className="text-xs text-slate-400">→</span>} />
          ))}
        </ListCard>

        {/* 3. A DÓNDE SE VA EL DINERO (sobre lo YA clasificado) */}
        <div className="bg-white rounded-xl border border-slate-200 p-3 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-slate-900">{t("dashboard.whereMoneyGoes")}</h2>
            <button onClick={() => onNavigate("pnl")} className="text-xs text-emerald-800 font-medium hover:underline">
              {t("dashboard.seePnl", { defaultValue: "See full P&L →" })}
            </button>
          </div>
          <BreakdownBar label={SECTION_LABEL.COGS} value={is.cogs} total={expenses} color="bg-rose-400" />
          <BreakdownBar label={SECTION_LABEL.OPERATING_EXPENSE} value={is.operating_expenses} total={expenses} color="bg-amber-400" />
          <BreakdownBar label={SECTION_LABEL.OTHER_EXPENSE} value={is.other_expense} total={expenses} color="bg-slate-400" />
          {/* Lo que salió sin clasificar va aparte: meterlo en una barra exigiría decidir a qué sección
              pertenece, y no se sabe. */}
          {u && u.count_out > 0 && (
            <div className="flex justify-between text-xs text-amber-700 border-t border-amber-100 pt-2">
              <span>{t("pnl.pendingTitle")} · {t("pnl.pendingOut")} · {u.count_out}</span>
              <span className="tabular-nums">{money(u.money_out)}</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
