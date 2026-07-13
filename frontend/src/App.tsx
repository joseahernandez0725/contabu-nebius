import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { onAuthStateChanged, signOut, type User as FirebaseUser } from "firebase/auth";
import Dashboard from "./components/Dashboard";
import Transactions from "./components/Transactions";
import Upload from "./components/Upload";
import Review from "./components/Review";
import Audit from "./components/Audit";
import Interview from "./components/Interview";
import Rules from "./components/Rules";
import Pnl from "./components/Pnl";
import Settings from "./components/Settings";
import Onboarding from "./components/Onboarding";
import Login from "./components/Login";
import LanguageSwitcher from "./components/LanguageSwitcher";
import CompanySwitcher from "./components/CompanySwitcher";
import { auth } from "./firebase";
import { getCompanies, getCompany, setActiveCompany, createCompany, getReviewTransactions, getAudit, confirmCheckout } from "./api";
import type { Company, Transaction } from "./types";

type View = "dashboard" | "transactions" | "review" | "upload" | "pnl" | "rules" | "settings";

function Logo() {
  // Logo de marca real (public/icon.svg): cuadro verde con el lettermark "ContaBu".
  return <img src="/icon.svg" alt="ContaBu" className="size-9 rounded-xl shadow-sm" />;
}

const ICONS: Partial<Record<View, ReactNode>> = {
  dashboard: (
    <svg viewBox="0 0 24 24" className="size-6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" />
      <rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" />
    </svg>
  ),
  transactions: (
    <svg viewBox="0 0 24 24" className="size-6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <line x1="8" y1="7" x2="20" y2="7" /><line x1="8" y1="12" x2="20" y2="12" /><line x1="8" y1="17" x2="20" y2="17" />
      <circle cx="4" cy="7" r="1" /><circle cx="4" cy="12" r="1" /><circle cx="4" cy="17" r="1" />
    </svg>
  ),
  review: (
    <svg viewBox="0 0 24 24" className="size-6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="5" y="4" width="14" height="17" rx="2" /><path d="M9 4h6v3H9z" /><path d="M9 13l2 2 4-4" />
    </svg>
  ),
  upload: (
    <svg viewBox="0 0 24 24" className="size-6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 16V4M7 9l5-5 5 5" /><path d="M4 17v2a1 1 0 001 1h14a1 1 0 001-1v-2" />
    </svg>
  ),
  rules: (
    <svg viewBox="0 0 24 24" className="size-6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 6h11M9 12h11M9 18h11" /><path d="M4 6l1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2" />
    </svg>
  ),
  pnl: (
    <svg viewBox="0 0 24 24" className="size-6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 19V5M4 19h16" /><path d="M8 16v-5M12 16V8M16 16v-3" />
    </svg>
  ),
};

function App() {
  const { t } = useTranslation();
  const [view, setView] = useState<View>("dashboard");
  const [company, setCompany] = useState<Company | null>(null);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [authError, setAuthError] = useState(false);
  const [reviewTxs, setReviewTxs] = useState<Transaction[]>([]);
  const [auditCount, setAuditCount] = useState(0); // hallazgos del AUDITOR (badge ámbar — FASE 9)
  const [billingNote, setBillingNote] = useState<string | null>(null);
  const [focusTxId, setFocusTxId] = useState<number | null>(null); // tx a abrir (desde el auditor)
  const [autoPropose, setAutoPropose] = useState(false); // "Crear mis reglas" tras la subida → Reglas genera solo
  // undefined = aún verificando la sesión de Firebase; null = no autenticado; User = autenticado.
  const [fbUser, setFbUser] = useState<FirebaseUser | null | undefined>(undefined);

  const reloadReview = useCallback(() => {
    getReviewTransactions().then(setReviewTxs).catch(() => setReviewTxs([]));
    // El AUDITOR corre solo (determinista, gratis) → badge ámbar sin que el dueño lo pida (FASE 9).
    getAudit().then((f) => setAuditCount(f.length)).catch(() => setAuditCount(0));
  }, []);

  // Observa el login de Firebase. Al cerrar sesión, limpia el estado de la empresa.
  useEffect(() => {
    return onAuthStateChanged(auth, (u) => {
      setFbUser(u);
      if (!u) { setCompany(null); setCompanies([]); setReviewTxs([]); }
    });
  }, []);

  // Cambia la empresa activa (todas las llamadas usan la activa). El `key` del main fuerza recarga.
  const switchCompany = (c: Company) => {
    setActiveCompany(c.id);
    setCompany(c);
    setView("dashboard");
    reloadReview();
  };

  // Crea una empresa nueva bajo la cuenta → se vuelve activa (sin sector → onboarding).
  const addCompany = async () => {
    const c = await createCompany();
    setActiveCompany(c.id);
    setCompanies((prev) => [...prev, c]);
    setCompany(c);
    setView("dashboard");
    setReviewTxs([]);
  };

  // Tras el onboarding o renombrar: refleja nombre/sector en la empresa activa y en la lista.
  const onCompanyReady = (c: Company) => {
    setCompany(c);
    setCompanies((prev) => (prev.some((p) => p.id === c.id) ? prev.map((p) => (p.id === c.id ? c : p)) : [...prev, c]));
  };

  // Tras eliminar la empresa activa: quitarla de la lista y cambiar a otra (el backend impide borrar la última).
  const onCompanyDeleted = () => {
    if (!company) return;
    const remaining = companies.filter((c) => c.id !== company.id);
    setCompanies(remaining);
    if (remaining.length > 0) switchCompany(remaining[0]);
  };

  // Con sesión, inicia la app (getMe fija la empresa activa) y carga las revisiones.
  useEffect(() => {
    if (!fbUser) return;
    setAuthError(false);
    getCompanies()
      .then((cs) => { setCompanies(cs); setCompany(cs[0] ?? null); reloadReview(); })
      .catch(() => setAuthError(true));
  }, [fbUser, reloadReview]);

  // Vuelta de Stripe Checkout (?billing=success&session_id=…): confirma el pago y refresca la empresa.
  useEffect(() => {
    if (!company) return;
    const params = new URLSearchParams(window.location.search);
    const billing = params.get("billing");
    if (!billing) return;
    window.history.replaceState({}, "", window.location.pathname); // limpia la URL (evita re-confirmar)
    if (billing === "success" && params.get("session_id")) {
      confirmCheckout(params.get("session_id")!)
        .then((r) => {
          setBillingNote(r.ok
            ? t("billing.success", { defaultValue: "Subscription active — {{n}} transactions/month. 🎉", n: r.tx_quota ?? 0 })
            : t("billing.pending", { defaultValue: "Payment received; finalizing your subscription…" }));
          getCompany().then(setCompany).catch(() => {});
        })
        .catch(() => setBillingNote(t("billing.error", { defaultValue: "Couldn't confirm the payment." })));
    } else if (billing === "cancel") {
      setBillingNote(t("billing.canceled", { defaultValue: "Checkout canceled — no charge made." }));
    }
  }, [company, t]);

  const views: View[] = ["dashboard", "transactions", "review", "pnl", "rules"];

  // Verificando sesión de Firebase.
  if (fbUser === undefined) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50">
        <span className="loading loading-spinner loading-lg text-brand-green" />
      </div>
    );
  }
  // Sin sesión → pantalla de acceso.
  if (fbUser === null) return <Login />;

  // Empresa SIN sector o con la entrevista a medias → onboarding. El gate es `round1_done` (no
  // solo el sector): el sector se guarda ANTES de las preguntas, y un refresh a media entrevista
  // la saltaba en silencio. Con el flag, el onboarding reanuda donde iba.
  if (company && (!company.sector || !company.profile?.round1_done)) {
    return <Onboarding company={company} onDone={onCompanyReady} />;
  }

  // Vista de escritorio (§5.B): las herramientas de operador (movimientos, reglas) usan MÁS ancho en
  // pantallas grandes; el resto del flujo del dueño queda angosto (mobile-first).
  const wideView = view === "transactions" || view === "rules";
  const contentWidth = wideView ? "max-w-2xl lg:max-w-5xl" : "max-w-2xl";

  return (
    <div className="min-h-screen flex flex-col bg-slate-50 text-slate-900">
      <header className="sticky top-0 z-10 bg-white/80 backdrop-blur-md border-b border-slate-200">
        <div className={`${contentWidth} mx-auto px-4 h-14 flex items-center gap-3`}>
          <Logo />
          <div className="flex-1 leading-tight min-w-0">
            <div className="font-bold tracking-tight">
              <span className="text-brand-amber">Conta</span>
              <span className="text-brand-green">Bu</span>
            </div>
            {company && companies.length > 0 ? (
              <CompanySwitcher companies={companies} activeId={company.id} onSwitch={switchCompany} onAdd={addCompany} />
            ) : (
              <div className="text-xs text-slate-500 truncate">{company?.name ?? "…"}</div>
            )}
          </div>
          <LanguageSwitcher />
          <button
            onClick={() => setView("settings")}
            aria-label={t("settings.title")}
            className={`p-1.5 rounded-lg transition-colors ${
              view === "settings" ? "text-brand-green" : "text-slate-400 hover:text-slate-600"
            }`}
          >
            <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
          </button>
          <button
            onClick={() => signOut(auth)}
            aria-label={t("login.logout")}
            title={t("login.logout")}
            className="p-1.5 rounded-lg text-slate-400 hover:text-rose-500 transition-colors"
          >
            <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><path d="M16 17l5-5-5-5" /><path d="M21 12H9" />
            </svg>
          </button>
        </div>
      </header>

      <main className={`flex-1 w-full ${contentWidth} mx-auto px-4 py-5 pb-24`}>
        {!company ? (
          <div className="flex justify-center py-16">
            {authError ? (
              <span className="text-sm text-rose-600">{t("auth.error")}</span>
            ) : (
              <span className="loading loading-spinner loading-lg text-brand-green" />
            )}
          </div>
        ) : (
          <div key={company.id}>
            {billingNote && (
              <div className="mb-4 flex items-center justify-between gap-3 text-sm text-brand-green bg-brand-green/10 rounded-xl px-4 py-2.5">
                <span>{billingNote}</span>
                <button onClick={() => setBillingNote(null)} className="text-brand-green/70 hover:text-brand-green shrink-0" aria-label="dismiss">✕</button>
              </div>
            )}
            {view === "dashboard" && (
              <Dashboard onNavigate={setView} />
            )}
            {view === "transactions" && (
              <Transactions
                onAddCategory={() => setView("settings")}
                focusTxId={focusTxId}
                onFocusHandled={() => setFocusTxId(null)}
              />
            )}
            {view === "review" && (
              <div className="space-y-4">
                {/* ENTREVISTA ronda 2 (FASE 9): preguntas del agente ancladas en los datos. */}
                <Interview onApplied={reloadReview} />
                <Audit
                  onOpenTransaction={(id) => { setFocusTxId(id); setView("transactions"); }}
                  onCount={setAuditCount}
                  onUpgrade={() => setView("settings")}
                />
                <Review items={reviewTxs} reload={reloadReview} />
              </div>
            )}
            {view === "pnl" && <Pnl companyName={company.name} />}
            {view === "rules" && (
              <Rules autoPropose={autoPropose} onAutoProposeDone={() => setAutoPropose(false)} />
            )}
            {view === "upload" && (
              <Upload
                onDone={() => { reloadReview(); setView("dashboard"); }}
                onProposeRules={() => { reloadReview(); setAutoPropose(true); setView("rules"); }}
              />
            )}
            {view === "settings" && (
              <Settings company={company} onSaved={onCompanyReady} onDeleted={onCompanyDeleted} canDelete={companies.length > 1} />
            )}
          </div>
        )}
      </main>

      <nav className="fixed bottom-0 inset-x-0 bg-white/90 backdrop-blur-md border-t border-slate-200">
        <div className="max-w-2xl mx-auto grid grid-cols-5">
          {views.map((id) => {
            const active = view === id;
            const count = id === "review" ? reviewTxs.length : 0;
            const audits = id === "review" ? auditCount : 0; // hallazgos del auditor (ámbar)
            return (
              <button
                key={id}
                onClick={() => setView(id)}
                className={`flex flex-col items-center gap-1 py-2.5 text-[11px] transition-colors ${
                  active ? "text-brand-green font-semibold" : "text-slate-400 hover:text-slate-600"
                }`}
              >
                <div className="relative">
                  {ICONS[id]}
                  {count > 0 && (
                    <span className="absolute -top-1.5 -end-2 bg-rose-500 text-white text-[9px] leading-none rounded-full px-1 py-0.5 min-w-4 text-center">
                      {count}
                    </span>
                  )}
                  {/* Badge ÁMBAR del AUDITOR: hallazgos del agente (aparte de las revisiones rojas). */}
                  {audits > 0 && (
                    <span className="absolute -top-1.5 -start-2 bg-amber-500 text-white text-[9px] leading-none rounded-full px-1 py-0.5 min-w-4 text-center">
                      {audits}
                    </span>
                  )}
                </div>
                {t(`nav.${id}`)}
              </button>
            );
          })}
        </div>
      </nav>
    </div>
  );
}

export default App;
