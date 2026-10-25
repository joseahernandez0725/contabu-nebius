import { useCallback, useEffect, useLayoutEffect, useReducer, useRef, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { getDemoIntro, getGuidePick, type DemoIntro } from "../api";
import { goLive, OPEN_GUIDE_EVENT } from "../demo";
import type { View } from "./Sidebar";

// EL RECORRIDO GUIADO DEL DEMO (FASE 13, área Firm · 23-oct; aprobado por Jose sobre dos dibujos). Una pantalla de
// inicio y 15 paradas: la pantalla se oscurece al 50 %, un recorte claro y redondeado sobre lo que hay que tocar, y
// al lado una burbuja (una barra de progreso sin números, título, 1–2 frases, Skip / Next). Se avanza tocando lo iluminado o con Next; Esc
// o Skip lo cierran. En el celular, la burbuja va abajo (o arriba, si lo iluminado queda abajo). Cada parada abre
// sola la página que le toca. Las marcas que ilumina son `data-tour="…"` en los componentes (PageKit, Sidebar,
// Dashboard, AuditWorkspace, Sheet, RulesSheet, Upload, NotesPage, TransactionPanel).
//
// 🔄 25-oct (2), LA GUÍA RÁPIDA (Jose): de 8 a 14 paradas (cambió el orden: el P&L pasa casi al final, es el
// resultado de todo el camino). Las paradas con controles adentro (`SHIELDED`) llevan un tapón: la visita es de
// solo lectura también por dentro. La fila de la parada 11 sale de `/demo/guide` (`transaction_id`) y se abre
// con el mismo "ir a la fila" de Auditoría (`onOpenRow` → `openInSheet`), sin tocar la lógica de la Hoja.
//
// 🔄 25-oct (Jose, sobre el demo desplegado): SOLO vive en la VISITA GUIADA — se montaba también en el demo en
// vivo con textos propios por puerta; ahí no debe aparecer nada de esto (`App.tsx` ya no lo monta fuera de la
// visita). Se simplificó: sin prop `door`, sin textos "tour"/"live" separados. Y "visto" se guarda por SESIÓN
// (`sessionStorage`, no `localStorage`): antes, saltarlo una vez lo apagaba para siempre en ese navegador; ahora
// salir de la visita y volver a entrar lo reinicia.

type Pick = Awaited<ReturnType<typeof getGuidePick>>;
// Las 15 paradas, en orden (la guía rápida, 25-oct). La clave es el tema: los textos viven en `demoGuide.<clave>`.
// 🔄 25-oct (4, Jose): el equipo de agentes (`team`) va justo después del banco, antes de la hoja.
type StopKey = "who" | "clients" | "nav" | "bank" | "team" | "book" | "rules" | "newrule" | "notes" | "finding" | "proposal"
  | "row" | "agents" | "pnl" | "live";
const STOPS: StopKey[] = ["who", "clients", "nav", "bank", "team", "book", "rules", "newrule", "notes", "finding", "proposal",
  "row", "agents", "pnl", "live"];
// Paradas con controles adentro que NO deben tocarse en la visita (vincular, actualizar, "Está bien"…): un tapón
// sobre lo iluminado, y tocarlo no avanza (se avanza con Next). La visita es de solo lectura también por dentro.
const SHIELDED: StopKey[] = ["bank", "book", "newrule", "notes", "row", "agents"];
// Las que muestran una lista de líneas en la burbuja (`demoGuide.<clave>.l`).
const LISTED: StopKey[] = ["nav", "book"];
const PAD = 6;          // el aire alrededor de lo iluminado
const GAP = 12;         // entre lo iluminado y la burbuja
const BUBBLE_W = 340;

const SEEN_KEY = "contabu.demoGuide.seen.v1";
function readSeen(): boolean {
  try { return window.sessionStorage.getItem(SEEN_KEY) === "1"; } catch { return false; }
}
function writeSeen() {
  try { window.sessionStorage.setItem(SEEN_KEY, "1"); } catch { /* modo privado: se ve otra vez, no pasa nada */ }
}

/** El primer elemento VISIBLE que cumple el selector (la barra lateral tiene una copia oculta en el celular). */
function visible(sel: string): HTMLElement | null {
  for (const el of Array.from(document.querySelectorAll<HTMLElement>(sel))) {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) return el;
  }
  return null;
}
async function waitFor(sel: string, ms = 4000): Promise<HTMLElement | null> {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    const el = visible(sel);
    if (el) return el;
    await new Promise((r) => window.setTimeout(r, 100));
  }
  return null;
}
async function clickWhenReady(sel: string, ms?: number): Promise<boolean> {
  const el = await waitFor(sel, ms);
  if (el) el.click();
  return !!el;
}

/** El rectángulo de `el` recortado por los contenedores con scroll que lo cortan (un panel angosto, la grilla…):
 *  sin esto, el recorte iluminaría también lo que ese contenedor esconde. null si no se ve nada. */
function clipped(el: HTMLElement): { top: number; left: number; right: number; bottom: number } | null {
  const r = el.getBoundingClientRect();
  let { top, left, right, bottom } = r;
  for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
    const o = getComputedStyle(p);
    if ([o.overflowX, o.overflowY].every((v) => v === "visible")) continue;
    const c = p.getBoundingClientRect();
    top = Math.max(top, c.top); left = Math.max(left, c.left); right = Math.min(right, c.right); bottom = Math.min(bottom, c.bottom);
  }
  return right > left && bottom > top ? { top, left, right, bottom } : null;
}

type Box = { top: number; left: number; width: number; height: number };

// LAS DOS PRUEBAS MEDIDAS: un solo lugar, cada una con su fuente — docs/JUECES - IMPLEMENTACION TECNOLOGICA.md § 5
// (los prompts finales, 24-oct). D2, el ciego del demo con el código que se entrega, 3 corridas: 14 · 15 · 14 de
// 22 errores nunca vistos; solo el código (las reglas, sin IA): 8. R3, el ④ sobre los 118 Pendientes de 2026 del
// libro real (×3 corridas): decide 15, acierta 7 sin la nota del bookkeeper y los 15 con ella (una cuenta de
// cliente autorizada, sin el tipo de empresa ni el parentesco; permiso del dueño, 23-oct).
const BLIND = { min: 14, max: 15, of: 22, rulesAlone: 8 };
const NOTE = { before: 7, after: 15, of: 15 };

export default function DemoGuide({ onNavigate, onSidebar, onOpenRow }: {
  onNavigate: (v: View) => void;
  onOpenRow: (txId: number) => void;    // la parada 11 abre el panel lateral de una fila (el mismo "ir a la fila" de Auditoría)
  onSidebar: (open: boolean) => void;   // la parada 2 abre la barra (escritorio) o el cajón (celular)
}) {
  const { t } = useTranslation();
  // null = cerrado · "intro" = la pantalla de inicio · 0..7 = la parada
  const [phase, setPhase] = useState<"intro" | number | null>(() => (readSeen() ? null : "intro"));
  const [intro, setIntro] = useState<DemoIntro | null>(null);
  const [pick, setPick] = useState<Pick | null>(null);
  const [noProposal, setNoProposal] = useState(false);
  const [noteIsVacation, setNoteIsVacation] = useState(false);   // la parada de Notas ilumina la nota de las vacaciones
  // `onOpenRow` llega como función nueva en cada pintada de App: en una ref, para que no re-dispare el efecto de las paradas.
  const openRowRef = useRef(onOpenRow);
  openRowRef.current = onOpenRow;
  const pickRef = useRef<Promise<Pick | null> | null>(null);   // /demo/guide se pide una sola vez por recorrido
  const [target, setTarget] = useState<string | null>(null);
  const [hole, setHole] = useState<Box | null>(null);
  // El tamaño de la ventana se lee AL PINTAR (guardarlo al montar falla si el panel aún no tenía tamaño) y se
  // vuelve a pintar al cambiar.
  const [, repaint] = useReducer((x: number) => x + 1, 0);
  useEffect(() => {
    window.addEventListener("resize", repaint);
    return () => window.removeEventListener("resize", repaint);
  }, []);
  const vp = { w: window.innerWidth, h: window.innerHeight };
  const bubbleRef = useRef<HTMLDivElement>(null);
  const [bubbleH, setBubbleH] = useState(180);

  const close = useCallback(() => {
    setPhase(null);
    setTarget(null);
    setHole(null);
    onSidebar(false);
    writeSeen();
  }, [onSidebar]);
  const next = useCallback(() => {
    if (typeof phase === "number" && phase + 1 >= STOPS.length) close();
    else setPhase((p) => (typeof p === "number" ? p + 1 : p));
  }, [phase, close]);

  // Atrás: de la primera parada vuelve a la pantalla de inicio. Cada parada abre sola lo suyo, así que se puede volver.
  const back = useCallback(() => {
    setPhase((p) => (typeof p === "number" ? (p === 0 ? "intro" : p - 1) : p));
  }, []);

  // "Guide" (barra de arriba o el resumen del inicio) lo vuelve a abrir.
  useEffect(() => {
    const open = () => setPhase("intro");
    window.addEventListener(OPEN_GUIDE_EVENT, open);
    return () => window.removeEventListener(OPEN_GUIDE_EVENT, open);
  }, []);

  // La pantalla de inicio: los números de Rivera HVAC de la visita, contados por la base.
  useEffect(() => {
    if (phase !== "intro" || intro) return;
    getDemoIntro().then(setIntro).catch(() => {});
  }, [phase, intro]);

  // Esc cierra.
  useEffect(() => {
    if (phase === null) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.preventDefault(); close(); } };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [phase, close]);

  // Cada parada: abre su página, hace lo que haga falta para mostrar lo suyo y dice qué iluminar.
  useEffect(() => {
    if (typeof phase !== "number") return;
    let alive = true;
    const key = STOPS[phase];
    setTarget(null);
    setHole(null);
    (async () => {
      onSidebar(key === "clients" || key === "nav");
      const getPick = () => (pickRef.current ??= getGuidePick().catch(() => null));
      let sel: string | null = null;
      if (key === "clients") sel = '[data-tour="book-switcher"]';
      if (key === "nav") sel = '[data-tour="nav-sections"]';
      if (key === "bank") { onNavigate("upload"); sel = '[data-tour="banks"]'; }
      if (key === "book") {
        onNavigate("transactions");
        await clickWhenReady('[data-tour="sheet-tab"]');
        sel = '[data-tour="sheet-grid"]';
      }
      if (key === "rules") {
        // El criterio del bookkeeper manda: la hoja de reglas (la "fórmula" del libro), en Movimientos.
        onNavigate("transactions");
        await clickWhenReady('[data-tour="rules-tab"]');
        sel = '[data-tour="rules-formula"]';
      }
      if (key === "newrule") {
        // Cómo se crea una regla: el botón "Crear regla" de la Hoja (su ventana prueba la regla contra la historia).
        // Solo se ilumina: el tapón (`SHIELDED`) impide abrir la ventana, que flota por encima de la guía.
        onNavigate("transactions");
        await clickWhenReady('[data-tour="sheet-tab"]');
        sel = '[data-tour="create-rule"]';
      }
      if (key === "notes") {
        onNavigate("notes");
        const p = await getPick();
        if (!alive) return;
        // La nota de las vacaciones; si el recorrido eligió la nómina, la primera de la lista; sin notas, la página.
        const mine = p?.note_id != null ? `[data-tour="note-${p.note_id}"]` : null;
        const found = !!mine && !!(await waitFor(mine, 2500));
        if (!alive) return;
        setNoteIsVacation(found);
        sel = found ? mine! : await waitFor('[data-tour^="note-"]', 2500) ? '[data-tour^="note-"]' : '[data-tour="notes-page"]';
      }
      if (key === "team") { onNavigate("audit"); sel = '[data-tour="ai-team"]'; }
      if (key === "finding") {
        onNavigate("audit");
        const p = await getPick();
        if (!alive) return;
        setPick(p);
        await clickWhenReady('[data-tour="tab-findings"]');
        if (p?.finding_id != null && await clickWhenReady(`[data-tour="finding-${p.finding_id}"]`)) sel = '[data-tour="detail"]';
        else sel = '[data-tour="tab-findings"]';
      }
      if (key === "proposal") {
        onNavigate("audit");
        await clickWhenReady('[data-tour="tab-proposals"]');
        // La regla con mejor evidencia de la web (la elige el servidor: `proposal_index`), no la primera de la lista.
        const p = await getPick();
        if (!alive) return;
        const has = await clickWhenReady(`[data-tour="proposal-${p?.proposal_index ?? 0}"]`, 2500);
        if (!alive) return;
        setNoProposal(!has);
        if (has) {
          // La prueba contra la historia, ya abierta: las filas que la regla cambiaría.
          await clickWhenReady('[data-tour="history-test"]', 1500);
          sel = '[data-tour="detail"]';
        } else sel = '[data-tour="tab-proposals"]';
      }
      if (key === "row" || key === "agents") {
        // La fila del hallazgo de la parada 9 (el mismo hilo de la historia): su panel lateral, abierto. La 12 usa el
        // mismo panel: si ya está abierto no se vuelve a pedir.
        onNavigate("transactions");
        if (!visible('[data-tour="tx-panel"]')) {
          const p = await getPick();
          if (!alive) return;
          if (p?.transaction_id != null) openRowRef.current(p.transaction_id);
        }
        const panel = await waitFor('[data-tour="tx-panel"]', 6000);
        if (!alive) return;
        sel = !panel ? '[data-tour="sheet-grid"]' : key === "row" ? '[data-tour="tx-panel"]' : '[data-tour="panel-agents"]';
      }
      if (key === "pnl") { onNavigate("dashboard"); sel = '[data-tour="pnl"]'; }
      if (!alive || !sel) return;
      const el = await waitFor(sel);
      if (!alive || !el) return;
      el.scrollIntoView({ block: "center", behavior: "auto" });
      setTarget(sel);
    })();
    return () => { alive = false; };
  }, [phase, onNavigate, onSidebar]);

  // Sigue a lo iluminado (scroll, cambio de tamaño, la barra que se ensancha) cuadro a cuadro.
  useEffect(() => {
    if (!target) return;
    let raf = 0;
    const tick = () => {
      const el = visible(target);
      const r = el ? clipped(el) : null;
      const w = window.innerWidth, h = window.innerHeight;
      setHole((prev) => {
        if (!r) return null;
        const top = Math.max(r.top - PAD, 4), left = Math.max(r.left - PAD, 4);
        const box = { top, left, width: Math.min(r.right + PAD, w - 4) - left, height: Math.min(r.bottom + PAD, h - 4) - top };
        return prev && Math.abs(prev.top - box.top) < 0.5 && Math.abs(prev.left - box.left) < 0.5
          && Math.abs(prev.width - box.width) < 0.5 && Math.abs(prev.height - box.height) < 0.5 ? prev : box;
      });
      raf = requestAnimationFrame(tick);
    };
    tick();
    return () => cancelAnimationFrame(raf);
  }, [target]);

  // Tocar lo iluminado avanza (el clic sigue su camino: abre el menú, la fila…).
  useEffect(() => {
    if (typeof phase !== "number" || !target || SHIELDED.includes(STOPS[phase])) return;
    const onClick = (e: MouseEvent) => {
      const el = visible(target);
      if (el && el.contains(e.target as Node)) window.setTimeout(next, 150);
    };
    document.addEventListener("click", onClick, true);
    return () => document.removeEventListener("click", onClick, true);
  }, [phase, target, next]);

  useLayoutEffect(() => {
    const h = bubbleRef.current?.offsetHeight;
    if (h && Math.abs(h - bubbleH) > 1) setBubbleH(h);
  });

  if (phase === null) return null;

  if (phase === "intro") {
    return <IntroScreen intro={intro} onStart={() => setPhase(0)} onSkip={close} />;
  }

  const key = STOPS[phase];
  const centered = key === "who" || key === "live";
  const mobile = vp.w < 640;
  const last = phase === STOPS.length - 1;

  // Dónde va la burbuja: al lado de lo iluminado sin taparlo; en el celular, abajo (o arriba si lo iluminado está abajo).
  let bubbleStyle: CSSProperties;
  if (centered || !hole) {
    bubbleStyle = { top: Math.max(16, (vp.h - bubbleH) / 2), left: Math.max(16, (vp.w - Math.min(440, vp.w - 32)) / 2), width: Math.min(440, vp.w - 32) };
  } else if (mobile) {
    const holeLow = hole.top + hole.height / 2 > vp.h / 2;
    bubbleStyle = holeLow ? { top: 8, left: 8, right: 8 } : { bottom: 8, left: 8, right: 8 };
  } else {
    const clampTop = (y: number) => Math.min(Math.max(8, y), vp.h - bubbleH - 8);
    const clampLeft = (x: number) => Math.min(Math.max(8, x), vp.w - BUBBLE_W - 8);
    if (hole.left + hole.width + GAP + BUBBLE_W <= vp.w - 8) bubbleStyle = { top: clampTop(hole.top), left: hole.left + hole.width + GAP, width: BUBBLE_W };
    else if (hole.left - GAP - BUBBLE_W >= 8) bubbleStyle = { top: clampTop(hole.top), left: hole.left - GAP - BUBBLE_W, width: BUBBLE_W };
    else if (hole.top + hole.height + GAP + bubbleH <= vp.h - 8) bubbleStyle = { top: hole.top + hole.height + GAP, left: clampLeft(hole.left), width: BUBBLE_W };
    else if (hole.top - GAP - bubbleH >= 8) bubbleStyle = { top: hole.top - GAP - bubbleH, left: clampLeft(hole.left), width: BUBBLE_W };
    else bubbleStyle = { bottom: 8, right: 8, width: BUBBLE_W };   // lo iluminado ocupa casi todo: a la esquina
  }

  const body = key === "finding" ? t(`demoGuide.finding.${pick?.kind ?? "finding"}`)
    : key === "proposal" ? (noProposal ? t("demoGuide.proposal.none") : t("demoGuide.proposal.b"))
    : LISTED.includes(key) ? null
    : t(`demoGuide.${key}.b`);
  const lines = LISTED.includes(key) ? (t(`demoGuide.${key}.l`, { returnObjects: true }) as unknown as string[]) : null;
  const title = t(`demoGuide.${key}.t`);
  const extra = key === "team" ? t("demoGuide.team.measured")
    : key === "finding" && pick?.kind !== "payroll" ? t("demoGuide.finding.judged")
    : key === "proposal" && !noProposal ? t("demoGuide.proposal.tourOnly")
    : key === "book" ? t("demoGuide.book.tools")
    : key === "notes" && noteIsVacation ? t("demoGuide.notes.vacation")
    : key === "nav" ? t("demoGuide.nav.end") : null;

  return (
    <div className="fixed inset-0 z-[70] pointer-events-none" role="dialog" aria-modal="true" aria-label={title}>
      {/* Lo oscuro, con el recorte. Fuera del recorte nada recibe clics; dentro, sí (se avanza tocándolo). */}
      {hole && !centered ? (
        <>
          <div className="pointer-events-none fixed rounded-xl ring-2 ring-white/90 transition-all duration-150"
               style={{ ...hole, boxShadow: "0 0 0 9999px rgba(15, 23, 42, 0.5)" }} />
          <div className="fixed pointer-events-auto" style={{ top: 0, left: 0, right: 0, height: hole.top }} />
          <div className="fixed pointer-events-auto" style={{ top: hole.top + hole.height, left: 0, right: 0, bottom: 0 }} />
          <div className="fixed pointer-events-auto" style={{ top: hole.top, left: 0, width: hole.left, height: hole.height }} />
          <div className="fixed pointer-events-auto" style={{ top: hole.top, left: hole.left + hole.width, right: 0, height: hole.height }} />
          {SHIELDED.includes(key) && <div className="fixed pointer-events-auto" style={hole} />}
        </>
      ) : (
        <div className="fixed inset-0 bg-slate-900/50 pointer-events-auto" />
      )}

      <div ref={bubbleRef} className="fixed pointer-events-auto rounded-2xl bg-white shadow-2xl border border-slate-200 p-4 space-y-2" style={bubbleStyle}>
        {/* Una barra delgada, sin números (25-oct): `role="progressbar"` para quien no ve la pantalla. */}
        <div role="progressbar" aria-label={t("demoGuide.progressLabel")} aria-valuemin={1} aria-valuemax={STOPS.length}
             aria-valuenow={phase + 1} className="h-1 rounded-full bg-slate-100 overflow-hidden">
          <div className="h-full rounded-full bg-brand-green transition-all duration-200" style={{ width: `${((phase + 1) / STOPS.length) * 100}%` }} />
        </div>
        <h2 className="font-semibold text-slate-900 leading-snug">{title}</h2>
        {body && <p className="text-sm text-slate-600">{body}</p>}
        {lines && (
          <>
            <p className={`${key === "nav" ? "hidden sm:block " : ""}text-sm text-slate-600`}>{t(`demoGuide.${key}.lead`)}</p>
            {/* En el celular las líneas corren seguidas ("a · b · c"): una lista alta taparía lo iluminado (la barra). */}
            <ul className="text-xs leading-snug text-slate-600 sm:text-[13px] sm:space-y-1 sm:list-disc sm:ps-4 sm:marker:text-slate-300">
              {lines.map((l) => <li key={l} className="inline after:content-['_·_'] last:after:content-none sm:block sm:after:content-none">{l}</li>)}
            </ul>
          </>
        )}
        {extra && <p className="text-xs text-slate-500">{extra}</p>}
        <div className="flex flex-wrap items-center gap-x-2 gap-y-2 pt-1">
          <button type="button" onClick={close} className="text-sm text-slate-500 hover:text-slate-800">{t("demoGuide.skip")}</button>
          <button type="button" onClick={back}
                  className="px-2 py-1 rounded-lg text-sm text-slate-600 hover:bg-slate-100 whitespace-nowrap">← {t("demoGuide.back")}</button>
          <div className="ms-auto flex items-center gap-2">
            {last && (
              <button type="button" onClick={() => { close(); void goLive(); }}
                      className="px-3 py-1.5 rounded-lg text-sm font-semibold whitespace-nowrap border border-brand-green text-brand-green hover:bg-emerald-50">
                {t("demoDoor.signInGoogle")}
              </button>
            )}
            <button type="button" onClick={last ? close : next} autoFocus
                    className="px-3 py-1.5 rounded-lg text-sm font-semibold text-white bg-brand-green hover:opacity-90">
              {last ? t("demoGuide.done") : t("demoGuide.next")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/** La pantalla de inicio, antes de las paradas. Todos los números de Rivera HVAC salen de la base (`/api/demo/intro`). */
function IntroScreen({ intro, onStart, onSkip }: { intro: DemoIntro | null; onStart: () => void; onSkip: () => void }) {
  const { t } = useTranslation();
  const n = (v?: number) => (v == null ? "…" : v.toLocaleString());
  return (
    <div className="fixed inset-0 z-[70] bg-slate-900/50 overflow-y-auto" role="dialog" aria-modal="true" aria-label={t("demoGuide.intro.title")}>
      <div className="min-h-full flex items-center justify-center p-4">
        <div className="w-full max-w-3xl rounded-3xl bg-white shadow-2xl p-5 sm:p-7 space-y-5">
          <div className="space-y-2 text-center">
            <div className="inline-block text-[11px] font-semibold tracking-wide uppercase text-emerald-800 bg-emerald-50 rounded-full px-3 py-1">
              {t("demoGuide.intro.powered")}
            </div>
            <h1 className="text-2xl sm:text-3xl font-bold tracking-tight text-slate-900">{t("demoGuide.intro.title")}</h1>
            <p className="text-slate-600 max-w-xl mx-auto">{t("demoGuide.intro.lead", { count: intro?.team.length ?? 4 })}</p>
            {intro && (
              <ul className="flex flex-wrap justify-center gap-1.5 pt-1">
                {intro.team.map((a) => (
                  <li key={a.agent} className="text-xs rounded-full border border-slate-200 px-2.5 py-1 text-slate-700">
                    {/* El ② es la búsqueda en la web (Tavily) MÁS el modelo que juzga lo que encontró: el nombre del
                        modelo sale medido (`agent_models.DEFAULTS`), "Tavily +" es fijo. */}
                    {t(`demoGuide.agent.${a.agent}`)} · <span className="font-semibold">{a.agent === "classifier" ? `Tavily + ${a.model}` : a.model}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <section className="rounded-2xl border border-slate-200 bg-slate-50 p-4 space-y-1.5">
              <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{t("demoGuide.intro.before")}</h2>
              <p className="text-sm text-slate-700">{t("demoGuide.intro.rows", { count: intro?.rows ?? 0, n: n(intro?.rows) })}</p>
              <p className="text-sm text-slate-700">{t("demoGuide.intro.noCategory")}</p>
              <p className="text-sm text-rose-700 font-medium">{t("demoGuide.intro.issues", { count: intro?.issues ?? 0, n: n(intro?.issues) })}</p>
            </section>
            <section className="rounded-2xl border border-emerald-200 bg-emerald-50/60 p-4 space-y-1.5">
              <h2 className="text-xs font-semibold uppercase tracking-wide text-emerald-800">{t("demoGuide.intro.after")}</h2>
              <p className="text-sm text-slate-700">{t("demoGuide.intro.pnl", { count: intro?.pending ?? 0, n: n(intro?.pending) })}</p>
              <p className="text-sm text-slate-700">{t("demoGuide.intro.explained", { count: intro?.explained ?? 0, n: n(intro?.explained) })}</p>
              <p className="text-sm text-slate-700">{t("demoGuide.intro.proposals", { count: intro?.proposals ?? 0, n: n(intro?.proposals) })}</p>
            </section>
          </div>
          {intro?.book && <p className="-mt-2 text-[11px] text-center text-slate-400">{t("demoGuide.intro.source", { book: intro.book })}</p>}

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-2xl border border-slate-200 p-4">
              <div className="text-2xl font-bold text-slate-900 tabular-nums">{BLIND.min}–{BLIND.max} / {BLIND.of}</div>
              <p className="text-sm text-slate-600">{t("demoGuide.intro.blind", { min: BLIND.min, max: BLIND.max, of: BLIND.of, rules: BLIND.rulesAlone })}</p>
            </div>
            <div className="rounded-2xl border border-slate-200 p-4">
              {/* Son 15 movimientos, no un porcentaje (7 de 15 → 15 de 15 con la nota): un % inventaría una base. */}
              <div className="text-2xl font-bold text-slate-900 tabular-nums">{NOTE.before} / {NOTE.of} → {NOTE.after} / {NOTE.of}</div>
              <p className="text-sm text-slate-600">{t("demoGuide.intro.note")}</p>
              <p className="text-[11px] text-slate-400 mt-0.5">{t("demoGuide.intro.noteSource")}</p>
            </div>
          </div>

          <div className="flex flex-col-reverse sm:flex-row items-center justify-center gap-2">
            <button type="button" onClick={onSkip} className="px-4 py-2 text-sm text-slate-500 hover:text-slate-800">{t("demoGuide.skip")}</button>
            <button type="button" onClick={onStart} autoFocus
                    className="px-5 py-2.5 rounded-xl font-semibold text-white shadow-sm"
                    style={{ background: "linear-gradient(135deg, #15803d, #f4b400)" }}>
              {t("demoGuide.intro.start")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
