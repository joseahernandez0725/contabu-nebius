import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { getAuditSummary, getBankConnections, getCompany, getIncomeStatement, getTransactions, money, type AuditBoard, type BankConnection } from "../api";
import { DEMO_MODE, openDemoTour } from "../demo";
import type { IncomeStatement as IS, Transaction } from "../types";
import IncomeStatement from "./IncomeStatement";
import NotesCard from "./NotesCard";
import { BTN_PRIMARY, BTN_SECONDARY, DOT, PageHeader, TONE, agoText, secsSince } from "./PageKit";

type View = "transactions" | "notes" | "audit" | "upload" | "pnl" | "settings";

// Un banco que no se actualiza hace más de esto sale en "lo que necesita atención".
const STALE_DAYS = 3;

// La fecha LOCAL como la espera el backend (YYYY-MM-DD). `toISOString()` daría la de UTC: de noche en
// California ya sería mañana.
function isoDay(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// SOLO EN EL DEMO (18-oct): qué es ContaBu y qué libro se está viendo, para quien llega sin contexto (un
// juez). No repite el recorrido: lo ofrece. Se puede ocultar; se recuerda en SU navegador (si no deja
// guardar, vuelve a salir — no pasa nada).
const KEY_INTRO_HIDDEN = "contabu.demoIntro.hidden.v1";

function DemoIntro({ onNavigate, book }: { onNavigate: (v: View) => void; book: string | null }) {
  const { t } = useTranslation();
  const [hidden, setHidden] = useState<boolean>(() => {
    try { return localStorage.getItem(KEY_INTRO_HIDDEN) === "1"; } catch { return false; }
  });
  if (hidden) return null;
  const hide = () => {
    setHidden(true);
    try { localStorage.setItem(KEY_INTRO_HIDDEN, "1"); } catch { /* modo privado */ }
  };
  const tries: { key: string; go: View; text: string }[] = [
    { key: "try1", go: "audit", text: t("page.summary.demo.try1", { audit: t("nav.audit"), review: t("auditView.reviewBook") }) },
    { key: "try2", go: "transactions", text: t("page.summary.demo.try2", { transactions: t("nav.transactions") }) },
    { key: "try3", go: "upload", text: t("page.summary.demo.try3", { banks: t("nav.upload") }) },
  ];
  return (
    <section className="bg-white rounded-xl border border-emerald-200 px-4 py-3 text-sm">
      <div className="flex items-start gap-3">
        <div className="min-w-0 space-y-1">
          <h2 className="font-semibold text-slate-900">{t("page.summary.demo.title")}</h2>
          <p className="text-slate-600">{t("page.summary.demo.lead")}</p>
          <p className="text-slate-600">
            {t("page.summary.demo.viewing")} <b className="text-slate-900">{book ?? "…"}</b>, {t("page.summary.demo.bookNote")}
          </p>
        </div>
        <div className="ms-auto flex items-center gap-1.5 shrink-0">
          <button onClick={openDemoTour} className={BTN_SECONDARY}>{t("page.summary.demo.tour")}</button>
          <button onClick={hide} aria-label={t("page.summary.demo.hide")} title={t("page.summary.demo.hide")}
                  className="p-1 rounded text-slate-400 hover:text-slate-700">✕</button>
        </div>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className="text-xs font-semibold uppercase tracking-wide text-slate-400">{t("page.summary.demo.tryLabel")}</span>
        {tries.map((x, i) => (
          <button key={x.key} onClick={() => onNavigate(x.go)} className="text-start text-emerald-800 hover:underline">
            {i + 1}. {x.text} →
          </button>
        ))}
      </div>
    </section>
  );
}

// Una cifra de la portada: la etiqueta arriba, el monto abajo a la derecha (tabular, como en el P&L).
function Kpi({ label, value, sub, tone = TONE.neutral, amber, onClick }: {
  label: string; value: string; sub?: ReactNode; tone?: string; amber?: boolean; onClick?: () => void;
}) {
  const box = `block w-full text-start rounded-xl border px-4 py-3 ${amber ? "border-amber-200 bg-amber-50/60" : "border-slate-200 bg-white"}`;
  const body = (
    <>
      <div className={`text-xs font-medium ${amber ? "text-amber-800" : "text-slate-500"}`}>{label}</div>
      <div className={`mt-1 text-xl sm:text-2xl font-semibold tabular-nums text-end ${tone}`}>{value}</div>
      <div className={`mt-0.5 min-h-4 text-xs text-end ${amber ? "text-amber-700" : "text-slate-400"}`}>{sub}</div>
    </>
  );
  return onClick
    ? <button onClick={onClick} className={`${box} hover:border-amber-300`}>{body}</button>
    : <div className={box}>{body}</div>;
}

// EL RESUMEN DEL LIBRO = LA PORTADA (13-oct, Firm: diseño de página compartido — `PageKit.tsx`; rehecho el
// 18-oct en dos rondas de diseño).
// Arriba, el libro y el período con 4 CIFRAS (ronda 2: Jose las quiere a la vista aunque el P&L las detalle —
// aquí cede la regla de "no repetir arriba"). Debajo, dos mitades: el P&L DEL AÑO EN CURSO (1-ene → hoy), con
// el mismo componente que se le entrega al cliente (`IncomeStatement`, de Entregables), y LO QUE NECESITA
// ATENCIÓN — cada renglón lleva a la pantalla donde se resuelve — con las Notas del libro debajo. Los números
// salen de SQL (`/income-statement`) y del auditor (`/audit/summary`); aquí no se calcula nada nuevo.
export default function Dashboard({ onNavigate, onOpenPending }: {
  onNavigate: (v: View) => void;
  onOpenPending?: () => void;   // Pendientes → la Hoja filtrada, en la primera Pendiente (14-oct)
}) {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language || "en").split("-")[0];
  const [today] = useState(() => new Date());
  const year = today.getFullYear();
  const start = `${year}-01-01`;
  const end = isoDay(today);
  const [ytd, setYtd] = useState<IS | null>(null);
  const [all, setAll] = useState<IS | null>(null);
  const [txs, setTxs] = useState<Transaction[] | null>(null);
  const [audit, setAudit] = useState<AuditBoard["stats"] | null>(null);
  const [banks, setBanks] = useState<BankConnection[]>([]);
  const [book, setBook] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getCompany().then((c) => setBook(c.name)).catch(() => setBook(null));
    const fail = (e: unknown) => setError(e instanceof Error ? e.message : "Error");
    getTransactions().then(setTxs).catch(fail);
    getIncomeStatement(start, end).then(setYtd).catch(fail);
    // Todo el libro: solo para contar lo Pendiente de CUALQUIER fecha (es trabajo por hacer, y el renglón
    // abre la Hoja con todas las Pendientes). Ninguna cifra de esta llamada se pinta como resultado.
    getIncomeStatement().then(setAll).catch(fail);
    getAuditSummary().then(setAudit).catch(() => setAudit(null));
    getBankConnections().then((r) => setBanks(r.config.enabled ? r.connections : [])).catch(() => setBanks([]));
  }, [start, end]);

  const intro = DEMO_MODE ? <DemoIntro onNavigate={onNavigate} book={book} /> : null;

  if (error) return <div className="alert alert-error">{error}</div>;
  if (!ytd || !all || !txs)
    return (
      <div className="flex justify-center py-16">
        <span className="loading loading-spinner loading-lg text-brand-green" />
      </div>
    );

  if (txs.length === 0) {
    return (
      <div className="space-y-3">
        {intro}
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
      </div>
    );
  }

  // FASE 12: lo Pendiente (sin `account_type`) no entra en ninguna suma del P&L — el P&L lo declara.
  const u = ytd.uncategorized;
  const pending = u?.count ?? 0;
  const pendingAll = all.uncategorized?.count ?? pending;
  // Un libro con movimientos pero NINGUNO en el año: un P&L en ceros se leería como equilibrio. Se dice.
  const noneThisYear = !txs.some((tx) => tx.date >= start && tx.date <= end);
  // Hay movimientos este año, pero ninguno tiene sección todavía: el resultado es 0 por falta de datos.
  const nothingClassified = pending > 0 && ytd.revenue === 0 && ytd.cogs === 0 && ytd.operating_expenses === 0
    && ytd.other_expense === 0 && ytd.other_income === 0;
  // Gastos de la portada = las tres secciones que restan en el P&L (lo mismo que hacía el Dashboard viejo).
  const expenses = ytd.cogs + ytd.operating_expenses + ytd.other_expense;

  // LO QUE NECESITA ATENCIÓN, de lo más grave a lo menos. Cada renglón lleva a donde se resuelve.
  const down = banks.filter((b) => b.status === "disconnected");
  const stale = banks.filter((b) => b.status !== "disconnected" &&
    (!b.last_synced_at || secsSince(b.last_synced_at) > STALE_DAYS * 86400));
  const items: { key: string; dot: string; title: string; sub?: string; go: View; count?: number }[] = [];
  if (down.length) items.push({ key: "down", dot: DOT.problem, go: "upload", count: down.length,
    title: t("page.summary.banksDown", { count: down.length }), sub: down.map((b) => b.institution_name ?? t("bank.unnamed")).join(" · ") });
  if (audit && audit.problems > 0) items.push({ key: "problems", dot: DOT.problem, go: "audit", count: audit.problems,
    title: t("page.summary.problems", { count: audit.problems }), sub: t("page.summary.problemsSub") });
  // Cuántas y cuánto salió ya lo dice el P&L de al lado; aquí solo se avisa si hay Pendientes FUERA del período.
  if (pendingAll > 0) items.push({ key: "pending", dot: DOT.pending, go: "transactions", count: pendingAll,
    title: t("page.summary.pending", { count: pendingAll }),
    sub: pendingAll > pending ? t("page.summary.pendingOutside", { count: pendingAll - pending }) : undefined });
  if (audit && audit.to_review - audit.problems > 0) items.push({ key: "review", dot: DOT.review, go: "audit",
    count: audit.to_review - audit.problems, title: t("page.summary.toReview", { count: audit.to_review - audit.problems }),
    sub: audit.reviewed_seconds_ago != null ? t("auditView.reviewed", { when: agoText(lang, audit.reviewed_seconds_ago) }) : t("auditView.neverReviewed") });
  if (stale.length) items.push({ key: "stale", dot: DOT.info, go: "upload", count: stale.length,
    title: t("page.summary.stale", { count: stale.length }),
    sub: stale.map((b) => `${b.institution_name ?? t("bank.unnamed")} · ${b.last_synced_at ? agoText(lang, secsSince(b.last_synced_at)) : t("bank.neverSynced")}`).join(" · ") });

  return (
    <div className="space-y-3">
      {intro}

      {/* 1. LA LÍNEA DE ESTADO: el libro y el período de TODO lo que sigue */}
      <PageHeader
        meta={<span className="text-sm text-slate-700">
          {book && <><b className="font-semibold text-slate-900">{book}</b> · </>}{t("page.summary.ytd", { year })}
        </span>}
        primary={<button onClick={() => onNavigate("transactions")} className={BTN_PRIMARY}>{t("page.summary.openBook")}</button>}
        more={[
          { label: t("nav.audit"), onClick: () => onNavigate("audit") },
          { label: t("nav.upload"), onClick: () => onNavigate("upload") },
        ]}
        moreLabel={t("page.more")}
      />

      {/* 2. LAS 4 CIFRAS, del mismo período que el P&L. Un año sin movimientos no pinta ceros (abajo se dice). */}
      {!noneThisYear && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <Kpi label={t("page.summary.kpi.revenue")} value={money(ytd.revenue)} />
          <Kpi label={t("page.summary.kpi.expenses")} value={money(expenses)} />
          <Kpi label={t("page.summary.kpi.netIncome")} value={money(ytd.net_income)}
               tone={ytd.net_income < 0 ? TONE.problem : TONE.good}
               sub={pending > 0 ? <span className="text-amber-700">{t("pnl.beforePending")}</span> : undefined} />
          <Kpi label={t("page.summary.kpi.pending")} value={String(pending)} tone={TONE.pending} amber
               sub={pending > 0 ? t("page.summary.kpi.pendingSub") : undefined}
               onClick={pending > 0 ? () => (onOpenPending ? onOpenPending() : onNavigate("transactions")) : undefined} />
        </div>
      )}

      <div className="grid gap-3 lg:grid-cols-2 items-start">
        {/* 3. EL P&L DEL AÑO EN CURSO — lo que al final recibe el cliente, en su modo COMPACTO (Entregables,
            18-oct): secciones plegadas a su total y lo Pendiente siempre a la vista. */}
        <div className="relative bg-white rounded-xl border border-slate-200 p-5">
          <button onClick={() => onNavigate("pnl")} className="absolute top-5 end-5 text-xs text-emerald-800 font-medium hover:underline">
            {t("dashboard.seePnl", { defaultValue: "See full P&L →" })}
          </button>
          {noneThisYear ? (
            <div className="py-6">
              <h2 className="font-semibold text-slate-900">{t("page.summary.noneThisYear", { year })}</h2>
              <p className="text-sm text-slate-500 mt-1">{t("page.summary.noneThisYearSub")}</p>
            </div>
          ) : (
            <>
              {nothingClassified && <p className="text-xs text-amber-700 mb-2 pe-28">{t("pnl.nothingClassified")}</p>}
              <IncomeStatement data={ytd} bare compact />
            </>
          )}
        </div>

        <div className="space-y-3">
          {/* 4. LO QUE NECESITA ATENCIÓN */}
          <section className="bg-white rounded-xl border border-slate-200 overflow-hidden">
            <h2 className="px-5 pt-4 pb-2 text-sm font-semibold text-slate-900">{t("page.summary.attention")}</h2>
            {items.length === 0 ? (
              <p className="px-5 pb-5 text-sm text-slate-400">{t("page.summary.allClear")}</p>
            ) : (
              <ul className="divide-y divide-slate-100 border-t border-slate-100">
                {items.map((it) => (
                  <li key={it.key}>
                    <button onClick={() => (it.key === "pending" && onOpenPending ? onOpenPending() : onNavigate(it.go))}
                            className="w-full text-start px-5 py-3 flex items-center gap-3 hover:bg-slate-50">
                      <span className={`w-2 h-2 rounded-full shrink-0 ${it.dot}`} />
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm text-slate-900">{it.title}</span>
                        {it.sub && <span className="block text-xs text-slate-500 mt-0.5 truncate">{it.sub}</span>}
                      </span>
                      <span className="text-sm text-slate-400 shrink-0">→</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* NOTAS DEL LIBRO: aquí va <NotesCard/> de Auditoría/IA */}
          <NotesCard onOpen={() => onNavigate("notes")} />
        </div>
      </div>
    </div>
  );
}
