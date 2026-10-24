import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { onAuthStateChanged, type User as FirebaseUser } from "firebase/auth";
import Dashboard from "./components/Dashboard";
import Sheet, { type SheetFocus } from "./components/Sheet";
import Upload from "./components/Upload";
import AuditWorkspace from "./components/AuditWorkspace";
import NotesPage from "./components/NotesPage";
import Pnl from "./components/Pnl";
import Settings from "./components/Settings";
import Onboarding from "./components/Onboarding";
import VerifyEmailBanner from "./components/VerifyEmailBanner";
import Login from "./components/Login";
import Sidebar, { viewLabel, type View } from "./components/Sidebar";
import DemoTour from "./components/DemoTour";
import { DEMO_MODE, ENTER_CODE_EVENT, openDemoGuide, setTouring, type DemoDoor } from "./demo";
import { AccessCodeScreen, TourBanner, TourMissing } from "./components/DemoDoors";
import DemoGuide from "./components/DemoGuide";
import { auth } from "./firebase";
import { getCompanies, getCompany, setActiveCompany, createCompany, getAuditSummary, confirmCheckout, demoDoor } from "./api";
import type { AuditBoard } from "./api";
import type { Company } from "./types";

function App() {
  const { t } = useTranslation();
  const [view, setView] = useState<View>("dashboard");
  const [menuOpen, setMenuOpen] = useState(false); // cajón de la barra lateral (solo celular)
  const [company, setCompany] = useState<Company | null>(null);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [authError, setAuthError] = useState(false);
  // Contadores de Auditoría en la barra lateral (FASE 11, 8-oct): salen del MISMO encabezado de estado
  // que muestra la pestaña (rojo = problemas, ámbar = por revisar), no de dos listas distintas.
  const [problemCount, setProblemCount] = useState(0);
  const [auditCount, setAuditCount] = useState(0); // hallazgos del AUDITOR (badge ámbar — FASE 9)
  const [billingNote, setBillingNote] = useState<string | null>(null);
  // La misma fila, para la Hoja (14-oct): Auditoría y el Resumen llevan a la transacción EXACTA.
  const [sheetFocus, setSheetFocus] = useState<SheetFocus | null>(null);
  const openInSheet = (f: Omit<SheetFocus, "n">) => {
    setSheetFocus({ ...f, n: Date.now() });
    setView("transactions");
  };
  // Notas → Auditoría (18-oct, ronda de diseño 2): "Proponer reglas para esta nota" corre el ④ en Auditoría (ahí
  // viven las propuestas). `n` cambia cada vez.
  const [auditProposeNote, setAuditProposeNote] = useState<{ id: number; n: number } | null>(null);
  // undefined = aún verificando la sesión de Firebase; null = no autenticado; User = autenticado.
  // Costura de DESARROLLO (espejo de VITE_DEV_UID en api.ts y de AUTH_DEV_MODE en el backend):
  // permite trabajar en la UI sin login de Firebase. Doble candado: la variable tiene que estar
  // puesta Y solo existe en `import.meta.env.DEV` — un build de producción no puede activarla.
  const DEV_UID = import.meta.env.DEV ? (import.meta.env.VITE_DEV_UID as string | undefined) : undefined;
  const [fbUser, setFbUser] = useState<FirebaseUser | null | undefined>(
    DEV_UID ? ({ uid: DEV_UID } as FirebaseUser) : undefined,
  );
  // DEMO — las dos puertas (FASE 13, 23-oct): por cuál entró (lo dice `/api/me`), si una cuenta de Google sin
  // código eligió mirar la visita mientras tanto, y `session` para volver a pedir `/api/me` tras canjear el código.
  const [door, setDoor] = useState<DemoDoor | null>(null);
  const [browsingTour, setBrowsingTour] = useState(false);
  const [session, setSession] = useState(0);
  // El recorrido guiado (23-oct): en su parada 2 abre la barra lateral (escritorio) o el cajón (celular).
  const [guideSidebar, setGuideSidebar] = useState(false);
  const onGuideSidebar = useCallback((open: boolean) => {
    if (window.innerWidth < 768) setMenuOpen(open);
    else setGuideSidebar(open);
  }, []);
  useEffect(() => {
    const enterCode = () => setBrowsingTour(false);
    window.addEventListener(ENTER_CODE_EVENT, enterCode);
    return () => window.removeEventListener(ENTER_CODE_EVENT, enterCode);
  }, []);

  const reloadReview = useCallback(() => {
    // El AUDITOR corre solo (determinista, gratis) → badges sin que el bookkeeper lo pida (FASE 9/11).
    getAuditSummary()
      .then((s) => { setAuditCount(s.to_review); setProblemCount(s.problems); })
      .catch(() => { setAuditCount(0); setProblemCount(0); });
  }, []);

  // Observa el login de Firebase. Al cerrar sesión, limpia el estado de la empresa.
  useEffect(() => {
    if (DEV_UID) return;               // modo desarrollo: no se escucha a Firebase
    return onAuthStateChanged(auth, (u) => {
      setFbUser(u);
      setDoor(null); setBrowsingTour(false);
      if (!u) { setCompany(null); setCompanies([]); setProblemCount(0); setAuditCount(0); }
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
    setProblemCount(0); setAuditCount(0);
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
      .then((cs) => {
        // En el demo la misma pestaña puede cambiar de oficina (visita → la suya, tras el código): el libro activo
        // siempre es el primero de la lista nueva (Rivera HVAC), no el que quedó de antes.
        if (DEMO_MODE && cs[0]) setActiveCompany(cs[0].id);
        setDoor(demoDoor());
        setCompanies(cs); setCompany(cs[0] ?? null); reloadReview();
      })
      .catch(() => setAuthError(true));
  }, [fbUser, reloadReview, session]);

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

  // DEMO: Google sin código → el código (o la visita, si la eligió); la visita sin crear todavía → un aviso.
  const touring = DEMO_MODE && (door === "tour" || (door === "needs_code" && browsingTour));
  setTouring(touring);   // lo leen `<LiveOnly>` y `api.ts` (ver demo.ts); antes de pintar cualquier página
  if (DEMO_MODE && door === "needs_code" && !browsingTour) {
    return <AccessCodeScreen email={fbUser.email} onDone={() => setSession((n) => n + 1)}
                             onBrowseTour={() => setBrowsingTour(true)} />;
  }
  if (DEMO_MODE && door === "tour_missing") return <TourMissing />;

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
        reviewCount={problemCount}
        auditCount={auditCount}
        open={menuOpen}
        onClose={() => setMenuOpen(false)}
        forceExpand={guideSidebar}
      />

      <div className="flex-1 min-w-0 flex flex-col">
        <header className="sticky top-0 z-10 h-14 shrink-0 bg-white/80 backdrop-blur-md border-b border-slate-200 flex items-center gap-3 px-3 sm:px-4">
          <button
            onClick={() => setMenuOpen(true)}
            aria-label={t("shell.menu")}
            // 22-oct: el riel angosto vive en TODAS las páginas de escritorio (antes solo fuera de
            // Movimientos); el ☰ vuelve a ser solo para el cajón de celular.
            className="p-1.5 -ms-1.5 rounded-lg text-slate-500 hover:bg-slate-100 md:hidden"
          >
            <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M4 7h16M4 12h16M4 17h16" />
            </svg>
          </button>
          <h1 className="font-semibold text-slate-900 truncate">{viewLabel(t, view)}</h1>
          {company && <span className={`text-sm text-slate-400 truncate ${DEMO_MODE ? "hidden sm:inline" : ""}`}>· {company.name}</span>}
          {DEMO_MODE && company && (
            <div className="ms-auto min-w-0 flex items-center gap-2">
              <button onClick={openDemoGuide} className="shrink-0 rounded-full border border-slate-200 px-3 py-1 text-xs font-semibold text-slate-700 hover:bg-slate-50">
                {t("demoGuide.open")}
              </button>
              {touring && <TourBanner />}
            </div>
          )}
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
              <Dashboard onNavigate={setView} onOpenPending={() => openInSheet({ id: null, onlyPending: true })} />
            )}
            {view === "transactions" && (
              <Sheet onNavigate={setView} focusTx={sheetFocus} onFocusTxDone={() => setSheetFocus(null)} />
            )}
            {view === "notes" && (
              // FASE 11 (18-oct): las notas del libro, con página propia (`NotesPage`).
              <NotesPage
                onOpenSheet={(txId) => openInSheet({ id: txId })}
                onProposeRules={(id) => { setAuditProposeNote({ id, n: Date.now() }); setView("audit"); }}
              />
            )}
            {view === "audit" && (
              // FASE 11 (8-oct): UNA herramienta — el equipo de IA, la cola y el detalle (`AuditWorkspace`).
              <AuditWorkspace
                onOpenSheet={(txId) => (txId != null ? openInSheet({ id: txId }) : setView("transactions"))}
                onStats={(s: AuditBoard["stats"]) => { setAuditCount(s.to_review); setProblemCount(s.problems); }}
                onOpenNotes={() => setView("notes")}
                proposeNote={auditProposeNote}
                onProposeNoteDone={() => setAuditProposeNote(null)}
              />
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

      {/* El recorrido guiado (23-oct): pantalla de inicio + 7 paradas, en las dos puertas (`key`: uno por puerta).
          La lista de antes (`DemoTour`) queda solo en el demo en vivo, como "Cosas para probar". */}
      {DEMO_MODE && company && (
        <DemoGuide key={touring ? "tour" : "live"} door={touring ? "tour" : "live"} onNavigate={setView} onSidebar={onGuideSidebar} />
      )}
      {DEMO_MODE && !touring && <DemoTour />}
    </div>
  );
}

export default App;
