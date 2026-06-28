import { useState } from "react";
import { useTranslation } from "react-i18next";
import { getAudit, type AuditFinding } from "../api";

// Etiqueta + color por tipo de hallazgo del auditor.
const TYPE_INFO: Record<string, { label: string; hint: string }> = {
  inconsistent_merchant: { label: "Inconsistent merchant", hint: "same merchant, different categories" },
  sign_mismatch: { label: "Sign mismatch", hint: "money direction contradicts the P&L section" },
  uncategorized: { label: "Uncategorized", hint: "no P&L section" },
};
const SEV_STYLE: Record<string, string> = {
  high: "text-rose-700 bg-rose-100",
  medium: "text-amber-700 bg-amber-100",
  low: "text-slate-600 bg-slate-100",
};

// Agente auditor (1 click): escanea la categorización y lista los sospechosos. Determinista, sin IA.
// Aditivo a la cola de revisión: atrapa inconsistencias entre transacciones que `needs_review` no ve.
export default function Audit() {
  const { t } = useTranslation();
  const [findings, setFindings] = useState<AuditFinding[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setLoading(true); setError(null);
    try {
      setFindings(await getAudit());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error");
    } finally {
      setLoading(false);
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
            {t("audit.subtitle", { defaultValue: "Find suspicious or inconsistent categorizations in your books." })}
          </p>
        </div>
        <button
          onClick={run}
          disabled={loading}
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
            const info = TYPE_INFO[f.type] ?? { label: f.type, hint: "" };
            return (
              <li key={i} className="px-1 py-2.5 flex items-start gap-3">
                <span className={`text-[10px] uppercase tracking-wide rounded px-1.5 py-0.5 mt-0.5 shrink-0 ${SEV_STYLE[f.severity] ?? SEV_STYLE.low}`}>
                  {f.severity}
                </span>
                <div className="min-w-0">
                  <div className="text-sm font-medium text-slate-800">
                    {t(`audit.type.${f.type}`, { defaultValue: info.label })}
                    {f.merchant && <span className="text-slate-500 font-normal"> · {f.merchant}</span>}
                  </div>
                  <div className="text-xs text-slate-500 truncate">
                    {Array.isArray(f.detail) ? f.detail.join("  ·  ") : f.detail}
                    {f.suggestion && <span className="text-brand-green"> → {f.suggestion}</span>}
                  </div>
                  <div className="text-[11px] text-slate-400 mt-0.5">
                    {t("audit.affects", { defaultValue: "{{n}} transaction(s)", n: f.transaction_ids.length })}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
