import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  getIncomeStatement, listPnlReports, createPnlReport, getPnlReport,
  closePnlReport, reopenPnlReport, deletePnlReport, money,
  type PnlReportMeta, type PnlReportDetail,
} from "../api";
import { exportIncomeStatementPdf } from "../pdf";
import type { IncomeStatement as IS } from "../types";
import IncomeStatementCard from "./IncomeStatement";
import {
  BTN_PRIMARY, BTN_SMALL_PRIMARY, BTN_GHOST, DOT, DetailHeader, ListCard, ListDetail, ListRow, PageHeader, Stat, TONE,
} from "./PageKit";

// `allTime` llega traducido desde el componente: "All time" cableado aquí salía en inglés
// dentro de la app en español (regla: todo lo visible, en EN y ES).
const fmtRange = (s: string | null, e: string | null, allTime: string) =>
  s && e ? `${s} → ${e}` : e ? `→ ${e}` : s ? `${s} →` : allTime;

// Sección P&L: estado de resultados en vivo (con filtro de fechas) + proyectos guardados.
// Un proyecto es un borrador que recalcula; al "cerrarlo" se congela (snapshot).
//
// 13-oct (Firm, diseño de página compartido — `PageKit.tsx`): línea de estado arriba (resultado, ingresos,
// lo Pendiente) con UNA acción principal (Exportar PDF) y el resto en "⋯"; a la izquierda la lista (el P&L
// en vivo y los proyectos guardados), a la derecha el estado de resultados elegido. La lógica no cambió.
export default function Pnl({ companyName }: { companyName: string }) {
  const { t } = useTranslation();
  const [reports, setReports] = useState<PnlReportMeta[] | null>(null);
  const [selected, setSelected] = useState<PnlReportDetail | null>(null);
  const [is, setIs] = useState<IS | null>(null);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const loadReports = () => listPnlReports().then(setReports).catch(() => setReports([]));
  useEffect(() => { loadReports(); }, []);
  useEffect(() => {
    if (selected) return; // en detalle no recalculamos el P&L en vivo
    getIncomeStatement(start || undefined, end || undefined)
      .then(setIs)
      .catch((e) => setError(e instanceof Error ? e.message : "Error"));
  }, [start, end, selected]);

  async function saveProject() {
    if (!name.trim()) return;
    await createPnlReport(name.trim(), start || undefined, end || undefined);
    setName(""); setNaming(false);
    loadReports();
  }

  if (error) return <div className="alert alert-error">{error}</div>;

  // Lo que se está mirando: el P&L en vivo o un proyecto guardado.
  const data: IS | null = selected ? selected.pnl : is;
  const closed = selected?.status === "closed";
  const pending = data?.uncategorized?.count ?? 0;
  const period = selected ? fmtRange(selected.start, selected.end, t("pnl.allTime"))
                          : fmtRange(start || null, end || null, t("pnl.allTime"));

  const exportPdf = () => {
    if (selected) exportIncomeStatementPdf(companyName, selected.pnl, selected.start || undefined, selected.end || undefined);
    else if (is) exportIncomeStatementPdf(companyName, is, start || undefined, end || undefined);
  };
  const more = selected ? [
    closed
      ? { label: t("pnl.reopen", { defaultValue: "Reopen (draft)" }),
          onClick: async () => { await reopenPnlReport(selected.id); setSelected(await getPnlReport(selected.id)); loadReports(); } }
      : { label: t("pnl.close", { defaultValue: "Close period (freeze)" }),
          onClick: async () => { await closePnlReport(selected.id); setSelected(await getPnlReport(selected.id)); loadReports(); } },
    { label: t("pnl.delete", { defaultValue: "Delete project" }), danger: true,
      onClick: async () => {
        if (!confirm(t("pnl.confirmDelete", { defaultValue: "Delete this project?" }))) return;
        await deletePnlReport(selected.id); setSelected(null); loadReports();
      } },
  ] : [
    { label: t("pnl.saveProject", { defaultValue: "Save as project" }), onClick: () => setNaming(true) },
  ];

  return (
    <div className="space-y-3">
      {/* 1. LA LÍNEA DE ESTADO */}
      <PageHeader
        meta={<>{period}{closed && <span className="ms-2">🔒 {t("pnl.closed", { defaultValue: "Closed" })}</span>}</>}
        primary={<button onClick={exportPdf} disabled={!data} className={BTN_PRIMARY}>{t("pnl.pdf", { defaultValue: "Export PDF" })}</button>}
        more={more}
        moreLabel={t("page.more")}
      >
        <Stat value={data ? money(data.net_income) : undefined}
              label={pending > 0 ? `${t("page.pnl.net")} · ${t("pnl.beforePending")}` : t("page.pnl.net")}
              tone={data && data.net_income < 0 ? TONE.problem : TONE.good} />
        <Stat value={data ? money(data.revenue) : undefined} label={t("page.pnl.revenue")} />
        {pending > 0 && <Stat value={pending} label={t("page.pnl.pending")} tone={TONE.pending} />}
      </PageHeader>

      {/* 2. LA LISTA (en vivo + proyectos) Y EL ESTADO DE RESULTADOS ELEGIDO */}
      <ListDetail
        wide="detail"
        selKey={selected?.id ?? "live"}
        list={
          <ListCard header={<div className="px-3 py-2 border-b border-slate-200 text-xs font-semibold uppercase tracking-wide text-slate-400">
            {t("page.pnl.reports")}</div>}>
            <ListRow active={!selected} onClick={() => setSelected(null)} dot={DOT.ok}
                     title={t("page.pnl.live")} sub={fmtRange(start || null, end || null, t("pnl.allTime"))} />
            {(reports ?? []).map((r) => (
              <ListRow key={r.id} active={selected?.id === r.id} onClick={async () => setSelected(await getPnlReport(r.id))}
                       dot={r.status === "closed" ? DOT.off : DOT.ok}
                       title={<>{r.name}{r.status === "closed" && <span className="ms-1 text-xs text-slate-400">🔒</span>}</>}
                       // Esta lista también enseña un net income: sin lo Pendiente sería la única salida
                       // del P&L que lo calla.
                       sub={<>{fmtRange(r.start, r.end, t("pnl.allTime"))}
                         {!!r.pending_count && <span className="text-amber-700"> · {t("pnl.pendingBadge", { count: r.pending_count })}</span>}</>}
                       right={r.net_income != null && (
                         <span className="text-end">
                           <span className={`block text-xs tabular-nums font-semibold ${r.net_income >= 0 ? "text-emerald-700" : "text-rose-700"}`}>{money(r.net_income)}</span>
                           {!!r.pending_count && <span className="block text-[10px] text-amber-700">{t("pnl.beforePending")}</span>}
                         </span>
                       )} />
            ))}
          </ListCard>
        }
        detail={
          <div className="space-y-3">
            {selected ? (
              <DetailHeader dot={closed ? DOT.off : DOT.ok}
                            kicker={closed ? t("pnl.closed", { defaultValue: "Closed" }) : t("pnl.draft", { defaultValue: "Draft" })}
                            title={selected.name} sub={fmtRange(selected.start, selected.end, t("pnl.allTime"))} />
            ) : (
              // Filtro por rango de fechas (solo el P&L en vivo).
              <div className="flex items-center gap-2 text-sm flex-wrap">
                <span className="text-slate-500 shrink-0">{t("dashboard.period", { defaultValue: "Period" })}</span>
                <input type="date" value={start} max={end || undefined} onChange={(e) => setStart(e.target.value)}
                  className="rounded-lg border border-slate-200 px-2 py-1 tabular-nums focus:outline-none focus:ring-2 focus:ring-brand-green/40" />
                <span className="text-slate-400">–</span>
                <input type="date" value={end} min={start || undefined} onChange={(e) => setEnd(e.target.value)}
                  className="rounded-lg border border-slate-200 px-2 py-1 tabular-nums focus:outline-none focus:ring-2 focus:ring-brand-green/40" />
                {(start || end) && (
                  <button onClick={() => { setStart(""); setEnd(""); }} className="ms-auto text-emerald-800 font-medium hover:underline">
                    {t("dashboard.allTime", { defaultValue: "All time" })}
                  </button>
                )}
              </div>
            )}

            {naming && !selected && (
              <div className="flex items-center gap-2">
                <input autoFocus value={name} onChange={(e) => setName(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && saveProject()}
                  placeholder={t("pnl.projectName", { defaultValue: "Project name (e.g. Q1 2026)" })}
                  className="flex-1 rounded-lg border border-slate-200 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-green/40" />
                <button onClick={saveProject} disabled={!name.trim()} className={BTN_SMALL_PRIMARY}>
                  {t("pnl.save", { defaultValue: "Save" })}
                </button>
                <button onClick={() => { setNaming(false); setName(""); }} className={BTN_GHOST}>
                  {t("pnl.cancel", { defaultValue: "Cancel" })}
                </button>
              </div>
            )}

            {data && <IncomeStatementCard data={data} bare />}
          </div>
        }
      />
    </div>
  );
}
