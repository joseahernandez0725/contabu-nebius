import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { onAuthStateChanged, type User as FirebaseUser } from "firebase/auth";
import Dashboard from "./components/Dashboard";
import Transactions from "./components/Transactions";
import Sheet from "./components/Sheet";
import Upload from "./components/Upload";
import Review from "./components/Review";
import Audit from "./components/Audit";
import Pnl from "./components/Pnl";
import Settings from "./components/Settings";
import Onboarding from "./components/Onboarding";
import VerifyEmailBanner from "./components/VerifyEmailBanner";
import Login from "./components/Login";
import Sidebar, { viewLabel, type View } from "./components/Sidebar";
import DemoTour from "./components/DemoTour";
import { DEMO_MODE } from "./demo";
import { auth } from "./firebase";
import { getCompanies, getCompany, setActiveCompany, createCompany, getReviewTransactions, getAudit, confirmCheckout } from "./api";
import type { Company, Transaction } from "./types";

function App() {
  const { t } = useTranslation();
  const [view, setView] = useState<View>("dashboard");
  const [menuOpen, setMenuOpen] = useState(false); // cajón de la barra lateral (solo celular)
  const [company, setCompany] = useState<Company | null>(null);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [authError, setAuthError] = useState(false);
  const [reviewTxs, setReviewTxs] = useState<Transaction[]>([]);
  const [auditCount, setAuditCount] = useState(0); // hallazgos del AUDITOR (badge ámbar — FASE 9)
  const [billingNote, setBillingNote] = useState<string | null>(null);
  const [focusTxId, setFocusTxId] = useState<number | null>(null); // tx a abrir (desde el auditor)
  // LA HOJA (FASE 10): la consola del bookkeeper reemplaza la lista móvil simplificada.
  // Se deja un interruptor a la vista anterior mientras se termina de construir, para poder comparar.
  // Va aquí con los demás hooks a propósito: declararlo después de los `return` tempranos rompe
  // las Reglas de Hooks — en cuanto `fbUser` pasa de undefined a usuario, React lanza "Rendered
  // more hooks than during the previous render". Con VITE_DEV_UID no se ve, porque ese camino
  // arranca con sesión y nunca pasa por el return de `undefined`.
  const [useSheet, setUseSheet] = useState(true);
  // undefined = aún verificando la sesión de Firebase; null = no autenticado; User = autenticado.
  // Costura de DESARROLLO (espejo de VITE_DEV_UID en api.ts y de AUTH_DEV_MODE en el backend):
  // permite trabajar en la UI sin login de Firebase. Doble candado: la variable tiene que estar
  // puesta Y solo existe en `import.meta.env.DEV` — un build de producción no puede activarla.
  const DEV_UID = import.meta.env.DEV ? (import.meta.env.VITE_DEV_UID as string | undefined) : undefined;
  const [fbUser, setFbUser] = useState<FirebaseUser | null | undefined>(
    DEV_UID ? ({ uid: DEV_UID } as FirebaseUser) : undefined,
  );

  const reloadReview = useCallback(() => {
    getReviewTransactions().then(setReviewTxs).catch(() => setReviewTxs([]));
    // El AUDITOR corre solo (determinista, gratis) → badge ámbar sin que el dueño lo pida (FASE 9).
    getAudit().then((f) => setAuditCount(f.length)).catch(() => setAuditCount(0));
  }, []);

  // Observa el login de Firebase. Al cerrar sesión, limpia el estado de la empresa.
  useEffect(() => {
    if (DEV_UID) return;               // modo desarrollo: no se escucha a Firebase
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

  // Libro SIN sector → onboarding (el sector decide el plan de cuentas). 🔄 2-oct (FASE 13 Paso 5):
  // fuera la entrevista y su flag `round1_done`. Y el sector lo elige el BOOKKEEPER: un cliente
  // (rol CLIENT) nunca aterriza eligiendo la industria de su negocio.
  if (company && !company.sector && company.role !== "CLIENT") {
    return <Onboarding company={company} onDone={onCompanyReady} />;
  }

  // LA CÁSCARA (FASE 13): barra lateral + UN solo ancho para todas las páginas — el de Activity.
  // Antes había tres (Activity a ventana completa, Reglas a 1024 px, el resto a 672 px centrado), y
  // como el header usaba el mismo ancho variable, hasta el logo cambiaba de lugar al cambiar de
  // pestaña. Ahora todas las páginas empiezan en el mismo punto y usan el mismo espacio.
  //
  // ⚠️ La barra de arriba mide EXACTAMENTE h-14 (56 px), igual que el header de antes: la hoja
  // (`Sheet.tsx`, del área Movimientos) calcula su alto como `100vh - 132px` contando con ella.
  return (
    <div className="min-h-screen flex bg-slate-50 text-slate-900">
      <Sidebar
        view={view}
        onNavigate={setView}
        company={company}
        companies={companies}
        onSwitch={switchCompany}
        onAdd={addCompany}
        reviewCount={reviewTxs.length}
        auditCount={auditCount}
        open={menuOpen}
        onClose={() => setMenuOpen(false)}
      />

      <div className="flex-1 min-w-0 flex flex-col">
        <header className="sticky top-0 z-10 h-14 shrink-0 bg-white/80 backdrop-blur-md border-b border-slate-200 flex items-center gap-3 px-3 sm:px-4">
          <button
            onClick={() => setMenuOpen(true)}
            aria-label={t("shell.menu")}
            className="md:hidden p-1.5 -ms-1.5 rounded-lg text-slate-500 hover:bg-slate-100"
          >
            <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M4 7h16M4 12h16M4 17h16" />
            </svg>
          </button>
          <h1 className="font-semibold text-slate-900 truncate">{viewLabel(t, view)}</h1>
          {company && <span className="text-sm text-slate-400 truncate">· {company.name}</span>}
        </header>

        <main className="flex-1 w-full px-3 sm:px-4 py-3">
        <VerifyEmailBanner />
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
              <>
                {/* En la Hoja, el enlace va a la derecha de la barra de menús (sin fila propia). */}
                {!useSheet && (
                  <div className="flex justify-end mb-2">
                    <button
                      onClick={() => setUseSheet(true)}
                      className="text-[11.5px] text-slate-400 hover:text-brand-green underline underline-offset-2"
                    >
                      {t("sheet.useSheet", { defaultValue: "Use the sheet" })}
                    </button>
                  </div>
                )}
                {useSheet ? (
                  <Sheet onNavigate={setView} menuRight={
                    <button
                      onClick={() => setUseSheet(false)}
                      className="text-[11.5px] text-slate-400 hover:text-brand-green underline underline-offset-2"
                    >
                      {t("sheet.useClassic", { defaultValue: "Use the previous view" })}
                    </button>
                  } />
                ) : (
                  <Transactions
                    onAddCategory={() => setView("settings")}
                    focusTxId={focusTxId}
                    onFocusHandled={() => setFocusTxId(null)}
                  />
                )}
              </>
            )}
            {view === "audit" && (
              <div className="space-y-4">
                <Audit
                  onOpenTransaction={(id) => { setFocusTxId(id); setView("transactions"); }}
                  onCount={setAuditCount}
                  onUpgrade={() => setView("settings")}
                />
                <Review items={reviewTxs} reload={reloadReview} />
              </div>
            )}
            {view === "pnl" && <Pnl companyName={company.name} />}
            {view === "upload" && (
              // Sin `onProposeRules`: ese botón (proponedor de reglas con IA) solo aparecía si la IA
              // categorizaba algo, y desde el 27-sep no categoriza. El proponedor vuelve en Auditoría (F11).
              <Upload onDone={() => { reloadReview(); setView("dashboard"); }} />
            )}
            {view === "settings" && (
              <Settings company={company} onSaved={onCompanyReady} onDeleted={onCompanyDeleted} canDelete={companies.length > 1} />
            )}
          </div>
        )}
        </main>
      </div>

      {/* El recorrido guiado: SOLO en el despliegue de demo (docs/DEMO Y ENTREGA.md, FASE 13). */}
      {DEMO_MODE && <DemoTour />}
    </div>
  );
}

export default App;
