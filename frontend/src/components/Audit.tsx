import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  categorizePending, getAiQuota, getAudit, getCategories, updateTransaction,
  type AiQuota, type AuditFinding, type ChartOfAccounts,
} from "../api";
import { SECTION_LABEL } from "../accounting";

const SECTIONS = ["REVENUE", "COGS", "OPERATING_EXPENSE", "OTHER_INCOME", "OTHER_EXPENSE", "NON_PL"];

const TYPE_INFO: Record<string, { label: string; hint: string }> = {
  rule_deviation: {
    label: "Out of sync with your rule",
    hint: "A rule of yours covered this transaction, but its category no longer matches the rule (the rule probably changed).",
  },
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

// Agente auditor: escanea la categorización y lista los sospechosos, cada uno **accionable en un
// toque**. Corre SOLO al abrir la pantalla (FASE 9: el agente vigila sin que se lo pidan) y con el
// botón para re-correr. Inconsistencias/desvíos: fix de 1 toque. Signo/sin-categorizar: el usuario
// elige la categoría. Cada arreglo alimenta el reglamento (cierra el loop con el proponedor).
export default function Audit({
  onOpenTransaction,
  onCount,
  onUpgrade,
}: {
  onOpenTransaction?: (id: number) => void;
  onCount?: (n: number) => void; // reporta los hallazgos al badge de la navegación
  onUpgrade?: () => void; // sin cupo de IA → llevar a Billing (Settings) a suscribirse
}) {
  const { t } = useTranslation();
  const [findings, setFindings] = useState<AuditFinding[] | null>(null);
  const [chart, setChart] = useState<ChartOfAccounts>({});
  const [loading, setLoading] = useState(false);
  const [fixing, setFixing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [picks, setPicks] = useState<Record<number, string>>({}); // categoría elegida por hallazgo
  const [catPendingMsg, setCatPendingMsg] = useState<string | null>(null); // resultado del re-intento
  const [quota, setQuota] = useState<AiQuota | null>(null); // cupo vigente → decide qué botón mostrar
  const [stillBlocked, setStillBlocked] = useState(false); // tras el intento quedaron sin cupo

  useEffect(() => { getCategories().then(setChart).catch(() => {}); }, []);
  useEffect(() => { getAiQuota().then(setQuota).catch(() => {}); }, []);
  // El agente corre solo al abrir (determinista, gratis) — no hay que acordarse de pedírselo.
  useEffect(() => { void run(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const catToType = useMemo(() => {
    const m: Record<string, string> = {};
    for (const [at, cats] of Object.entries(chart)) for (const c of cats) m[c] = at;
    return m;
  }, [chart]);

  async function run() {
    setLoading(true); setError(null);
    try {
      const f = await getAudit();
      setFindings(f);
      onCount?.(f.length); // actualiza el badge ámbar de la navegación
      setPicks({});
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error");
    } finally {
      setLoading(false);
    }
  }

  // Transacciones sin categorizar (quedaron fuera por el CUPO de IA en la subida). Un toque las
  // re-corre por el motor: reglas nuevas → banda → IA hasta donde alcance el cupo VIGENTE (tras
  // subir de plan o al renovarse el mes). Es el cobro de la promesa "sube de plan para categorizarlas".
  const uncatCount = useMemo(
    () => (findings ?? []).filter((f) => f.type === "uncategorized")
      .reduce((n, f) => n + f.transaction_ids.length, 0),
    [findings],
  );

  async function runCategorizePending() {
    setFixing(true); setError(null); setCatPendingMsg(null); setStillBlocked(false);
    try {
      const r = await categorizePending();
      const done = r.by_rule + r.by_band + r.by_transfer + r.by_ai;
      const left = r.still_uncategorized;
      setStillBlocked(left > 0);
      setCatPendingMsg(left > 0
        ? t("audit.pendingBlocked", {
            defaultValue: "{{done}} categorized; {{left}} still need AI quota.",
            done, left,
          })
        : t("audit.pendingDone", { defaultValue: "All {{done}} categorized. ✅", done }));
      getAiQuota().then(setQuota).catch(() => {});
      await run();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error");
    } finally {
      setFixing(false);
    }
  }

  // Aplica la categoría a las transacciones → crea regla + re-aplica (updateTransaction) → refresca.
  // `onlyThis=true` (desvío de regla): re-alinear a la regla NO debe crear otra regla redundante.
  async function applyFix(ids: number[], accountType?: string, category?: string, onlyThis = false) {
    if (!ids.length || !accountType || !category) return;
    setFixing(true); setError(null);
    try {
      for (const id of ids)
        await updateTransaction(id, {
          account_type: accountType, category,
          ...(onlyThis ? { rule_action: "only_this" as const } : {}),
        });
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

      {catPendingMsg && (
        <div className={`flex items-center justify-between gap-3 flex-wrap text-sm rounded-lg px-3 py-2 ${
          stillBlocked ? "text-amber-800 bg-amber-50" : "text-brand-green bg-brand-green/10"
        }`}>
          <span>{catPendingMsg}</span>
          {stillBlocked && onUpgrade && (
            <button
              onClick={onUpgrade}
              className="px-3 py-1.5 rounded-lg font-semibold text-white shadow-sm shrink-0"
              style={{ background: "linear-gradient(135deg, #15803d, #f4b400)" }}
            >
              {t("audit.upgradeCta", { defaultValue: "Upgrade my plan" })}
            </button>
          )}
        </div>
      )}

      {uncatCount > 0 && (
        <div className="flex items-center justify-between gap-3 flex-wrap text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-xl px-3 py-2.5">
          <span>
            {quota && quota.remaining <= 0
              ? t("audit.pendingNoQuota", {
                  defaultValue: "{{n}} transactions have no category yet and you're out of AI quota — upgrade your plan to categorize them.",
                  n: uncatCount,
                })
              : t("audit.pendingAi", {
                  defaultValue: "{{n}} transactions have no category yet (they hit your AI limit when uploaded).",
                  n: uncatCount,
                })}
          </span>
          <div className="flex items-center gap-3 shrink-0">
            {/* Sin cupo: lo primero es SUSCRIBIRSE (a Billing); reintentar solo aplica reglas (gratis). */}
            {quota && quota.remaining <= 0 && onUpgrade ? (
              <>
                <button
                  onClick={onUpgrade}
                  className="px-3 py-1.5 rounded-lg font-semibold text-white shadow-sm"
                  style={{ background: "linear-gradient(135deg, #15803d, #f4b400)" }}
                >
                  {t("audit.upgradeCta", { defaultValue: "Upgrade my plan" })}
                </button>
                <button
                  onClick={runCategorizePending}
                  disabled={loading || fixing}
                  className="text-amber-800 underline disabled:opacity-60"
                >
                  {fixing
                    ? t("audit.pendingRunning", { defaultValue: "Categorizing…" })
                    : t("audit.rulesOnly", { defaultValue: "Apply my rules only (free)" })}
                </button>
              </>
            ) : (
              <button
                onClick={runCategorizePending}
                disabled={loading || fixing}
                className="px-3 py-1.5 rounded-lg font-semibold text-white shadow-sm disabled:opacity-60"
                style={{ background: "linear-gradient(135deg, #15803d, #f4b400)" }}
              >
                {fixing
                  ? t("audit.pendingRunning", { defaultValue: "Categorizing…" })
                  : t("audit.categorizePending", { defaultValue: "Categorize them now" })}
              </button>
            )}
          </div>
        </div>
      )}

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
                  {f.type === "inconsistent_merchant" || f.type === "rule_deviation" ? (
                    <button
                      onClick={() => applyFix(
                        fixIds, f.suggested_account_type, f.suggested_category,
                        f.type === "rule_deviation", // re-alinear a la regla sin crear otra regla
                      )}
                      disabled={fixing}
                      className="mt-1.5 text-xs font-semibold text-brand-green hover:underline disabled:opacity-50"
                    >
                      {f.type === "rule_deviation"
                        ? t("audit.fixRule", { defaultValue: "Fix → back to the rule ({{s}})", s: f.suggestion })
                        : t("audit.fixApply", { defaultValue: "Fix → set all to {{s}}", s: f.suggestion })}
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
