import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  getAudit, getCategories, updateTransaction,
  type AuditFinding, type ChartOfAccounts,
} from "../api";
import { SECTION_LABEL } from "../accounting";

const SECTIONS = ["REVENUE", "COGS", "OPERATING_EXPENSE", "OTHER_INCOME", "OTHER_EXPENSE", "NON_PL"];

const TYPE_INFO: Record<string, { label: string; hint: string }> = {
  inconsistent_merchant: {
    label: "Same merchant, different categories",
    hint: "These transactions look like the same merchant but were categorized differently.",
  },
  sign_mismatch: {
    label: "Direction doesn't match the category",
    hint: "Money came in but it's marked as an expense (or vice-versa) — likely a refund or a mistake.",
  },
  uncategorized: { label: "Not categorized yet", hint: "This transaction has no category." },
};
const SEV_STYLE: Record<string, string> = {
  high: "text-rose-700 bg-rose-100",
  medium: "text-amber-700 bg-amber-100",
  low: "text-slate-600 bg-slate-100",
};

// Agente auditor (1 click): escanea la categorización y lista los sospechosos, cada uno **accionable
// en un toque**. Inconsistencias: aplica la categoría dominante a las que difieren. Signo/sin-categorizar:
// el usuario elige la categoría y se aplica. Cada arreglo crea una regla (cierra el loop con el proponedor).
export default function Audit({ onOpenTransaction }: { onOpenTransaction?: (id: number) => void }) {
  const { t } = useTranslation();
  const [findings, setFindings] = useState<AuditFinding[] | null>(null);
  const [chart, setChart] = useState<ChartOfAccounts>({});
  const [loading, setLoading] = useState(false);
  const [fixing, setFixing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [picks, setPicks] = useState<Record<number, string>>({}); // categoría elegida por hallazgo

  useEffect(() => { getCategories().then(setChart).catch(() => {}); }, []);

  const catToType = useMemo(() => {
    const m: Record<string, string> = {};
    for (const [at, cats] of Object.entries(chart)) for (const c of cats) m[c] = at;
    return m;
  }, [chart]);

  async function run() {
    setLoading(true); setError(null);
    try {
      setFindings(await getAudit());
      setPicks({});
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error");
    } finally {
      setLoading(false);
    }
  }

  // Aplica la categoría a las transacciones → crea regla + re-aplica (updateTransaction) → refresca.
  async function applyFix(ids: number[], accountType?: string, category?: string) {
    if (!ids.length || !accountType || !category) return;
    setFixing(true); setError(null);
    try {
      for (const id of ids) await updateTransaction(id, { account_type: accountType, category });
      await run();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error");
    } finally {
      setFixing(false);
    }
  }

  return (
    <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-4 space-y-3">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h2 className="font-semibold text-slate-900">
            {t("audit.title", { defaultValue: "Audit my categorization" })}
          </h2>
          <p className="text-sm text-slate-500">
            {t("audit.subtitle", { defaultValue: "Find and fix suspicious or inconsistent categorizations." })}
          </p>
        </div>
        <button
          onClick={run}
          disabled={loading || fixing}
          className="px-4 py-2 rounded-xl font-semibold text-white shadow-sm disabled:opacity-60 shrink-0"
          style={{ background: "linear-gradient(135deg, #15803d, #f4b400)" }}
        >
          {loading ? t("audit.running", { defaultValue: "Checking…" }) : t("audit.run", { defaultValue: "Run audit" })}
        </button>
      </div>

      {error && <div className="text-sm text-rose-600">{error}</div>}

      {findings && findings.length === 0 && (
        <div className="text-sm text-brand-green bg-brand-green/10 rounded-lg px-3 py-2">
          {t("audit.clean", { defaultValue: "No issues found — your categorization looks consistent. ✅" })}
        </div>
      )}

      {findings && findings.length > 0 && (
        <ul className="divide-y divide-slate-100 -mx-1">
          {findings.map((f, i) => {
            const info = TYPE_INFO[f.type] ?? { label: f.type };
            const fixIds = f.odd_ids && f.odd_ids.length ? f.odd_ids : f.transaction_ids;
            return (
              <li key={i} className="px-1 py-2.5 flex items-start gap-3">
                <span className={`text-[10px] uppercase tracking-wide rounded px-1.5 py-0.5 mt-0.5 shrink-0 ${SEV_STYLE[f.severity] ?? SEV_STYLE.low}`}>
                  {f.severity}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium text-slate-800">
                    {t(`audit.type.${f.type}`, { defaultValue: info.label })}
                    {f.merchant && <span className="text-slate-500 font-normal"> · {f.merchant}</span>}
                  </div>
                  <div className="text-xs text-slate-500">
                    {t(`audit.hint.${f.type}`, { defaultValue: info.hint })}
                  </div>
                  {/* Las transacciones confusas, RESALTADAS (color ámbar) para que se vean cuáles son. */}
                  {f.examples && f.examples.length > 0 && (
                    <ul className="mt-1 space-y-0.5">
                      {f.examples.map((ex, k) => (
                        <li key={k}>
                          <button
                            type="button"
                            onClick={() => onOpenTransaction?.(ex.id)}
                            disabled={!onOpenTransaction}
                            className="w-full text-[11px] bg-amber-50 border border-amber-100 rounded px-1.5 py-0.5 flex items-center gap-1.5 enabled:hover:bg-amber-100 enabled:cursor-pointer text-start"
                            title={onOpenTransaction ? t("audit.goToTx", { defaultValue: "Open this transaction" }) : ex.description}
                          >
                            <span className={`shrink-0 font-medium ${ex.is_inflow ? "text-brand-green" : "text-rose-600"}`}>
                              {ex.is_inflow ? `↑ ${t("audit.in", { defaultValue: "in" })}` : `↓ ${t("audit.out", { defaultValue: "out" })}`}
                            </span>
                            <span className="truncate text-amber-800">{ex.description}</span>
                            {onOpenTransaction && <span className="ms-auto shrink-0 text-amber-400">›</span>}
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                  {Array.isArray(f.detail) && (
                    <div className="text-[11px] text-slate-400 mt-0.5">{f.detail.join("  ·  ")}</div>
                  )}

                  {/* Fix de 1 toque */}
                  {f.type === "inconsistent_merchant" ? (
                    <button
                      onClick={() => applyFix(fixIds, f.suggested_account_type, f.suggested_category)}
                      disabled={fixing}
                      className="mt-1.5 text-xs font-semibold text-brand-green hover:underline disabled:opacity-50"
                    >
                      {t("audit.fixApply", { defaultValue: "Fix → set all to {{s}}", s: f.suggestion })}
                    </button>
                  ) : (
                    <div className="mt-1.5 flex items-center gap-2">
                      <select
                        value={picks[i] ?? ""}
                        onChange={(e) => setPicks((p) => ({ ...p, [i]: e.target.value }))}
                        className="rounded-lg border border-slate-200 px-2 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-brand-green/40"
                      >
                        <option value="">{t("audit.pickCategory", { defaultValue: "Pick category…" })}</option>
                        {SECTIONS.filter((s) => chart[s]?.length).map((s) => (
                          <optgroup key={s} label={SECTION_LABEL[s] ?? s}>
                            {chart[s].map((c) => <option key={c} value={c}>{c}</option>)}
                          </optgroup>
                        ))}
                      </select>
                      <button
                        onClick={() => applyFix(f.transaction_ids, catToType[picks[i]], picks[i])}
                        disabled={fixing || !picks[i]}
                        className="text-xs font-semibold text-brand-green hover:underline disabled:opacity-40"
                      >
                        {t("audit.fix", { defaultValue: "Fix" })}
                      </button>
                    </div>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
