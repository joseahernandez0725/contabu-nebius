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

const fmtRange = (s: string | null, e: string | null) =>
  s && e ? `${s} → ${e}` : e ? `→ ${e}` : s ? `${s} →` : "All time";

// Sección P&L: estado de resultados en vivo (con filtro de fechas) + proyectos guardados.
// Un proyecto es un borrador que recalcula; al "cerrarlo" se congela (snapshot).
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

  // --- Vista DETALLE de un proyecto guardado ---
  if (selected) {
    const closed = selected.status === "closed";
    return (
      <div className="space-y-4">
        <button onClick={() => setSelected(null)} className="text-sm text-brand-green font-medium">
          ← {t("pnl.back", { defaultValue: "Back" })}
        </button>
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-bold text-slate-900">{selected.name}</h2>
            <StatusBadge status={selected.status} />
          </div>
          <p className="text-sm text-slate-500">{fmtRange(selected.start, selected.end)}</p>
        </div>

        <IncomeStatementCard data={selected.pnl} />

        <div className="grid grid-cols-2 gap-2">
          <button
            onClick={() => exportIncomeStatementPdf(companyName, selected.pnl, selected.start || undefined, selected.end || undefined)}
            className="py-2.5 rounded-xl border border-slate-200 bg-white font-medium text-slate-700 hover:bg-slate-50"
          >
            {t("pnl.pdf", { defaultValue: "Export PDF" })}
          </button>
          {closed ? (
            <button
              onClick={async () => { await reopenPnlReport(selected.id); setSelected(await getPnlReport(selected.id)); loadReports(); }}
              className="py-2.5 rounded-xl border border-slate-200 bg-white font-medium text-slate-700 hover:bg-slate-50"
            >
              {t("pnl.reopen", { defaultValue: "Reopen (draft)" })}
            </button>
          ) : (
            <button
              onClick={async () => { await closePnlReport(selected.id); setSelected(await getPnlReport(selected.id)); loadReports(); }}
              className="py-2.5 rounded-xl font-semibold text-white"
              style={{ background: "linear-gradient(135deg, #15803d, #f4b400)" }}
            >
              {t("pnl.close", { defaultValue: "Close period (freeze)" })}
            </button>
          )}
        </div>
        <button
          onClick={async () => {
            if (!confirm(t("pnl.confirmDelete", { defaultValue: "Delete this project?" }))) return;
            await deletePnlReport(selected.id); setSelected(null); loadReports();
          }}
          className="w-full py-2 text-sm text-rose-600 hover:underline"
        >
          {t("pnl.delete", { defaultValue: "Delete project" })}
        </button>
      </div>
    );
  }

  // --- Vista PRINCIPAL: P&L en vivo + proyectos ---
  if (error) return <div className="alert alert-error">{error}</div>;
  return (
    <div className="space-y-4">
      <h2 className="text-lg font-bold text-slate-900">{t("pnl.title", { defaultValue: "Profit & Loss" })}</h2>

      {/* Filtro por rango de fechas */}
      <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-3 flex items-center gap-2 text-sm flex-wrap">
        <span className="text-slate-500 shrink-0">{t("dashboard.period", { defaultValue: "Period" })}</span>
        <input type="date" value={start} max={end || undefined} onChange={(e) => setStart(e.target.value)}
          className="rounded-lg border border-slate-200 px-2 py-1 tabular-nums focus:outline-none focus:ring-2 focus:ring-brand-green/40" />
        <span className="text-slate-400">–</span>
        <input type="date" value={end} min={start || undefined} onChange={(e) => setEnd(e.target.value)}
          className="rounded-lg border border-slate-200 px-2 py-1 tabular-nums focus:outline-none focus:ring-2 focus:ring-brand-green/40" />
        {(start || end) && (
          <button onClick={() => { setStart(""); setEnd(""); }} className="ms-auto text-brand-green font-medium hover:underline">
            {t("dashboard.allTime", { defaultValue: "All time" })}
          </button>
        )}
      </div>

      {is && <IncomeStatementCard data={is} />}

      {/* Guardar como proyecto + PDF */}
      <div className="grid grid-cols-2 gap-2">
        <button
          onClick={() => is && exportIncomeStatementPdf(companyName, is, start || undefined, end || undefined)}
          className="py-2.5 rounded-xl border border-slate-200 bg-white font-medium text-slate-700 hover:bg-slate-50"
        >
          {t("pnl.pdf", { defaultValue: "Export PDF" })}
        </button>
        <button
          onClick={() => setNaming(true)}
          className="py-2.5 rounded-xl font-semibold text-white"
          style={{ background: "linear-gradient(135deg, #15803d, #f4b400)" }}
        >
          {t("pnl.saveProject", { defaultValue: "Save as project" })}
        </button>
      </div>

      {naming && (
        <div className="flex items-center gap-2">
          <input autoFocus value={name} onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && saveProject()}
            placeholder={t("pnl.projectName", { defaultValue: "Project name (e.g. Q1 2026)" })}
            className="flex-1 rounded-lg border border-slate-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-green/40" />
          <button onClick={saveProject} disabled={!name.trim()}
            className="px-3 py-2 rounded-lg text-sm font-medium text-white disabled:opacity-50"
            style={{ background: "linear-gradient(135deg, #15803d, #f4b400)" }}>
            {t("pnl.save", { defaultValue: "Save" })}
          </button>
          <button onClick={() => { setNaming(false); setName(""); }} className="px-2 py-2 text-sm text-slate-500">
            {t("pnl.cancel", { defaultValue: "Cancel" })}
          </button>
        </div>
      )}

      {/* Lista de proyectos guardados */}
      {reports && reports.length > 0 && (
        <div className="bg-white rounded-2xl shadow-sm border border-slate-100 overflow-hidden">
          <div className="px-4 py-2.5 border-b border-slate-100 font-semibold text-slate-900 text-sm">
            {t("pnl.saved", { defaultValue: "Saved projects" })}
          </div>
          <div className="divide-y divide-slate-100">
            {reports.map((r) => (
              <button key={r.id} onClick={async () => setSelected(await getPnlReport(r.id))}
                className="w-full flex items-center gap-3 px-4 py-3 hover:bg-slate-50 text-start">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-slate-800 truncate">{r.name}</span>
                    <StatusBadge status={r.status} />
                  </div>
                  <div className="text-xs text-slate-500">{fmtRange(r.start, r.end)}</div>
                </div>
                {r.net_income != null && (
                  <span className={`tabular-nums font-semibold ${r.net_income >= 0 ? "text-brand-green" : "text-rose-600"}`}>
                    {money(r.net_income)}
                  </span>
                )}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function StatusBadge({ status }: { status: "draft" | "closed" }) {
  const { t } = useTranslation();
  const closed = status === "closed";
  return (
    <span className={`text-[10px] uppercase tracking-wide rounded px-1.5 py-0.5 ${
      closed ? "bg-slate-200 text-slate-600" : "bg-brand-green/10 text-brand-green"
    }`}>
      {closed ? `🔒 ${t("pnl.closed", { defaultValue: "Closed" })}` : `🔄 ${t("pnl.draft", { defaultValue: "Draft" })}`}
    </span>
  );
}
