import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AgentError, applyRuleProposals, dismissProposal, getAgentEstimate, getTourProposals, getAgentTeam, getAgentUsage, getAuditBoard, getBookNotes, getCategories, money,
  resolveFinding, runAudit, runFormulaDrafter, runMerchantVerifier, updateTransaction,
  type AgentEstimate, type AgentSpent, type AgentTeam, type AgentUsageSummary, type AuditBoard, type BoardFinding, type ChartOfAccounts,
  type DismissReason, type DraftResponse, type FormulaProposal, type PendingGroup, type TeamAgent, type TeamAgentKey, type TxSummary,
} from "../api";
import { SECTION_LABEL } from "../accounting";
import { DEMO_MODE, inTour } from "../demo";
import MerchantSearch from "./MerchantSearch";
import { LiveOnly } from "./DemoDoors";   // visita guiada del demo (Firm, 23-oct)
import JudgedBy from "./JudgedBy";          // quién juzgó cada hallazgo (Firm, 23-oct)
import {
  BTN_GHOST, BTN_PRIMARY, BTN_SECONDARY, Badge, EmptyRow as Empty, ListCard, ListDetail, ListRow as QueueRow, Tabs,
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
type StepState = "idle" | "queued" | "running" | "done" | "error" | "skipped";   // queued: "Revisar el libro" lo correrá
// 18-oct (ronda de diseño, "la IA al frente"): cada paso con su botón y sus integrantes a la vista. En pantalla
// los agentes se nombran por lo que hacen (`auditView.agent.*`), nunca ①…⑥; los números quedan para el código:
// note_checker = ⑥ · personal_judge = ⑤ · reviewer = ① · verifier = ② · drafter = ④.
type Member = "auditor" | TeamAgentKey;
const STEP_MEMBERS: Record<Step, Member[]> = {
  audit: ["auditor", "note_checker", "personal_judge", "reviewer"],
  verify: ["verifier"],
  draft: ["drafter"],
};
const STEP_MARK: Record<StepState, string> = { idle: "", queued: "", running: "● ", done: "✓ ", error: "✗ ", skipped: "– " };
// Cada cuánto se pregunta al backend, MIENTRAS corre, qué agentes ya reportaron (solo lee `agentcall`; no gasta IA).
const POLL_MS = 2500;

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

// 18-oct (ronda de diseño 2): el detalle de cada agente (modelo, llamadas, segundos, costo) se ve completo en el DEMO
// (los jueces lo quieren ver); en el producto, el bookkeeper ve los 3 pasos y su resultado en una línea, y "Ver
// detalle" despliega lo demás. La preferencia se recuerda en este navegador.
const DETAIL_KEY = "contabu.audit.teamDetail.v1";

export default function AuditWorkspace({
  onOpenSheet,
  onStats,
  onOpenNotes,
  proposeNote,
  onProposeNoteDone,
}: {
  onOpenSheet?: (txId?: number) => void;                      // a Bookkeeping (la Hoja), a ESA fila
  onStats?: (s: AuditBoard["stats"]) => void;                 // contadores de la barra lateral
  onOpenNotes?: () => void;                                   // a la página de Notas (18-oct)
  // Desde Notas, "Proponer reglas para esta nota": el ④ corre AQUÍ, donde viven las propuestas. `n` cambia cada vez.
  proposeNote?: { id: number; n: number } | null;
  onProposeNoteDone?: () => void;
}) {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language || "en").split("-")[0];
  const { label, fmtDate, ago, webLabel, detectorSentence, estimateText, spentText } = useAuditText();

  const [board, setBoard] = useState<AuditBoard | null>(null);
  const [chart, setChart] = useState<ChartOfAccounts>({});
  const [proposals, setProposals] = useState<FormulaProposal[]>([]);
  useEffect(() => { if (inTour()) getTourProposals().then(setProposals).catch(() => {}); }, []);   // visita guiada (Firm, 23-oct): las guardadas, en solo lectura
  const [rejected, setRejected] = useState(0);
  // 20-oct (prueba de campo): cuando el ④ deja 0 propuestas, POR QUÉ y qué hacer (antes solo "0 pasaron").
  const [draftWhy, setDraftWhy] = useState<DraftWhyData | null>(null);
  const [tab, setTab] = useState<Tab>("findings");
  const [sel, setSel] = useState<Sel>(null);
  // En pantalla angosta el detalle queda DEBAJO de la cola: al elegir algo, `ListDetail` baja hasta él.
  const [steps, setSteps] = useState<Record<Step, StepState>>({ audit: "idle", verify: "idle", draft: "idle" });
  const [running, setRunning] = useState<Step | null>(null);
  // 20-oct: el botón se apaga AL INSTANTE del clic. Antes `running` se ponía recién después de `beginAction()` (una
  // ida y vuelta al backend: 13 s con el demo en frío) y un segundo clic en ese hueco lanzaba otra revisión entera
  // (✅ logs de Cloud Run, libro 7, 15:28 UTC: los 3 pasos, dos veces). La ref no espera a que React pinte;
  // `launching` es lo que se ve. El backend además rechaza el segundo paso (409 `agent_busy`).
  const runLock = useRef(false);
  const [launching, setLaunching] = useState(false);
  const [stepNote, setStepNote] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [usage, setUsage] = useState<AgentUsageSummary | null>(null);
  // Lo que costaría cada acción (ANTES) y lo que costó la última (DESPUÉS), 14-oct.
  const [est, setEst] = useState<Partial<Record<Step | "review_book", AgentEstimate>>>({});
  // "El equipo trabajando" (18-oct): qué hizo cada agente en la última corrida, leído de `agentcall`.
  const [team, setTeam] = useState<AgentTeam | null>(null);
  const [lastSpent, setLastSpent] = useState<AgentSpent | null>(null);
  // Las Notas viven en su página (18-oct): aquí, solo cuántas hay activas y un enlace.
  const [noteCount, setNoteCount] = useState<{ active: number; paused: number } | null>(null);
  const [teamOpen, setTeamOpen] = useState<boolean>(() => {
    try { return window.localStorage.getItem(DETAIL_KEY) === "1"; } catch { return false; }
  });
  const toggleTeam = () => setTeamOpen((v) => {
    try { window.localStorage.setItem(DETAIL_KEY, v ? "0" : "1"); } catch { /* sin almacenamiento: solo esta vez */ }
    return !v;
  });
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
    // Los estimados se recalculan con el libro como quedó (no bloquean la pantalla). En la visita guiada no: es de
    // solo lectura, la API los niega (403) y ensuciaban la consola del navegador (revisión externa, 26-oct).
    if (!inTour()) Promise.all((["audit", "verify", "draft", "review_book"] as const).map((a) => getAgentEstimate(a, { lang })))
      .then(([audit, verify, draft, review_book]) => setEst({ audit, verify, draft, review_book })).catch(() => {});
    return b;
  }, [onStats, lang]);

  /** La acción en curso (o la última de esta pantalla) empezó DESPUÉS de esta llamada: el panel del equipo muestra
   *  solo lo suyo, exacto (18-oct). `null` = no se sabe (al abrir la página, o tras "Buscar este comercio"): el
   *  backend agrupa la última corrida por pausas. */
  const sinceRef = useRef<number | null>(null);
  const beginAction = useCallback(async () => {
    sinceRef.current = (await getAgentTeam().catch(() => null))?.max_id ?? null;
  }, []);

  /** El costo del libro y lo que hizo el equipo: después de cada acción de los agentes. */
  const refreshUsage = useCallback(() => {
    getAgentUsage().then(setUsage).catch(() => {});
    getAgentTeam(sinceRef.current).then(setTeam).catch(() => {});
  }, []);

  // 18-oct: MIENTRAS corre un paso, el panel se llena agente por agente (cada uno escribe su fila en `agentcall`
  // al terminar su llamada). Se detiene al terminar, o si el backend no contesta. Una respuesta que llega después
  // de detenerse se ignora (no pisa la final).
  useEffect(() => {
    if (running === null) return;
    let alive = true;
    const id = window.setInterval(() => {
      getAgentTeam(sinceRef.current).then((x) => { if (alive) setTeam(x); })
        .catch(() => { alive = false; window.clearInterval(id); });
    }, POLL_MS);
    return () => { alive = false; window.clearInterval(id); };
  }, [running]);

  useEffect(() => {
    getCategories().then(setChart).catch(() => {});
    getBookNotes().then((ns) => setNoteCount({ active: ns.filter((n) => !n.paused_at).length, paused: ns.filter((n) => n.paused_at).length }))
      .catch(() => setNoteCount(null));
    refreshUsage();
  }, [refreshUsage]);
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

  /** Corre un paso. `stop`: otro paso de agentes ya corre sobre este libro (409 `agent_busy`): no se sigue. */
  async function runStep(step: Step, b: AuditBoard | null): Promise<{ b: AuditBoard | null; stop: boolean }> {
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
        setStepNote(t("auditView.step.searched", { searched: r.searched, cached: r.from_cache, credits: r.credits, pending: r.pending ?? 0,
                                                   rejudged: r.rejudge?.rejudged ?? 0 }));
      } else {
        setStepNote(t("auditView.step.drafting"));
        const r = await runFormulaDrafter(lang);
        addSpent(r.spent);
        setProposals(r.proposals);
        setRejected(r.rejected.length);
        setDraftWhy(draftWhyOf(r));
        setDecided({});
      }
      setSteps((s) => ({ ...s, [step]: "done" }));
      getAgentTeam(sinceRef.current).then(setTeam).catch(() => {});
    } catch (e) {
      if (e instanceof AgentError && e.code === "agent_busy") {
        setSteps({ audit: "idle", verify: "idle", draft: "idle" });
        setError(errText(e));
        return { b, stop: true };
      }
      // Un paso que falla (p. ej. sin clave de búsqueda) no detiene el flujo: se marca y se sigue.
      setSteps((s) => ({ ...s, [step]: e instanceof AgentError && /unavailable/.test(e.code) ? "skipped" : "error" }));
      setError(errText(e));
    }
    return { b, stop: false };
  }

  /** Una acción de los agentes a la vez en esta pantalla: `false` si ya hay una (el clic se ignora). */
  function claimRun(): boolean {
    if (runLock.current) return false;
    runLock.current = true;
    setLaunching(true);
    setDraftWhy(null);
    return true;
  }
  function releaseRun() {
    runLock.current = false;
    setLaunching(false);
  }

  async function reviewBook() {
    if (!claimRun()) return;
    try {
      setError(null);
      setLastSpent(null);
      setSteps({ audit: "queued", verify: "queued", draft: "queued" });
      await beginAction();
      let b: AuditBoard | null = board;
      for (const step of STEPS) {
        const r = await runStep(step, b);
        b = r.b;
        if (r.stop) break;
      }
      setRunning(null);
      setStepNote("");
      await loadBoard().catch(() => {});
      refreshUsage();
    } finally { releaseRun(); }
  }

  async function runOne(step: Step) {
    if (!claimRun()) return;
    try {
      setError(null);
      setLastSpent(null);
      setSteps({ audit: "idle", verify: "idle", draft: "idle" });
      await beginAction();
      await runStep(step, board);
      setRunning(null);
      setStepNote("");
      if (step !== "audit") await loadBoard().catch(() => {});
      if (step === "draft") setTab("proposals");
      refreshUsage();
    } finally { releaseRun(); }
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
    if (!claimRun()) return;
    setBusy(true); setError(null);
    try {
      await beginAction();
      const r = await runFormulaDrafter(lang, g.transactions.map((x) => x.id));
      setLastSpent(r.spent ?? null);
      if (r.proposals.length) {
        setProposals((prev) => [...r.proposals, ...prev]);
        setDecided({});
        setTab("proposals");
        setSel({ kind: "proposal", idx: 0 });
      } else {
        setDraftWhy(draftWhyOf(r, g.merchant));
      }
      refreshUsage();
    } catch (e) { setError(errText(e)); } finally { setBusy(false); releaseRun(); }
  }

  /** "Proponer las filas para esta nota" (14-oct): el ④ corre SOLO para esa nota. */
  async function proposeForNote(noteId: number): Promise<number> {
    setError(null);
    await beginAction();
    const r = await runFormulaDrafter(lang, undefined, noteId);
    setLastSpent(r.spent ?? null);
    if (r.proposals.length) {
      setProposals((prev) => [...r.proposals, ...prev]);
      setDecided({});
      setTab("proposals");
      setSel({ kind: "proposal", idx: 0 });
    }
    refreshUsage();
    return r.proposals.length;
  }

  // Llegó desde Notas con "Proponer reglas para esta nota": corre una vez por pedido (`n`).
  const handledNote = useRef<number | null>(null);
  useEffect(() => {
    if (!proposeNote || handledNote.current === proposeNote.n) return;
    handledNote.current = proposeNote.n;
    if (!claimRun()) { onProposeNoteDone?.(); return; }
    setBusy(true);
    setRunning("draft");
    setSteps({ audit: "idle", verify: "idle", draft: "running" });
    setStepNote(t("auditView.step.drafting"));
    proposeForNote(proposeNote.id)
      .then((count) => { setSteps((x) => ({ ...x, draft: "done" })); if (!count) setError(t("notesPage.noRulesForNote")); })
      .catch((e) => { setSteps((x) => ({ ...x, draft: "error" })); setError(errText(e)); })
      .finally(() => { setBusy(false); setRunning(null); setStepNote(""); releaseRun(); onProposeNoteDone?.(); });
  }, [proposeNote]); // eslint-disable-line react-hooks/exhaustive-deps

  /** Después de "Buscar este comercio": la evidencia y el veredicto nuevos se ven en el tablero. */
  async function afterSearch(s?: AgentSpent) {
    setLastSpent(s ?? null);
    sinceRef.current = null;   // la búsqueda la lanzó `MerchantSearch`: no se sabe desde qué llamada empezó
    await loadBoard().catch(() => {});
    refreshUsage();
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

  const modelName = "Nemotron · Nebius Token Factory";
  const costTip = usage
    ? t("auditView.costTip", {
        cost: `$${(usage.total_cost_usd ?? 0).toFixed(5)}`, calls: usage.left_today, cap: usage.daily_cap,
        searches: usage.searches_left_today, scap: usage.daily_search_cap,
      })
    : "";

  return (
    <div className="space-y-3">
      {/* 1. LA IA AL FRENTE (18-oct): los pasos a la vista, cada agente con su nombre y lo que hizo. Ronda 2: "Revisar el
          libro" vive AQUÍ (es el botón que corre los 3 pasos) y la línea de estado de arriba se fue: el "hace cuánto" y el
          costo ya los dice el equipo. */}
      <TeamPanel team={team} exact={sinceRef.current !== null} steps={steps} running={running} launching={launching} stepNote={stepNote} est={est}
                 disabled={running !== null || busy || launching}
                 onRun={(s) => void runOne(s)} onReviewBook={() => void reviewBook()} lastSpent={lastSpent} spentText={spentText}
                 detailed={DEMO_MODE || teamOpen} canCollapse={!DEMO_MODE} onToggle={toggleTeam} ago={ago} estimateText={estimateText} />

      {error && <p className="text-sm text-rose-700 bg-rose-50 rounded-lg px-3 py-2">{error}</p>}
      {draftWhy && !running && <DraftWhy d={draftWhy} onClose={() => setDraftWhy(null)} />}

      {/* 2. LAS NOTAS, en su página (ronda 2): aquí una línea con el enlace. */}
      {noteCount && (
        <button onClick={onOpenNotes}
                className="w-full bg-white rounded-xl border border-slate-200 px-3 py-2 flex items-center gap-2 text-sm text-start hover:bg-slate-50">
          <span className="text-slate-700">
            {noteCount.active + noteCount.paused === 0 ? t("notesPage.auditLine.none")
              : [t("notesPage.auditLine.active", { count: noteCount.active }),
                 ...(noteCount.paused ? [t("notesPage.auditLine.paused", { count: noteCount.paused })] : [])].join(" · ")}
          </span>
          <span className="ms-auto shrink-0 text-xs font-semibold text-emerald-800">{t("notesPage.auditLine.open")}</span>
        </button>
      )}

      {/* 3. LA COLA + EL DETALLE (estructura común: `ListDetail`). Jose, 19-oct: el detalle (un hallazgo,
          un grupo de Pendientes, una propuesta) necesita más espacio que la cola → antes `wide="detail"`
          (lista 300px fija); tras mirarlo, Jose lo quiere 60/40 → `wide="split"` (PageKit, de Firm). */}
      <ListDetail
        wide="split"
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

              {/* Cuántos son y qué cuenta el número de la pestaña: hallazgos + observaciones (revisión externa, 26-oct). */}
              {tab === "findings" && board && findings.length > 0 && (
                <li className="px-3 py-1.5 text-[11px] text-slate-500 border-b border-slate-100">
                  {board.stats.info > 0 ? t("auditView.listHeadInfo", { count: board.stats.to_review, info: board.stats.info })
                                        : t("auditView.listHead", { count: board.stats.to_review })}
                </li>)}
              {tab === "findings" && findings.map((f) => (
                <QueueRow key={f.id} tour={`finding-${f.id}`} active={sel?.kind === "finding" && sel.id === f.id} onClick={() => setSel({ kind: "finding", id: f.id })}
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
                <QueueRow key={i} tour={`proposal-${i}`} active={sel?.kind === "proposal" && sel.idx === i} onClick={() => setSel({ kind: "proposal", idx: i })}
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
        // 23-oct: separado a propósito por reglas con monto o fechas → solo las filas que no las siguen.
        const head = p.by_rules
          ? t("auditView.say.inconsistent_by_rules", { count: ((p.odd_ids as number[] | undefined) ?? []).length })
          : t("auditView.say.inconsistent_merchant", { count: cats.length, cats: cats.map(label).join(" · ") });
        return head + (manual ? " " + t("auditView.say.byHand", { count: manual }) : "");
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

/** "El equipo de IA" (18-oct): los tres pasos con su botón; en cada uno, sus integrantes con el modelo que usan y
 *  lo que hicieron en la ÚLTIMA corrida (datos de `agentcall`, nunca inventados). Mientras corre, el paso se marca.
 *  Ronda 2: "Revisar el libro" (los 3 pasos en orden) va en su encabezado. `detailed` = cada integrante a la vista
 *  (siempre en el demo); si no, cada paso dice su resultado en UNA línea y "Ver detalle" despliega lo demás. */
function TeamPanel({ team, exact, steps, running, launching, stepNote, est, disabled, onRun, onReviewBook, lastSpent, spentText,
                     detailed, canCollapse, onToggle, ago, estimateText }: {
  team: AgentTeam | null; exact: boolean; steps: Record<Step, StepState>; running: Step | null; launching: boolean;
  stepNote: string; est: Partial<Record<Step | "review_book", AgentEstimate>>; disabled: boolean; onRun: (s: Step) => void;
  onReviewBook: () => void; lastSpent: AgentSpent | null; spentText: (s: AgentSpent | null | undefined) => string;
  detailed: boolean; canCollapse: boolean; onToggle: () => void;
  ago: (secs: number) => string; estimateText: (e: AgentEstimate | null | undefined) => string;
}) {
  const { t } = useTranslation();
  const byKey = Object.fromEntries((team?.agents ?? []).map((a) => [a.agent, a])) as Partial<Record<TeamAgentKey, TeamAgent>>;
  const r = team?.run;
  /** El resultado del paso en una línea (vista compacta): lo que hizo cada integrante que corrió, sin modelos ni costos. */
  const summary = (s: Step): { text: string; tone: string } => {
    if (steps[s] === "running") return { text: running === s && stepNote ? stepNote : t("auditView.team.working"), tone: "text-emerald-700 animate-pulse" };
    if (steps[s] === "queued") return { text: t("auditView.team.queued"), tone: "text-slate-500" };
    if (steps[s] === "error") return { text: t("auditView.team.stepFailed"), tone: "text-rose-700" };
    const did = STEP_MEMBERS[s].flatMap((m) => {
      const x = m === "auditor" ? undefined : byKey[m]?.run;
      if (!x) return [];
      const count = m === "verifier" ? x.calls : m === "drafter" ? x.items_out : x.items_valid;
      return [t(`auditView.team.did.${m}`, { count }) + (m === "drafter" ? ` · ${t("auditView.team.passed", { count: x.items_valid })}` : "")];
    });
    if (did.length) return { text: did.join(" · "), tone: "text-slate-700" };
    if (!team) return { text: "", tone: "" };
    return { text: r ? (s === "audit" ? t("auditView.team.codeOnly") : t("auditView.team.didNotRun")) : t("auditView.team.never"), tone: "text-slate-500" };
  };
  return (
    <section data-tour="ai-team" className="bg-white rounded-xl border border-slate-200 p-3 space-y-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <h2 className="text-sm font-semibold text-slate-900">{t("auditView.team.title")}</h2>
        {team && (
          <span className="text-xs text-slate-500">
            {r ? [t("auditView.team.lastRun", { when: ago(r.seconds_ago) }), t("auditView.team.calls", { count: r.calls }),
                  `$${r.cost_usd.toFixed(5)}`, ...(r.credits ? [t("auditView.team.credits", { count: r.credits })] : [])].join(" · ")
               : running ? "" : exact ? t("auditView.team.nothingThisRun") : t("auditView.team.neverRun")}
          </span>
        )}
        {running && <span className="text-xs text-emerald-800">{stepNote}</span>}
        <div className="ms-auto flex items-center gap-1.5">
          {canCollapse && (
            <button onClick={onToggle} className={BTN_GHOST} aria-expanded={detailed}>
              {detailed ? t("auditView.team.hideDetail") : t("auditView.team.showDetail")}
            </button>
          )}
          {/* La acción principal de la página: corre Auditar → Verificar → Redactar, en orden. */}
          <LiveOnly><button onClick={onReviewBook} disabled={disabled} className={BTN_PRIMARY} title={estimateText(est.review_book)}>
            {running || launching ? t("auditView.running") : t("auditView.reviewBook")}
          </button></LiveOnly>
        </div>
      </div>
      {/* ANTES: lo que costaría "Revisar el libro". DESPUÉS: lo que costó la última acción. */}
      {(lastSpent && !running) ? <p className="text-[11px] text-emerald-800">{spentText(lastSpent)}</p>
        : est.review_book && <p className="text-[11px] text-slate-400">{t("auditView.est.line", { action: t("auditView.reviewBook"), what: estimateText(est.review_book) })}</p>}
      <div className="grid gap-2 md:grid-cols-3">
        {STEPS.map((s, i) => {
          const sum = detailed ? null : summary(s);
          return (
            <div key={s} className={`rounded-lg border ${detailed ? "p-2.5 space-y-2" : "px-2.5 py-2"} ${steps[s] === "running" ? "border-emerald-400 bg-emerald-50/50" : "border-slate-200"}`}>
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <div className={`text-xs font-semibold ${steps[s] === "error" ? "text-rose-700" : "text-slate-900"}`}>
                    {STEP_MARK[steps[s]]}{i + 1}. {t(`auditView.stepName.${s}`)}
                  </div>
                  {detailed && <p className="text-[11px] text-slate-500">{t(`auditView.team.step.${s}`)}</p>}
                  {sum?.text && <p className={`text-[11px] truncate ${sum.tone}`} title={sum.text}>{sum.text}</p>}
                </div>
                <LiveOnly compact><button onClick={() => onRun(s)} disabled={disabled} className={BTN_SECONDARY} title={estimateText(est[s])}>
                  {t("auditView.team.run")}
                </button></LiveOnly>
              </div>
              {detailed && est[s] && <p className="text-[11px] text-slate-400">{estimateText(est[s])}</p>}
              {detailed && (
                <ul className="space-y-1.5 border-t border-slate-100 pt-1.5">
                  {STEP_MEMBERS[s].map((m) => <TeamMember key={m} m={m} a={m === "auditor" ? undefined : byKey[m]} loaded={team !== null}
                                                           state={steps[s]} ago={ago} />)}
                </ul>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

/** Por qué el ④ no dejó propuestas (20-oct). Los motivos del validador, agrupados como los lee el bookkeeper. */
type DraftWhyData = { nothing: boolean; total: number; counts: Record<string, number>; merchant?: string };
const WHY_GROUP: Record<string, string> = {
  no_evidence: "evidence", weak_evidence: "evidence", unrelated_evidence: "evidence", person_without_evidence: "evidence",
  contradicts_corrections: "contradicts", no_effect: "noEffect", matches_nothing: "noEffect",
  duplicate_rule: "repeated", repeated_in_proposal: "repeated", dismissed_before: "repeated",
  outside_note: "outside", outside_focus: "outside",
};

/** `null` si hubo propuestas. Con un backend anterior al 20-oct (sin `rejection_counts`), los cuenta de `rejected`. */
function draftWhyOf(r: DraftResponse, merchant?: string): DraftWhyData | null {
  if (r.proposals.length) return null;
  const counts = r.rejection_counts ?? r.rejected.reduce<Record<string, number>>((acc, x) => {
    const k = x.errors[0]; if (k) acc[k] = (acc[k] ?? 0) + 1; return acc;
  }, {});
  return { nothing: r.outcome === "nothing_qualifies", total: r.rejected.length, counts, merchant };
}

function DraftWhy({ d, onClose }: { d: DraftWhyData; onClose: () => void }) {
  const { t } = useTranslation();
  const groups: Record<string, number> = {};
  for (const [code, n] of Object.entries(d.counts)) {
    const g = WHY_GROUP[code] ?? "invalid";
    groups[g] = (groups[g] ?? 0) + n;
  }
  const sorted = Object.entries(groups).sort((a, b) => b[1] - a[1]);
  // "Qué hacer" solo cuando lo que falta es respaldo (nada calificaba, o hubo filas sin evidencia).
  const showTodo = d.nothing || (groups.evidence ?? 0) > 0 || d.total === 0;
  return (
    <div className="text-sm text-amber-900 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 space-y-1">
      <div className="flex items-start gap-2">
        <p className="font-medium flex-1">
          {d.merchant ? t("auditView.noRuleForMerchant", { merchant: d.merchant }) : t("auditView.draftWhy.title")}
        </p>
        <button onClick={onClose} className="text-amber-700 hover:text-amber-900 text-xs" aria-label="×">×</button>
      </div>
      {d.nothing ? <p className="text-xs">{t("auditView.draftWhy.nothing")}</p>
        : d.total > 0 && (
          <>
            <p className="text-xs">{t("auditView.draftWhy.rejected", { count: d.total })}</p>
            <ul className="text-xs list-disc ps-5">
              {sorted.map(([g, n]) => <li key={g}>{t("auditView.draftWhy.line", { count: n, reason: t(`auditView.draftWhy.reason.${g}`) })}</li>)}
            </ul>
          </>
        )}
      {showTodo && <p className="text-xs">{t("auditView.draftWhy.todo")}</p>}
    </div>
  );
}

/** Un integrante: su nombre por lo que hace, su modelo y lo que hizo (o que no corrió, y por qué puede pasar). */
function TeamMember({ m, a, loaded, state, ago }: {
  m: Member; a?: TeamAgent; loaded: boolean; state: StepState; ago: (secs: number) => string;
}) {
  const { t } = useTranslation();
  const model = m === "auditor" ? t("auditView.team.code")
    : a ? `${a.model.label}${a.model.reasoning ? ` · ${t("auditView.team.reasoning")}` : ""}` : "";
  let line: string = "";
  let why: string | null = null;
  if (m === "auditor") line = t("auditView.team.auditor");
  else if (a?.run) {
    const x = a.run;
    // Lo que produjo, según lo que guarda cada agente en `items_out` / `items_valid` (ver `agent_calls.record`).
    // El ④: lo que escribió el modelo y lo que pasó TODOS los controles del código (lo que llega a Propuestas).
    const did = m === "verifier" ? x.calls : m === "drafter" ? x.items_out : x.items_valid;
    line = [
      t(`auditView.team.did.${m}`, { count: did }),
      ...(m === "drafter" ? [t("auditView.team.passed", { count: x.items_valid })] : []),
      // 20-oct: si alguna no pasó, el motivo principal (guardado en `agentcall.rejections`).
      ...(m === "drafter" && x.rejections ? Object.entries(x.rejections).slice(0, 1).map(([code, n]) =>
        t("auditView.draftWhy.line", { count: n, reason: t(`auditView.draftWhy.reason.${WHY_GROUP[code] ?? "invalid"}`) })) : []),
      ...(m === "verifier" ? [] : [t("auditView.team.calls", { count: x.calls })]),
      ...(x.ok < x.calls ? [t("auditView.team.failed", { count: x.calls - x.ok })] : []),
      t("auditView.team.seconds", { s: (x.latency_ms / 1000).toFixed(1) }),
      m === "verifier" ? t("auditView.team.credits", { count: x.credits }) : `$${x.cost_usd.toFixed(5)}`,
    ].join(" · ");
  } else if (state === "running") {
    line = t("auditView.team.working");   // su paso corre de verdad y todavía no reportó
  } else if (state === "queued") {
    line = t("auditView.team.queued");
  } else if (a && a.last_seconds_ago != null) {
    line = `${t("auditView.team.didNotRun")} ${t("auditView.team.lastWorked", { when: ago(a.last_seconds_ago) })}`;
    why = t(`auditView.team.why.${m}`);
  } else if (loaded) {
    line = t("auditView.team.never");
    why = t(`auditView.team.why.${m}`);
  }
  return (
    <li className="text-xs">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-medium text-slate-800">{t(`auditView.agent.${m}`)}</span>
        {model && <span className="px-1.5 py-0.5 rounded bg-slate-100 text-[10px] text-slate-600">{model}</span>}
      </div>
      {line && <div className={`text-[11px] ${a?.run ? "text-slate-700" : state === "running" ? "text-emerald-700 animate-pulse" : "text-slate-500"}`}>{line}</div>}
      {why && <div className="text-[11px] text-slate-400">{why}</div>}
    </li>
  );
}

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
        <JudgedBy findingId={f.id} />
      </div>
      {/* Lo que vio el auditor */}
      <p className="text-sm text-slate-700">{sentence}</p>
      {/* El ① revisor ("Revisa cada hallazgo": en pantalla, por su nombre, 18-oct) */}
      {!f.is_info && (
        f.agent_verdict ? (
          <div>
            <div className="text-[11px] text-slate-400">{t("auditView.agent.reviewer")}</div>
            <div className="flex items-start gap-2">
              <Badge cls={VERDICT_STYLE[f.agent_verdict]}>{t(`agents.verdict.${f.agent_verdict}`)}</Badge>
              {f.observation_stale
                ? <span className="text-xs text-slate-400 italic">{t("auditView.staleVerdict")}</span>
                : <span className="text-sm text-slate-600">{f.agent_observation}</span>}
            </div>
          </div>
        ) : <p className="text-xs text-slate-400">{t("auditView.notReviewed")}</p>
      )}
      {/* La evidencia del ② (Tavily es la fuente) */}
      {f.web && (
        <a href={f.web.source_url ?? undefined} target="_blank" rel="noopener noreferrer"
           className="inline-block text-xs px-2 py-0.5 rounded-full bg-sky-50 text-sky-800 hover:underline">
          🔎 Tavily: {webLabel(f.web)}{f.web.source_domain ? ` · ${f.web.source_domain}` : ""}
        </a>
      )}
      {f.transactions.length > 0 && (
        <ul className="border-t border-slate-100 pt-1">
          {f.transactions.map((x) => <TxRow key={x.id} x={x} fmtDate={fmtDate} extra={(x.category ? label(`${x.account_type}/${x.category}`) : t("agents.pending")) +
            // 23-oct: lo que decide el motor para ESTA fila, cuando el comercio está separado por reglas con condición.
            ((p.suggested_by_tx as Record<string, string> | undefined)?.[String(x.id)]
              ? ` → ${t("auditView.say.ruleSays", { s: label((p.suggested_by_tx as Record<string, string>)[String(x.id)]) })}` : "")}
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
          🔎 Tavily: {webLabel(g.web)}{g.web.source_domain ? ` · ${g.web.source_domain}` : ""}
        </a>
      )}
      <ul className="border-t border-slate-100 pt-1">{g.transactions.map((x) => <TxRow key={x.id} x={x} fmtDate={fmtDate}
                                                                         onOpen={onOpenSheet} openLabel={t("auditView.act.openSheet")} />)}</ul>
      <div className="border-t border-slate-100 pt-2 space-y-2">
        <CategoryPicker chart={chart} busy={busy} t={t} cta={t("auditView.act.categorizeAll", { count: g.count })}
                        onPick={(c) => onCategorize(ids, catToType[c], c)} />
        <div className="flex flex-wrap gap-2">
          <LiveOnly compact><button onClick={() => onAskRule(g)} disabled={busy || g.person_payment} title={t("auditView.agent.drafter")}
                  className="px-2.5 py-1 rounded-lg text-xs font-semibold border border-emerald-700 text-emerald-800 disabled:opacity-40">
            {t("auditView.act.askRule")}
          </button></LiveOnly>
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
        <div className="text-xs text-emerald-700">{t("auditView.proposal")} · {t("auditView.agent.drafter")}</div>
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
        <button onClick={() => setShowRows(!showRows)} className="text-start w-full" data-tour="history-test">
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
          <div className="flex gap-2 justify-end"><LiveOnly compact>
            <button onClick={() => setDismissing(true)} className="px-2.5 py-1 rounded-lg text-xs text-slate-600 hover:bg-slate-100">{t("agents.discard")}</button>
            <button onClick={() => onApprove(idx, p)} disabled={busy}
                    className="px-3 py-1 rounded-lg text-xs font-semibold text-white bg-emerald-700 disabled:opacity-40">{t("agents.approve")}</button>
          </LiveOnly></div>
        )}
      </div>
    </div>
  );
}
