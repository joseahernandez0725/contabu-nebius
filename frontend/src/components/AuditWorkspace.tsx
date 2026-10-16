import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AgentError, applyRuleProposals, dismissProposal, getAgentEstimate, getAgentUsage, getAuditBoard, getCategories, money,
  resolveFinding, runAudit, runFormulaDrafter, runMerchantVerifier, updateTransaction,
  type AgentEstimate, type AgentSpent, type AgentUsageSummary, type AuditBoard, type BoardFinding, type ChartOfAccounts, type DismissReason,
  type FormulaProposal, type PendingGroup, type TxSummary,
} from "../api";
import { SECTION_LABEL } from "../accounting";
import BookNotes from "./BookNotes";
import MerchantSearch from "./MerchantSearch";
import {
  BTN_PRIMARY, Badge, EmptyRow as Empty, ListCard, ListDetail, ListRow as QueueRow, PageHeader, Stat, TONE, Tabs,
  agoText, DetailEmpty,
} from "./PageKit";

// LA PESTAÑA AUDITORÍA (FASE 11, 8-oct): una herramienta de trabajo, no cuatro paneles sueltos.
// - Encabezado de estado: cómo está el libro en 2 segundos.
// - "Revisar el libro": auditor → ② verificador → ① revisor → ④ redactor, en orden, mostrando el paso.
// - Una cola (propuestas · hallazgos · Pendientes agrupados) y un panel de detalle con la evidencia.
// - Cada hallazgo es UNA tarjeta: lo que vio el auditor + el veredicto del ① + la evidencia del ②.
// La IA nunca categoriza: las acciones de esta pantalla las hace el bookkeeper (o aprueba una regla).
// 13-oct (Firm): la ESTRUCTURA (línea de estado, lista + detalle, renglones) salió a `PageKit.tsx` para
// que toda la app se vea igual; los textos de los hallazgos salieron a `useAuditText()` para que el panel
// lateral de la transacción (Bookkeeping) los diga con las mismas palabras. La lógica no cambió.

type Tab = "proposals" | "findings" | "pending";
type Sel = { kind: "finding"; id: number } | { kind: "pending"; merchant: string } | { kind: "proposal"; idx: number } | null;
// 14-oct (Jose, "los agentes, separados"): "audit" ya incluye las notas, "¿personal?" y el ①; "verify" busca
// en internet SOLO los comercios marcados y el ① vuelve a juzgar esos (ida y vuelta); "draft" es el ④.
type Step = "audit" | "verify" | "draft";
const STEPS: Step[] = ["audit", "verify", "draft"];
type StepState = "idle" | "running" | "done" | "error" | "skipped";

const SECTIONS = ["REVENUE", "COGS", "OPERATING_EXPENSE", "OTHER_INCOME", "OTHER_EXPENSE", "NON_PL"];
const SEV_DOT: Record<string, string> = {
  high: "bg-rose-500", medium: "bg-amber-500", low: "bg-slate-400", info: "bg-sky-500",
};
export const VERDICT_STYLE: Record<string, string> = {
  problem: "bg-rose-50 text-rose-700 ring-rose-200",
  legitimate: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  unsure: "bg-amber-50 text-amber-800 ring-amber-200",
};
const REASONS: DismissReason[] = ["wrong_category", "too_broad", "one_off", "not_now", "other"];

export default function AuditWorkspace({
  onOpenSheet,
  onStats,
}: {
  onOpenSheet?: (txId?: number) => void;                      // a Bookkeeping (la Hoja), a ESA fila
  onStats?: (s: AuditBoard["stats"]) => void;                 // contadores de la barra lateral
}) {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language || "en").split("-")[0];
  const { label, fmtDate, ago, webLabel, detectorSentence, estimateText, spentText } = useAuditText();

  const [board, setBoard] = useState<AuditBoard | null>(null);
  const [chart, setChart] = useState<ChartOfAccounts>({});
  const [proposals, setProposals] = useState<FormulaProposal[]>([]);
  const [rejected, setRejected] = useState(0);
  const [tab, setTab] = useState<Tab>("findings");
  const [sel, setSel] = useState<Sel>(null);
  // En pantalla angosta el detalle queda DEBAJO de la cola: al elegir algo, `ListDetail` baja hasta él.
  const [steps, setSteps] = useState<Record<Step, StepState>>({ audit: "idle", verify: "idle", draft: "idle" });
  const [running, setRunning] = useState<Step | null>(null);
  const [stepNote, setStepNote] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [usage, setUsage] = useState<AgentUsageSummary | null>(null);
  // Lo que costaría cada acción (ANTES) y lo que costó la última (DESPUÉS), 14-oct.
  const [est, setEst] = useState<{ audit?: AgentEstimate; review_book?: AgentEstimate }>({});
  const [lastSpent, setLastSpent] = useState<AgentSpent | null>(null);
  // La decisión se guarda como DATO y se traduce al mostrarla (si se cambia de idioma, sigue bien).
  type Decision = { kind: "approved"; count: number } | { kind: "dismissed" } | { kind: "failed" };
  const [decided, setDecided] = useState<Record<number, Decision>>({});
  const decisionText = (d?: Decision) => !d ? undefined
    : d.kind === "approved" ? t("agents.approved", { count: d.count })
    : d.kind === "dismissed" ? t("auditView.dismissed") : t("agents.approveFailed");

  // ── Carga ────────────────────────────────────────────────────────────────────────────────────
  const loadBoard = useCallback(async () => {
    const b = await getAuditBoard(lang);
    setBoard(b);
    onStats?.(b.stats);
    // Los estimados se recalculan con el libro como quedó (no bloquean la pantalla).
    Promise.all([getAgentEstimate("audit", { lang }), getAgentEstimate("review_book", { lang })])
      .then(([audit, review_book]) => setEst({ audit, review_book })).catch(() => {});
    return b;
  }, [onStats, lang]);

  useEffect(() => {
    getCategories().then(setChart).catch(() => {});
    getAgentUsage().then(setUsage).catch(() => {});
  }, []);
  // Se recarga al cambiar de idioma: un veredicto del ① en otro idioma pasa a "pendiente de rehacer".
  useEffect(() => {
    void loadBoard().catch((e) => setError(errText(e)));
  }, [lang]); // eslint-disable-line react-hooks/exhaustive-deps

  const catToType = useMemo(() => {
    const m: Record<string, string> = {};
    for (const [at, cats] of Object.entries(chart)) for (const c of cats) m[c] = at;
    return m;
  }, [chart]);

  function errText(e: unknown) {
    return e instanceof AgentError
      ? t(`agents.error.${e.code}`, { defaultValue: t("agents.error.agent_error") })
      : t("agents.error.agent_error");
  }

  // ── El flujo "Revisar el libro" ──────────────────────────────────────────────────────────────
  /** Suma lo gastado de cada paso: al final se muestra el total de la acción. */
  const addSpent = (s?: AgentSpent) => s && setLastSpent((p) => p ? {
    calls: p.calls + s.calls, cost_usd: p.cost_usd + s.cost_usd, searches: p.searches + s.searches, credits: p.credits + s.credits,
  } : s);

  async function runStep(step: Step, b: AuditBoard | null) {
    setRunning(step);
    setSteps((s) => ({ ...s, [step]: "running" }));
    try {
      if (step === "audit") {
        setStepNote(t("auditView.step.auditing"));
        const r = await runAudit(lang);
        addSpent(r.spent);
        b = await loadBoard();
      } else if (step === "verify") {
        const e = await getAgentEstimate("verify", { lang }).catch(() => null);
        setStepNote(t("auditView.step.searching", { count: e?.searches ?? 0 }));
        const r = await runMerchantVerifier(lang);
        addSpent(r.spent);
        setStepNote(t("auditView.step.searched", { searched: r.searched, cached: r.from_cache, credits: r.credits,
                                                   rejudged: r.rejudge?.rejudged ?? 0 }));
      } else {
        setStepNote(t("auditView.step.drafting"));
        const r = await runFormulaDrafter(lang);
        addSpent(r.spent);
        setProposals(r.proposals);
        setRejected(r.rejected.length);
        setDecided({});
      }
      setSteps((s) => ({ ...s, [step]: "done" }));
    } catch (e) {
      // Un paso que falla (p. ej. sin clave de búsqueda) no detiene el flujo: se marca y se sigue.
      setSteps((s) => ({ ...s, [step]: e instanceof AgentError && /unavailable/.test(e.code) ? "skipped" : "error" }));
      setError(errText(e));
    }
    return b;
  }

  async function reviewBook() {
    setError(null);
    setLastSpent(null);
    setSteps({ audit: "idle", verify: "idle", draft: "idle" });
    let b: AuditBoard | null = board;
    for (const step of STEPS) b = await runStep(step, b);
    setRunning(null);
    setStepNote("");
    await loadBoard().catch(() => {});
    getAgentUsage().then(setUsage).catch(() => {});
  }

  async function runOne(step: Step) {
    setError(null);
    setLastSpent(null);
    await runStep(step, board);
    setRunning(null);
    setStepNote("");
    if (step !== "audit") await loadBoard().catch(() => {});
    if (step === "draft") setTab("proposals");
    getAgentUsage().then(setUsage).catch(() => {});
  }

  // ── Acciones del bookkeeper ──────────────────────────────────────────────────────────────────
  async function categorize(ids: number[], accountType: string | undefined, category: string | undefined, onlyThis = false) {
    if (!ids.length || !accountType || !category) return;
    setBusy(true); setError(null);
    try {
      for (const id of ids)
        await updateTransaction(id, { account_type: accountType, category, ...(onlyThis ? { rule_action: "only_this" as const } : {}) });
      await loadBoard();
      setSel(null);
    } catch (e) { setError(errText(e)); } finally { setBusy(false); }
  }

  async function markOk(f: BoardFinding, note?: string) {
    setBusy(true);
    try { await resolveFinding(f.id, "ok", note); await loadBoard(); setSel(null); }
    catch (e) { setError(errText(e)); } finally { setBusy(false); }
  }

  async function askRule(g: PendingGroup) {
    setBusy(true); setError(null);
    try {
      const r = await runFormulaDrafter(lang, g.transactions.map((x) => x.id));
      setLastSpent(r.spent ?? null);
      if (r.proposals.length) {
        setProposals((prev) => [...r.proposals, ...prev]);
        setDecided({});
        setTab("proposals");
        setSel({ kind: "proposal", idx: 0 });
      } else {
        setError(t("auditView.noRuleForMerchant", { merchant: g.merchant }));
      }
      getAgentUsage().then(setUsage).catch(() => {});
    } catch (e) { setError(errText(e)); } finally { setBusy(false); }
  }

  /** "Proponer las filas para esta nota" (14-oct): el ④ corre SOLO para esa nota. */
  async function proposeForNote(noteId: number): Promise<number> {
    setError(null);
    const r = await runFormulaDrafter(lang, undefined, noteId);
    setLastSpent(r.spent ?? null);
    if (r.proposals.length) {
      setProposals((prev) => [...r.proposals, ...prev]);
      setDecided({});
      setTab("proposals");
      setSel({ kind: "proposal", idx: 0 });
    }
    getAgentUsage().then(setUsage).catch(() => {});
    return r.proposals.length;
  }

  /** Después de "Buscar este comercio": la evidencia y el veredicto nuevos se ven en el tablero. */
  async function afterSearch(s?: AgentSpent) {
    setLastSpent(s ?? null);
    await loadBoard().catch(() => {});
    getAgentUsage().then(setUsage).catch(() => {});
  }

  async function approve(idx: number, p: FormulaProposal) {
    setBusy(true);
    try {
      const r = await applyRuleProposals([{
        keyword: p.keyword, account_type: p.account_type, category: p.category, subcategory: p.subcategory ?? undefined,
        priority: p.priority, valid_from: p.valid_from, valid_to: p.valid_to,
        amount_min: p.amount_min, amount_max: p.amount_max, origin: "agent",
      }]);
      setDecided((d) => ({ ...d, [idx]: { kind: "approved", count: r.applied } }));
      await loadBoard();
    } catch {
      setDecided((d) => ({ ...d, [idx]: { kind: "failed" } }));
      setError(t("agents.approveFailed"));
    }
    finally { setBusy(false); }
  }

  async function dismiss(idx: number, p: FormulaProposal, reason: DismissReason, text: string) {
    setBusy(true);
    try {
      await dismissProposal(p, reason, text);
      setDecided((d) => ({ ...d, [idx]: { kind: "dismissed" } }));
    } catch (e) { setError(errText(e)); } finally { setBusy(false); }
  }

  // ── Lo que se ve ─────────────────────────────────────────────────────────────────────────────
  const findings = board?.findings ?? [];
  const groups = board?.pending_groups ?? [];
  const openProposals = proposals.filter((_, i) => !decided[i] || decided[i].kind === "failed");
  const counts = { proposals: openProposals.length, findings: findings.length, pending: groups.length };

  const selFinding = sel?.kind === "finding" ? findings.find((f) => f.id === sel.id) : undefined;
  const selGroup = sel?.kind === "pending" ? groups.find((g) => g.merchant === sel.merchant) : undefined;
  const selProposal = sel?.kind === "proposal" ? proposals[sel.idx] : undefined;

  const stats = board?.stats;
  const modelName = "Nemotron · Nebius Token Factory";
  const costTip = usage
    ? t("auditView.costTip", {
        cost: `$${(usage.total_cost_usd ?? 0).toFixed(5)}`, calls: usage.left_today, cap: usage.daily_cap,
        searches: usage.searches_left_today, scap: usage.daily_search_cap,
      })
    : "";

  return (
    <div className="space-y-3">
      {/* 1. LA LÍNEA DE ESTADO + el flujo (estructura común: `PageHeader`) */}
      <PageHeader
        meta={stats?.reviewed_seconds_ago != null ? t("auditView.reviewed", { when: ago(stats.reviewed_seconds_ago) }) : t("auditView.neverReviewed")}
        primary={
          <span className="flex items-center gap-2">
            <button onClick={() => void runOne("audit")} disabled={running !== null || busy}
                    title={`${t("auditView.auditTip")} · ${estimateText(est.audit)}`}
                    className="px-3 py-1.5 rounded-lg text-sm font-semibold text-emerald-800 ring-1 ring-emerald-200 hover:bg-emerald-50 disabled:opacity-50">
              {t("auditView.auditBtn")}
            </button>
            <button onClick={reviewBook} disabled={running !== null || busy} className={BTN_PRIMARY}
                    title={estimateText(est.review_book)}>
              {running ? t("auditView.running") : t("auditView.reviewBook")}
            </button>
          </span>
        }
        moreLabel={t("auditView.moreActions")}
        more={STEPS.map((s) => ({
          label: t(`auditView.only.${s}`), onClick: () => void runOne(s), disabled: running !== null,
        }))}
        below={running ? (
          <div className="flex items-center gap-2 text-xs text-slate-600">
            {STEPS.map((s, i) => (
              <span key={s} className="flex items-center gap-1">
                {i > 0 && <span className="text-slate-300">›</span>}
                <span className={steps[s] === "running" ? "font-semibold text-emerald-800" : steps[s] === "done" ? "text-emerald-700" : steps[s] === "error" ? "text-rose-600" : "text-slate-400"}>
                  {steps[s] === "done" ? "✓ " : steps[s] === "running" ? "● " : steps[s] === "error" ? "✗ " : ""}{t(`auditView.stepName.${s}`)}
                </span>
              </span>
            ))}
            <span className="ms-2 text-slate-500">{stepNote}</span>
          </div>
        ) : (
          // ANTES: lo que costaría cada botón. DESPUÉS: lo que costó la última acción.
          <div className="text-[11px] text-slate-500 flex flex-wrap gap-x-3">
            {lastSpent && <span className="text-emerald-800">{spentText(lastSpent)}</span>}
            {est.audit && <span>{t("auditView.est.line", { action: t("auditView.auditBtn"), what: estimateText(est.audit) })}</span>}
            {est.review_book && <span>{t("auditView.est.line", { action: t("auditView.reviewBook"), what: estimateText(est.review_book) })}</span>}
          </div>
        )}
      >
        <Stat value={stats?.to_review} label={t("auditView.stat.toReview")} tone={TONE.neutral} />
        <Stat value={stats?.pending} label={t("auditView.stat.pending", { count: stats?.pending_merchants ?? 0 })} tone={TONE.pending} />
        <Stat value={stats?.problems} label={t("auditView.stat.problems")} tone={TONE.problem} />
      </PageHeader>

      {error && <p className="text-sm text-rose-700 bg-rose-50 rounded-lg px-3 py-2">{error}</p>}

      {/* 2. LAS NOTAS (compactas si están vacías) */}
      <BookNotes compact onProposeForNote={proposeForNote} />

      {/* 3. LA COLA + EL DETALLE (estructura común: `ListDetail`) */}
      <ListDetail
        selKey={sel ? JSON.stringify(sel) : undefined}
        list={
          <ListCard
            tabs={<Tabs active={tab} onChange={setTab}
                        items={(["findings", "pending", "proposals"] as Tab[]).map((k) => ({ key: k, label: t(`auditView.tab.${k}`), count: counts[k] }))} />}
            footer={tab === "proposals" && rejected > 0 ? t("agents.rejected", { count: rejected }) : undefined}>
              {board === null && <li className="px-3 py-3 text-sm text-slate-400">{t("auditView.loading")}</li>}
              {tab === "findings" && board && findings.length === 0 && <Empty text={t("auditView.empty.findings")} />}
              {tab === "pending" && board && groups.length === 0 && <Empty text={t("auditView.empty.pending")} />}
              {tab === "proposals" && proposals.length === 0 && <Empty text={t("auditView.empty.proposals")} />}

              {tab === "findings" && findings.map((f) => (
                <QueueRow key={f.id} active={sel?.kind === "finding" && sel.id === f.id} onClick={() => setSel({ kind: "finding", id: f.id })}
                          dot={SEV_DOT[f.severity]} title={t(`auditView.type.${f.type}`, { defaultValue: f.type })}
                          sub={f.merchant ?? ""} right={
                            <>
                              {f.agent_verdict && <Badge cls={VERDICT_STYLE[f.agent_verdict]}>{t(`agents.verdict.${f.agent_verdict}`)}</Badge>}
                              {f.web && <span title={webLabel(f.web)} className="text-xs">🔎</span>}
                              {f.rows > 0 && <span className="text-xs text-slate-400 tabular-nums">{f.rows}</span>}
                            </>
                          } />
              ))}
              {tab === "pending" && groups.map((g) => (
                <QueueRow key={g.merchant} active={sel?.kind === "pending" && sel.merchant === g.merchant}
                          onClick={() => setSel({ kind: "pending", merchant: g.merchant })}
                          dot="bg-amber-400" title={`${g.merchant} ×${g.count}`}
                          sub={`${fmtDate(g.first)}${g.first !== g.last ? ` – ${fmtDate(g.last)}` : ""}${g.accounts.length ? ` · ${g.accounts.join(", ")}` : ""}`}
                          right={<span className="text-xs tabular-nums text-slate-600">{money(g.total)}</span>} />
              ))}
              {tab === "proposals" && proposals.map((p, i) => (
                <QueueRow key={i} active={sel?.kind === "proposal" && sel.idx === i} onClick={() => setSel({ kind: "proposal", idx: i })}
                          dot={decided[i] ? "bg-slate-300" : "bg-emerald-500"} title={`${p.keyword} → ${p.category}`}
                          sub={decisionText(decided[i]) ?? t("agents.test", { count: p.test.changes })}
                          right={<>{p.warnings.length > 0 && <span className="text-xs">⚠️</span>}{p.web_evidence.length > 0 && <span className="text-xs">🔎</span>}</>} />
              ))}
          </ListCard>
        }
        detail={
          <>
            {!sel && <DetailEmpty text={t("auditView.pickOne")} />}
            {selFinding && (
              <FindingDetail f={selFinding} t={t} sentence={detectorSentence(selFinding)} label={label} webLabel={webLabel}
                             fmtDate={fmtDate} chart={chart} catToType={catToType} busy={busy}
                             onCategorize={categorize} onOk={markOk} onOpenSheet={onOpenSheet} />
            )}
            {selFinding && selFinding.transactions[0] && (
              <MerchantSearch transactionId={selFinding.transactions[0].id} onDone={afterSearch} />
            )}
            {selGroup && (
              <PendingDetail g={selGroup} t={t} webLabel={webLabel} fmtDate={fmtDate} chart={chart} catToType={catToType}
                             busy={busy} onCategorize={categorize} onAskRule={askRule} onOpenSheet={onOpenSheet} />
            )}
            {selGroup && !selGroup.person_payment && selGroup.transactions[0] && (
              <MerchantSearch transactionId={selGroup.transactions[0].id} onDone={afterSearch} />
            )}
            {selProposal && sel?.kind === "proposal" && (
              <ProposalDetail p={selProposal} idx={sel.idx} decided={decided[sel.idx]?.kind === "failed" ? undefined : decisionText(decided[sel.idx])} t={t} label={label} webLabel={webLabel}
                              fmtDate={fmtDate} busy={busy} onApprove={approve} onDismiss={dismiss} />
            )}
          </>
        }
      />

      {/* 4. PIE: quién hace el trabajo (para los jueces) y el costo, discreto */}
      <p className="text-[11px] text-slate-400 flex flex-wrap gap-x-3" title={costTip}>
        <span>{t("auditView.poweredBy", { model: modelName })}</span>
        {usage && <span>{t("auditView.costLine", { cost: `$${(usage.total_cost_usd ?? 0).toFixed(5)}`, left: usage.left_today })}</span>}
      </p>
    </div>
  );
}

// ── Los textos de la Auditoría (13-oct: salieron del componente, sin cambiar la lógica) ──────────
// Los usa también el panel lateral de la transacción (Bookkeeping): un hallazgo se dice igual en los dos.

export function useAuditText() {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language || "en").split("-")[0];
  const showTr = lang !== "en";
  const catTr = (c: string) => (showTr ? t(`cat.${c}`, { defaultValue: "" }) : "");
  /** "OPERATING_EXPENSE/Meals" → "Operating expense · Meals (Comidas)" */
  const label = (s: string | null | undefined) => {
    if (!s || s === "PENDING") return t("agents.pending");
    const [sec, cat] = s.includes("/") ? s.split("/", 2) : ["", s];
    const tr = cat ? catTr(cat) : "";
    return `${sec ? `${SECTION_LABEL[sec] ?? sec} · ` : ""}${cat}${tr ? ` (${tr})` : ""}`;
  };
  const fmtDate = (iso: string) => {
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(y, m - 1, d).toLocaleDateString(lang, { day: "numeric", month: "short", year: "numeric" });
  };
  const fmtMonth = (ym: string) => {
    const [y, m] = ym.split("-").map(Number);
    return new Date(y, m - 1, 1).toLocaleDateString(lang, { month: "long", year: "numeric" });
  };
  /** "hace 5 minutos": los segundos los calcula la base con su propio reloj (sin líos de zona horaria). */
  const ago = (secs: number) => agoText(lang, secs);
  const webLabel = (w: { business_type: string; confidence: string | null }) =>
    `${t(`agents.webType.${w.business_type}`, { defaultValue: w.business_type })}` +
    (w.confidence ? ` · ${t(`agents.confidence.${w.confidence}`)}` : "");

  /** La frase del AUDITOR para cada tipo, traducida, con los datos del hallazgo. */
  function detectorSentence(f: BoardFinding): string {
    const p = f.payload as Record<string, unknown>;
    switch (f.type) {
      case "rule_deviation":
        return t("auditView.say.rule_deviation", { rule: p.rule ?? "—", says: label(p.rule_says as string), has: label(p.row_has as string) });
      case "inconsistent_merchant": {
        const cats = (p.categories as string[] | undefined) ?? [];
        const manual = (p.manual_ids as number[] | undefined)?.length ?? 0;
        return t("auditView.say.inconsistent_merchant", { count: cats.length, cats: cats.map(label).join(" · ") }) +
          (manual ? " " + t("auditView.say.byHand", { count: manual }) : "");
      }
      case "sign_mismatch":
        return t("auditView.say.sign_mismatch", { dir: t(`auditView.dir.${p.direction}`), section: SECTION_LABEL[p.account_type as string] ?? String(p.account_type) });
      case "rule_collision":
        return t("auditView.say.rule_collision", { a: p.winning_rule, athen: label(p.winning_then as string), b: p.losing_rule, bthen: label(p.losing_then as string), count: Number(p.rows ?? 0) });
      case "merchant_type_mismatch":
        return t("auditView.say.merchant_type_mismatch", { type: t(`agents.webType.${p.business_type}`, { defaultValue: String(p.business_type) }), cats: ((p.categories as string[]) ?? []).map(label).join(" · ") });
      case "payroll_net_only":
        return t(p.kind === "tax" ? "auditView.say.payroll_tax" : "auditView.say.payroll_net");
      case "note_check":
        return t(p.meaning === "personal" ? "auditView.say.note_check_personal" : "auditView.say.note_check",
          { note: p.note, count: Number(p.rows ?? 0), cats: ((p.categories as string[]) ?? []).map(label).join(" · ") });
      case "personal_use":
        // La opinión de Nemotron "¿personal PARA ESTE NEGOCIO?" (14-oct): la razón viene en los dos idiomas.
        return t("auditView.say.personal_use", {
          business: t(`sectors.${p.sector}`, { defaultValue: String(p.sector ?? "") }).toLowerCase(),
          confidence: t(`agents.confidence.${p.confidence}`, { defaultValue: String(p.confidence ?? "") }),
          reason: String((lang === "es" ? p.reason_es : p.reason_en) ?? p.reason_en ?? "").replace(/\.?$/, "."),
          cats: ((p.categories as string[]) ?? []).map(label).join(" · ") });
      case "unusual_amount":
        return t("auditView.say.unusual_amount", { amount: money(Number(p.amount)), usual: money(Number(p.usual)), times: p.times });
      case "coverage_gap":
        return t("auditView.say.coverage_gap", { month: fmtMonth(String(p.month)), account: p.account });
      case "bank_disconnected":
        return t("auditView.say.bank_disconnected", { bank: p.institution ?? "—", error: p.error ?? "—" });
      default:
        return f.type;
    }
  }

  /** "~2 llamadas a la IA · hasta 3 búsquedas (3–9 créditos)": lo que costaría, ANTES (14-oct). */
  const estimateText = (e: AgentEstimate | null | undefined) => {
    if (!e) return "";
    const parts = [
      e.calls ? t("auditView.est.calls", { count: e.calls }) : "",
      e.searches ? t("auditView.est.searches", { count: e.searches, min: e.credits_min, max: e.credits_max }) : "",
    ].filter(Boolean);
    return parts.length ? parts.join(" · ") : t("auditView.est.free");
  };
  /** Lo que costó, DESPUÉS. */
  const spentText = (s: AgentSpent | null | undefined) => !s ? "" : t("auditView.spent", {
    calls: s.calls, cost: `$${s.cost_usd.toFixed(5)}`, searches: s.searches, credits: s.credits,
  });
  return { label, fmtDate, fmtMonth, ago, webLabel, detectorSentence, estimateText, spentText };
}

// ── Piezas ─────────────────────────────────────────────────────────────────────────────────────

type T = ReturnType<typeof useTranslation>["t"];

/** Un movimiento: comercio limpio · fecha · monto · cuenta; la línea cruda del banco, al expandir. */
function TxRow({ x, fmtDate, extra, onOpen, openLabel }: {
  x: TxSummary; fmtDate: (s: string) => string; extra?: React.ReactNode;
  onOpen?: (id: number) => void; openLabel?: string;   // ir a ESTA fila en Bookkeeping (14-oct)
}) {
  const [open, setOpen] = useState(false);
  return (
    <li className="py-1">
      <button onClick={() => setOpen(!open)} className="w-full text-start flex items-center gap-2 text-xs">
        <span className="text-slate-400 w-3">{open ? "▾" : "▸"}</span>
        <span className="min-w-0 flex-1 truncate text-slate-800">{x.merchant}</span>
        <span className="text-slate-500 shrink-0">{fmtDate(x.date)}</span>
        <span className={`tabular-nums shrink-0 w-20 text-end ${x.is_inflow ? "text-emerald-700" : "text-slate-800"}`}>
          {x.is_inflow ? "+" : "−"}{money(Math.abs(x.amount))}
        </span>
      </button>
      {extra && <div className="ps-5 text-[11px] text-slate-500">{extra}</div>}
      {open && (
        <div className="ps-5 pt-0.5 text-[11px] text-slate-500 space-y-0.5">
          {x.account && <div>{x.account}</div>}
          <div className="font-mono break-all text-slate-400">{x.description}</div>
          {onOpen && <button onClick={() => onOpen(x.id)} className="text-emerald-800 hover:underline">{openLabel} →</button>}
        </div>
      )}
    </li>
  );
}

function CategoryPicker({ chart, onPick, busy, t, cta }: {
  chart: ChartOfAccounts; onPick: (cat: string) => void; busy: boolean; t: T; cta: string;
}) {
  const [pick, setPick] = useState("");
  return (
    <div className="flex items-center gap-2">
      <select value={pick} onChange={(e) => setPick(e.target.value)}
              className="min-w-0 flex-1 rounded-lg border border-slate-200 px-2 py-1 text-xs">
        <option value="">{t("audit.pickCategory", { defaultValue: "Pick category…" })}</option>
        {SECTIONS.filter((s) => chart[s]?.length).map((s) => (
          <optgroup key={s} label={SECTION_LABEL[s] ?? s}>
            {chart[s].map((c) => <option key={c} value={c}>{c}</option>)}
          </optgroup>
        ))}
      </select>
      <button onClick={() => pick && onPick(pick)} disabled={busy || !pick}
              className="px-2.5 py-1 rounded-lg text-xs font-semibold text-white bg-emerald-700 disabled:opacity-40">{cta}</button>
    </div>
  );
}

function FindingDetail({ f, t, sentence, label, webLabel, fmtDate, chart, catToType, busy, onCategorize, onOk, onOpenSheet }: {
  f: BoardFinding; t: T; sentence: string; label: (s: string) => string;
  webLabel: (w: { business_type: string; confidence: string | null }) => string; fmtDate: (s: string) => string;
  chart: ChartOfAccounts; catToType: Record<string, string>; busy: boolean;
  onCategorize: (ids: number[], at?: string, cat?: string, onlyThis?: boolean) => void;
  onOk: (f: BoardFinding, note?: string) => void; onOpenSheet?: (txId?: number) => void;
}) {
  const [note, setNote] = useState("");
  const p = f.payload as Record<string, unknown>;
  const ids = f.transactions.map((x) => x.id);
  return (
    <div className="space-y-2.5">
      <div>
        <div className="flex items-center gap-1.5 text-xs">
          <span className={`w-2 h-2 rounded-full ${SEV_DOT[f.severity]}`} />
          <span className="text-slate-600">{t(`auditView.severity.${f.severity}`)}</span>
        </div>
        <h3 className="text-sm font-semibold text-slate-900 mt-0.5">
          {t(`auditView.type.${f.type}`, { defaultValue: f.type })}{f.merchant ? <span className="font-normal text-slate-500"> · {f.merchant}</span> : null}
        </h3>
      </div>
      {/* Lo que vio el auditor */}
      <p className="text-sm text-slate-700">{sentence}</p>
      {/* El ① revisor */}
      {!f.is_info && (
        f.agent_verdict ? (
          <div className="flex items-start gap-2">
            <Badge cls={VERDICT_STYLE[f.agent_verdict]}>① {t(`agents.verdict.${f.agent_verdict}`)}</Badge>
            {f.observation_stale
              ? <span className="text-xs text-slate-400 italic">{t("auditView.staleVerdict")}</span>
              : <span className="text-sm text-slate-600">{f.agent_observation}</span>}
          </div>
        ) : <p className="text-xs text-slate-400">{t("auditView.notReviewed")}</p>
      )}
      {/* La evidencia del ② */}
      {f.web && (
        <a href={f.web.source_url ?? undefined} target="_blank" rel="noopener noreferrer"
           className="inline-block text-xs px-2 py-0.5 rounded-full bg-sky-50 text-sky-800 hover:underline">
          🔎 ② Tavily: {webLabel(f.web)}{f.web.source_domain ? ` · ${f.web.source_domain}` : ""}
        </a>
      )}
      {f.transactions.length > 0 && (
        <ul className="border-t border-slate-100 pt-1">
          {f.transactions.map((x) => <TxRow key={x.id} x={x} fmtDate={fmtDate} extra={x.category ? label(`${x.account_type}/${x.category}`) : t("agents.pending")}
                                            onOpen={onOpenSheet} openLabel={t("auditView.act.openSheet")} />)}
        </ul>
      )}
      {/* Acciones */}
      <div className="border-t border-slate-100 pt-2 space-y-2">
        {f.type === "rule_deviation" && (
          <button onClick={() => onCategorize(ids, p.suggested_account_type as string, p.suggested_category as string, true)} disabled={busy}
                  className="px-2.5 py-1 rounded-lg text-xs font-semibold text-white bg-emerald-700 disabled:opacity-40">
            {t("auditView.act.backToRule", { s: label(p.rule_says as string) })}
          </button>
        )}
        {f.type === "inconsistent_merchant" && Boolean(p.suggested_category) && (
          <button onClick={() => onCategorize(((p.odd_ids as number[]) ?? []).filter((i) => !((p.manual_ids as number[]) ?? []).includes(i)),
                                              p.suggested_account_type as string, p.suggested_category as string)} disabled={busy}
                  className="px-2.5 py-1 rounded-lg text-xs font-semibold text-white bg-emerald-700 disabled:opacity-40">
            {t("auditView.act.setAll", { s: label(`${p.suggested_account_type}/${p.suggested_category}`) })}
          </button>
        )}
        {(f.type === "sign_mismatch" || f.type === "merchant_type_mismatch") && (
          <CategoryPicker chart={chart} busy={busy} t={t} cta={t("auditView.act.fix")}
                          onPick={(c) => onCategorize(ids, catToType[c], c)} />
        )}
        <div className="flex flex-wrap items-center gap-2">
          <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={300}
                 placeholder={t("auditView.okNote")} className="min-w-0 flex-1 rounded-lg border border-slate-200 px-2 py-1 text-xs" />
          <button onClick={() => onOk(f, note)} disabled={busy}
                  className="px-2.5 py-1 rounded-lg text-xs font-medium border border-slate-300 text-slate-700 hover:bg-slate-50 disabled:opacity-40">
            {f.is_info ? t("auditView.act.understood") : t("auditView.act.ok")}
          </button>
          {onOpenSheet && f.transactions.length > 0 && (
            <button onClick={() => onOpenSheet(f.transactions[0].id)} className="px-2 py-1 rounded-lg text-xs text-slate-600 hover:bg-slate-100">{t("auditView.act.openSheet")}</button>
          )}
        </div>
      </div>
    </div>
  );
}

function PendingDetail({ g, t, webLabel, fmtDate, chart, catToType, busy, onCategorize, onAskRule, onOpenSheet }: {
  g: PendingGroup; t: T; webLabel: (w: { business_type: string; confidence: string | null }) => string;
  fmtDate: (s: string) => string; chart: ChartOfAccounts; catToType: Record<string, string>; busy: boolean;
  onCategorize: (ids: number[], at?: string, cat?: string) => void; onAskRule: (g: PendingGroup) => void; onOpenSheet?: (txId?: number) => void;
}) {
  const ids = g.transactions.map((x) => x.id);
  return (
    <div className="space-y-2.5">
      <div>
        <div className="flex items-center gap-1.5 text-xs text-amber-700"><span className="w-2 h-2 rounded-full bg-amber-400" />{t("agents.pending")}</div>
        <h3 className="text-sm font-semibold text-slate-900 mt-0.5">{g.merchant} ×{g.count} <span className="font-normal text-slate-500">· {money(g.total)}</span></h3>
        <p className="text-xs text-slate-500">{fmtDate(g.first)}{g.first !== g.last ? ` – ${fmtDate(g.last)}` : ""}{g.accounts.length ? ` · ${g.accounts.join(", ")}` : ""}</p>
      </div>
      {g.person_payment && <p className="text-xs text-amber-800 bg-amber-50 rounded-lg px-2 py-1">{t("auditView.personHint")}</p>}
      {g.web && (
        <a href={g.web.source_url ?? undefined} target="_blank" rel="noopener noreferrer"
           className="inline-block text-xs px-2 py-0.5 rounded-full bg-sky-50 text-sky-800 hover:underline">
          🔎 ② Tavily: {webLabel(g.web)}{g.web.source_domain ? ` · ${g.web.source_domain}` : ""}
        </a>
      )}
      <ul className="border-t border-slate-100 pt-1">{g.transactions.map((x) => <TxRow key={x.id} x={x} fmtDate={fmtDate}
                                                                         onOpen={onOpenSheet} openLabel={t("auditView.act.openSheet")} />)}</ul>
      <div className="border-t border-slate-100 pt-2 space-y-2">
        <CategoryPicker chart={chart} busy={busy} t={t} cta={t("auditView.act.categorizeAll", { count: g.count })}
                        onPick={(c) => onCategorize(ids, catToType[c], c)} />
        <div className="flex flex-wrap gap-2">
          <button onClick={() => onAskRule(g)} disabled={busy || g.person_payment}
                  className="px-2.5 py-1 rounded-lg text-xs font-semibold border border-emerald-700 text-emerald-800 disabled:opacity-40">
            ④ {t("auditView.act.askRule")}
          </button>
          {onOpenSheet && <button onClick={() => onOpenSheet(g.transactions[0]?.id)} className="px-2 py-1 rounded-lg text-xs text-slate-600 hover:bg-slate-100">{t("auditView.act.openSheet")}</button>}
        </div>
      </div>
    </div>
  );
}

function ProposalDetail({ p, idx, decided, t, label, webLabel, fmtDate, busy, onApprove, onDismiss }: {
  p: FormulaProposal; idx: number; decided?: string; t: T; label: (s: string) => string;
  webLabel: (w: { business_type: string; confidence: string | null }) => string; fmtDate: (s: string) => string;
  busy: boolean; onApprove: (i: number, p: FormulaProposal) => void;
  onDismiss: (i: number, p: FormulaProposal, r: DismissReason, text: string) => void;
}) {
  const [showRows, setShowRows] = useState(false);
  const [dismissing, setDismissing] = useState(false);
  const [reason, setReason] = useState<DismissReason>("wrong_category");
  const [why, setWhy] = useState("");
  const period = p.valid_from || p.valid_to ? `${p.valid_from ? fmtDate(p.valid_from) : "…"} → ${p.valid_to ? fmtDate(p.valid_to) : "…"}` : null;
  const amount = p.amount_min != null || p.amount_max != null
    ? `${p.amount_min != null ? `≥ ${money(p.amount_min)}` : ""}${p.amount_min != null && p.amount_max != null ? " · " : ""}${p.amount_max != null ? `≤ ${money(p.amount_max)}` : ""}` : null;
  return (
    <div className="space-y-2.5">
      <div>
        <div className="text-xs text-emerald-700">④ {t("auditView.proposal")}</div>
        <h3 className="text-sm font-semibold text-slate-900 mt-0.5 flex flex-wrap items-center gap-1.5">
          <span className="font-mono">{p.keyword}</span>
          {period && <span className="text-[11px] font-normal px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-800">{period}</span>}
          {amount && <span className="text-[11px] font-normal px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-800">{amount}</span>}
          <span className="text-slate-400 font-normal">→</span>
          <span className="font-normal">{label(`${p.account_type}/${p.category}`)}</span>
        </h3>
      </div>
      {p.explanation && <p className="text-sm text-slate-600">{p.explanation}</p>}
      {/* El POR QUÉ, como evidencia */}
      <div className="flex flex-wrap gap-1.5 text-[11px]">
        {p.notes_used.map((n) => (
          <span key={n.id} title={n.text} className="px-2 py-0.5 rounded-full bg-violet-50 text-violet-800 max-w-full truncate">📝 {n.text}</span>
        ))}
        {p.corrections_used > 0 && (
          <span className="px-2 py-0.5 rounded-full bg-slate-100 text-slate-700">✋ {t("auditView.yourCorrections", { count: p.corrections_used })}</span>
        )}
        {p.web_evidence.map((e, k) => (
          <a key={k} href={e.source_url ?? undefined} target="_blank" rel="noopener noreferrer"
             className="px-2 py-0.5 rounded-full bg-sky-50 text-sky-800 hover:underline">🔎 {webLabel(e)}{e.source_domain ? ` · ${e.source_domain}` : ""}</a>
        ))}
      </div>
      {p.warnings.map((w) => <p key={w} className="text-xs text-amber-800 bg-amber-50 rounded-lg px-2 py-1">⚠️ {t(`agents.warning.${w}`)}</p>)}
      {/* La prueba contra la historia */}
      <div className="text-xs text-slate-700 bg-slate-50 rounded-lg px-2 py-1.5">
        <button onClick={() => setShowRows(!showRows)} className="text-start w-full">
          <span className="text-slate-400">{showRows ? "▾" : "▸"} </span>
          {t("agents.test", { count: p.test.changes })}
          {p.test.by_pair.map((b) => ` · ${label(b.from)} → ${label(b.to)}: ${b.rows}`).join("")}
        </button>
        {(p.test.protected_manual > 0 || p.test.realigned > 0) && (
          <div className="text-slate-500 mt-0.5">
            {p.test.protected_manual > 0 && t("agents.protected", { count: p.test.protected_manual })}
            {p.test.protected_manual > 0 && p.test.realigned > 0 && " · "}
            {p.test.realigned > 0 && t("agents.realigned", { count: p.test.realigned })}
          </div>
        )}
        {showRows && (
          <ul className="mt-1">{p.test.changed.map((x) => <TxRow key={x.id} x={x} fmtDate={fmtDate} extra={`${label(x.from)} → ${label(x.to)}`} />)}</ul>
        )}
      </div>
      {/* Decidir */}
      <div className="border-t border-slate-100 pt-2">
        {decided ? <p className="text-xs text-emerald-700 font-medium">{decided}</p> : dismissing ? (
          <div className="space-y-1.5">
            <div className="flex flex-wrap gap-1.5">
              {REASONS.map((r) => (
                <button key={r} onClick={() => setReason(r)}
                        className={`px-2 py-0.5 rounded-full text-[11px] ring-1 ring-inset ${reason === r ? "bg-slate-800 text-white ring-slate-800" : "text-slate-600 ring-slate-300"}`}>
                  {t(`auditView.reason.${r}`)}
                </button>
              ))}
            </div>
            <input value={why} onChange={(e) => setWhy(e.target.value)} maxLength={300} placeholder={t("auditView.reasonText")}
                   className="w-full rounded-lg border border-slate-200 px-2 py-1 text-xs" />
            <div className="flex gap-2 justify-end">
              <button onClick={() => setDismissing(false)} className="px-2.5 py-1 rounded-lg text-xs text-slate-600 hover:bg-slate-100">{t("notes.cancel")}</button>
              <button onClick={() => onDismiss(idx, p, reason, why)} disabled={busy}
                      className="px-2.5 py-1 rounded-lg text-xs font-semibold text-white bg-slate-700 disabled:opacity-40">{t("agents.discard")}</button>
            </div>
          </div>
        ) : (
          <div className="flex gap-2 justify-end">
            <button onClick={() => setDismissing(true)} className="px-2.5 py-1 rounded-lg text-xs text-slate-600 hover:bg-slate-100">{t("agents.discard")}</button>
            <button onClick={() => onApprove(idx, p)} disabled={busy}
                    className="px-3 py-1 rounded-lg text-xs font-semibold text-white bg-emerald-700 disabled:opacity-40">{t("agents.approve")}</button>
          </div>
        )}
      </div>
    </div>
  );
}
