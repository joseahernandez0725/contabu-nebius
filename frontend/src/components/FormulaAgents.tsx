import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AgentError, createRule, getAgentUsage, runFormulaDrafter, runMerchantVerifier, runReviewer,
  type AgentReview, type AgentUsage, type AgentUsageSummary, type DraftResponse, type FormulaProposal,
  type VerifyResponse, type WebEvidence,
} from "../api";
import { SECTION_LABEL } from "../accounting";

// LOS AGENTES DE LA FÓRMULA (FASE 11), sobre Nemotron en Nebius Token Factory.
// ④ Redactor: propone FILAS de la fórmula del libro (palabra clave, ventana de fechas → categoría),
//    cada una con su prueba contra la historia. Nada se aplica hasta que el bookkeeper aprueba
//    (la aprobación es una regla normal: POST /rules).
// ① Revisor: juzga los hallazgos del auditor (problema / legítimo / duda) y deja una observación.
// ② Verificador (Tavily): qué negocio es cada comercio (tipo de una lista cerrada + enlace). Nunca
//    busca personas, guarda una búsqueda por comercio y no cambia categorías: aporta evidencia.
// Cada llamada muestra lo que costó (tokens y dólares): se mide desde el principio.

const VERDICT_STYLE: Record<AgentReview["verdict"], string> = {
  problem: "bg-rose-100 text-rose-800",
  legitimate: "bg-emerald-100 text-emerald-800",
  unsure: "bg-amber-100 text-amber-800",
};

function fmtCost(usd: number | null | undefined): string {
  if (usd == null) return "—";
  return usd < 0.01 ? `$${usd.toFixed(5)}` : `$${usd.toFixed(3)}`;
}

export default function FormulaAgents({
  onOpenTransaction,
  onChanged,
}: {
  onOpenTransaction?: (id: number) => void;
  onChanged?: () => void; // una regla aprobada cambia el libro: que el auditor vuelva a correr
}) {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language || "en").split("-")[0];
  const showTr = lang !== "en";
  const tr = (c: string) => (showTr ? t(`cat.${c}`, { defaultValue: "" }) : "");

  const [busy, setBusy] = useState<"draft" | "review" | "verify" | null>(null);
  const [checks, setChecks] = useState<VerifyResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<DraftResponse | null>(null);
  const [reviews, setReviews] = useState<AgentReview[] | null>(null);
  const [lastUsage, setLastUsage] = useState<AgentUsage | null>(null);
  const [summary, setSummary] = useState<AgentUsageSummary | null>(null);
  const [decided, setDecided] = useState<Record<number, "approved" | "discarded" | "failed">>({});
  const [applied, setApplied] = useState<Record<number, number>>({});

  const errText = (e: unknown) =>
    e instanceof AgentError
      ? t(`agents.error.${e.code}`, { defaultValue: t("agents.error.agent_error") })
      : t("agents.error.agent_error");

  async function loadUsage() {
    try { setSummary(await getAgentUsage()); } catch { /* sin permiso o sin datos: no se muestra */ }
  }
  useEffect(() => { void loadUsage(); }, []);

  async function propose() {
    setBusy("draft"); setError(null); setDecided({}); setApplied({});
    try {
      const r = await runFormulaDrafter(lang);
      setDraft(r); setLastUsage(r.usage);
      if (r.outcome !== "ok" && !r.proposals.length) setError(t(`agents.error.${r.outcome}`, { defaultValue: t("agents.error.agent_error") }));
    } catch (e) { setError(errText(e)); }
    finally { setBusy(null); void loadUsage(); }
  }

  async function review() {
    setBusy("review"); setError(null);
    try {
      const r = await runReviewer(lang);
      setReviews(r.reviews); setLastUsage(r.usage);
      if (r.outcome !== "ok" && r.outcome !== "nothing_to_review") setError(t(`agents.error.${r.outcome}`, { defaultValue: t("agents.error.agent_error") }));
    } catch (e) { setError(errText(e)); }
    finally { setBusy(null); void loadUsage(); }
  }

  async function verify() {
    setBusy("verify"); setError(null);
    try {
      const r = await runMerchantVerifier();
      setChecks(r);
      if (r.outcome !== "ok") setError(t(`agents.error.${r.outcome.split(":")[0]}`, { defaultValue: t("agents.error.agent_error") }));
    } catch (e) { setError(errText(e)); }
    finally { setBusy(null); void loadUsage(); }
  }

  const webLabel = (e: { business_type: string | null; confidence: string | null }) =>
    `${t(`agents.webType.${e.business_type}`, { defaultValue: e.business_type ?? "—" })}` +
    (e.confidence ? ` · ${t(`agents.confidence.${e.confidence}`)}` : "");

  const evidenceChip = (e: WebEvidence, k: number) => (
    <a key={k} href={e.source_url ?? undefined} target="_blank" rel="noopener noreferrer"
       className="px-2 py-0.5 rounded-full bg-sky-50 text-sky-800 text-xs hover:underline">
      Tavily: {webLabel(e)}{e.source_domain ? ` · ${e.source_domain}` : ""}
    </a>
  );

  async function approve(i: number, p: FormulaProposal) {
    setError(null);
    try {
      const r = await createRule({
        match_text: p.keyword, account_type: p.account_type, category: p.category,
        subcategory: p.subcategory ?? undefined, valid_from: p.valid_from, valid_to: p.valid_to,
        priority: p.priority,
      });
      setDecided((d) => ({ ...d, [i]: "approved" }));
      setApplied((a) => ({ ...a, [i]: r.applied }));
      onChanged?.();
    } catch {
      setDecided((d) => ({ ...d, [i]: "failed" }));
    }
  }

  const period = (p: FormulaProposal) =>
    p.valid_from || p.valid_to ? `${p.valid_from ?? "…"} → ${p.valid_to ?? "…"}` : null;

  return (
    <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-4 space-y-3">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          <h2 className="font-semibold text-slate-900">{t("agents.title")}</h2>
          <p className="text-sm text-slate-500">{t("agents.subtitle")}</p>
        </div>
        <div className="flex gap-2 flex-wrap shrink-0">
          <button onClick={propose} disabled={busy !== null}
                  className="px-4 py-2 rounded-xl font-semibold text-white shadow-sm disabled:opacity-60"
                  style={{ background: "linear-gradient(135deg, #15803d, #f4b400)" }}>
            {busy === "draft" ? t("agents.drafting") : t("agents.draft")}
          </button>
          <button onClick={review} disabled={busy !== null}
                  className="px-4 py-2 rounded-xl font-semibold border border-emerald-700 text-emerald-800 disabled:opacity-60">
            {busy === "review" ? t("agents.reviewing") : t("agents.review")}
          </button>
          <button onClick={verify} disabled={busy !== null}
                  className="px-4 py-2 rounded-xl font-semibold border border-sky-700 text-sky-800 disabled:opacity-60">
            {busy === "verify" ? t("agents.verifying") : t("agents.verify")}
          </button>
        </div>
      </div>

      {/* Lo que cuesta: siempre a la vista. */}
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500 bg-slate-50 rounded-lg px-3 py-2">
        {lastUsage && (
          <span>
            {t("agents.lastCall", {
              input: lastUsage.prompt_tokens.toLocaleString(lang), output: lastUsage.completion_tokens.toLocaleString(lang),
              cost: fmtCost(lastUsage.cost_usd), seconds: (lastUsage.latency_ms / 1000).toFixed(1),
            })}
          </span>
        )}
        {summary && (
          <>
            <span>{t("agents.bookCost", { cost: fmtCost(summary.total_cost_usd) })}</span>
            <span>{t("agents.callsLeft", { left: summary.left_today, cap: summary.daily_cap })}</span>
            <span>{t("agents.searchesLeft", { left: summary.searches_left_today, cap: summary.daily_search_cap })}</span>
          </>
        )}
        <span className="text-slate-400">{lastUsage?.model ?? "Nemotron · Nebius Token Factory"}</span>
      </div>

      {error && <p className="text-sm text-rose-700 bg-rose-50 rounded-lg px-3 py-2">{error}</p>}

      {draft && (
        <div className="space-y-2">
          <h3 className="text-sm font-semibold text-slate-800">
            {t("agents.proposalsTitle", { count: draft.proposals.length })}
          </h3>
          {draft.proposals.length === 0 && <p className="text-sm text-slate-500">{t("agents.noProposals")}</p>}
          {draft.proposals.map((p, i) => (
            <div key={i} className="border border-slate-200 rounded-xl p-3 space-y-1.5">
              <div className="flex flex-wrap items-center gap-1.5 text-sm">
                <span className="font-mono font-semibold text-slate-900">{p.keyword}</span>
                {period(p) && (
                  <span className="px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-800 text-xs">{period(p)}</span>
                )}
                <span className="text-slate-400">→</span>
                <span className="text-slate-800">
                  {SECTION_LABEL[p.account_type] ?? p.account_type} · {p.category}
                  {tr(p.category) && <span className="text-slate-400"> ({tr(p.category)})</span>}
                </span>
              </div>
              <p className="text-sm text-slate-600">{p.explanation}</p>
              {p.web_evidence.length > 0 && (
                <div className="flex flex-wrap gap-1.5">{p.web_evidence.map(evidenceChip)}</div>
              )}
              {p.warnings.map((w) => (
                <p key={w} className="text-xs text-amber-800 bg-amber-50 rounded-lg px-2 py-1">⚠️ {t(`agents.warning.${w}`)}</p>
              ))}
              <p className="text-xs text-slate-500">
                {t("agents.test", { count: p.test.changes })}
                {p.test.by_pair.map((b) => ` · ${b.from === "PENDING" ? t("agents.pending") : b.from} → ${b.to}: ${b.rows}`).join("")}
                {p.test.protected_manual > 0 && ` · ${t("agents.protected", { count: p.test.protected_manual })}`}
                {p.test.agrees_with_corrections > 0 && ` · ${t("agents.agrees", { count: p.test.agrees_with_corrections })}`}
                {p.test.realigned > 0 && ` · ${t("agents.realigned", { count: p.test.realigned })}`}
              </p>
              <div className="flex gap-2 justify-end">
                {decided[i] === "approved" ? (
                  <span className="text-xs text-emerald-700 font-medium">{t("agents.approved", { count: applied[i] ?? 0 })}</span>
                ) : decided[i] === "discarded" ? (
                  <span className="text-xs text-slate-400">{t("agents.discarded")}</span>
                ) : (
                  <>
                    {decided[i] === "failed" && <span className="text-xs text-rose-700">{t("agents.approveFailed")}</span>}
                    <button onClick={() => setDecided((d) => ({ ...d, [i]: "discarded" }))}
                            className="px-3 py-1 rounded-lg text-xs text-slate-600 hover:bg-slate-100">
                      {t("agents.discard")}
                    </button>
                    <button onClick={() => approve(i, p)}
                            className="px-3 py-1 rounded-lg text-xs font-semibold text-white bg-emerald-700">
                      {t("agents.approve")}
                    </button>
                  </>
                )}
              </div>
            </div>
          ))}
          {draft.rejected.length > 0 && (
            <details className="text-xs text-slate-500">
              <summary className="cursor-pointer">{t("agents.rejected", { count: draft.rejected.length })}</summary>
              <ul className="mt-1 space-y-0.5 pl-4 list-disc">
                {draft.rejected.map((r, i) => (
                  <li key={i}><span className="font-mono">{r.keyword ?? "—"}</span>: {r.errors.join(", ")}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}

      {checks && (
        <div className="space-y-2">
          <h3 className="text-sm font-semibold text-slate-800">
            {t("agents.verifyTitle", { count: checks.items.length })}
          </h3>
          <p className="text-xs text-slate-500">
            {t("agents.verifySummary", { searched: checks.searched, cached: checks.from_cache, credits: checks.credits })}
            {Object.entries(checks.skipped).map(([why, n]) => ` · ${t(`agents.skip.${why}`, { count: n })}`).join("")}
          </p>
          <ul className="divide-y divide-slate-100">
            {checks.items.map((c, i) => (
              <li key={i} className="py-1.5 flex flex-wrap items-center gap-2 text-sm">
                <span className="font-mono font-semibold text-slate-900">{c.merchant}</span>
                {c.business_type ? (
                  <span className={`px-2 py-0.5 rounded-full text-xs ${c.confidence === "low" ? "bg-slate-100 text-slate-500" : "bg-sky-50 text-sky-800"}`}>
                    {webLabel(c)}
                  </span>
                ) : (
                  <span className="text-xs text-slate-400">{t("agents.webUnknown")}</span>
                )}
                {c.source_url && (
                  <a href={c.source_url} target="_blank" rel="noopener noreferrer" className="text-xs text-sky-700 hover:underline">
                    {c.source_domain}
                  </a>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {reviews && (
        <div className="space-y-2">
          <h3 className="text-sm font-semibold text-slate-800">{t("agents.reviewsTitle", { count: reviews.length })}</h3>
          {reviews.length === 0 && <p className="text-sm text-slate-500">{t("agents.nothingToReview")}</p>}
          {reviews.map((r) => (
            <div key={r.ref} className="border border-slate-200 rounded-xl p-3 flex items-start gap-3">
              <span className={`text-xs px-2 py-0.5 rounded-full font-medium shrink-0 ${VERDICT_STYLE[r.verdict]}`}>
                {t(`agents.verdict.${r.verdict}`)}
              </span>
              <div className="min-w-0 flex-1 space-y-0.5">
                <p className="text-sm">
                  <span className="font-mono font-semibold text-slate-900">{r.merchant}</span>
                  <span className="text-slate-400"> · {t(`agents.type.${r.type}`, { defaultValue: r.type })}</span>
                </p>
                <p className="text-sm text-slate-600">{r.observation || t("agents.noObservation")}</p>
              </div>
              {r.transaction_ids.length > 0 && onOpenTransaction && (
                <button onClick={() => onOpenTransaction(r.transaction_ids[0])}
                        className="px-2 py-1 rounded-lg text-xs text-slate-600 hover:bg-slate-100 shrink-0">
                  {t("agents.open")}
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
