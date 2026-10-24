import { useCallback, useEffect, useLayoutEffect, useReducer, useRef, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { getDemoIntro, getGuidePick, type DemoIntro } from "../api";
import { goLive, OPEN_GUIDE_EVENT } from "../demo";
import type { View } from "./Sidebar";

// EL RECORRIDO GUIADO DEL DEMO (FASE 13, área Firm · 23-oct; aprobado por Jose sobre dos dibujos). Una pantalla de
// inicio y 7 paradas: la pantalla se oscurece al 50 %, un recorte claro y redondeado sobre lo que hay que tocar, y
// al lado una burbuja ("N of 7", título, 1–2 frases, Skip / Next). Se avanza tocando lo iluminado o con Next; Esc
// o Skip lo cierran. Arranca solo la primera vez (por puerta) y se reabre con "Guide". En el celular, la burbuja va
// abajo (o arriba, si lo iluminado queda abajo). Cada parada abre sola la página que le toca.
// Las marcas que ilumina son `data-tour="…"` en los componentes (PageKit, Sidebar, Dashboard, AuditWorkspace).

// ── LAS DOS PRUEBAS MEDIDAS: un solo lugar, cada una con su fuente ─────────────────────────────────────────────
// docs/JUECES - IMPLEMENTACION TECNOLOGICA.md → §5 (la medición final, M2, 24-oct): el ciego del demo (D2) con el
// código que se entrega (sha1 congelado), 5 corridas: 13 · 12 · 13 · 14 · 16 de 22 errores nunca vistos; solo el
// código (las reglas, sin IA): 8 de 22.
const BLIND = { min: 12, max: 16, of: 22, rulesAlone: 8 };
// docs/FASE 11.A.md → "22 de octubre de 2026 (2)": el ④ (Super VL) con y sin la nota del libro, en el libro real:
// 62 → 112 aciertos de 121 (51 % → 93 %). Se publica como "an authorized client account", sin el tipo de empresa
// ni el parentesco (permiso del dueño, 23-oct).
// ⏸️ PENDIENTE (Jose, 24-oct): se midió sobre el libro ENTERO y solo vale 2026. Mientras `pending` sea true la
// tarjeta NO se muestra. Cuando Auditoría dé el número de 2026 (M1 en `DEMO Y ENTREGA.md` → "Los documentos para los
// jueces"; el resultado irá a `JUECES - IMPLEMENTACION TECNOLOGICA.md` §5, fila R3), se cambian `before`,
// `after` y `of`, se pone `pending: false` y se cita aquí la entrada nueva.
const NOTE = { before: 62, after: 112, of: 121, pending: true };
// docs/FASE 11.A.md → "17 de octubre de 2026": el ⑤ en Lightning, Super y Ultra encontró MENOS errores nunca vistos
// que en Nano (control 14–15; Lightning 12–13, Super 12–13, Ultra 13). Es la línea de la parada 4.

type Door = "tour" | "live";
type Pick = Awaited<ReturnType<typeof getGuidePick>>;
type StopKey = "s1" | "s2" | "s3" | "s4" | "s5" | "s6" | "s7";
const STOPS: StopKey[] = ["s1", "s2", "s3", "s4", "s5", "s6", "s7"];
const PAD = 6;          // el aire alrededor de lo iluminado
const GAP = 12;         // entre lo iluminado y la burbuja
const BUBBLE_W = 340;

const seenKey = (door: Door) => `contabu.demoGuide.seen.v1.${door}`;
function readSeen(door: Door): boolean {
  try { return window.localStorage.getItem(seenKey(door)) === "1"; } catch { return false; }
}
function writeSeen(door: Door) {
  try { window.localStorage.setItem(seenKey(door), "1"); } catch { /* modo privado: se ve otra vez, no pasa nada */ }
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

type Box = { top: number; left: number; width: number; height: number };

export default function DemoGuide({ door, onNavigate, onSidebar }: {
  door: Door;
  onNavigate: (v: View) => void;
  onSidebar: (open: boolean) => void;   // la parada 2 abre la barra (escritorio) o el cajón (celular)
}) {
  const { t } = useTranslation();
  // null = cerrado · "intro" = la pantalla de inicio · 0..6 = la parada
  const [phase, setPhase] = useState<"intro" | number | null>(() => (readSeen(door) ? null : "intro"));
  const [intro, setIntro] = useState<DemoIntro | null>(null);
  const [pick, setPick] = useState<Pick | null>(null);
  const [noProposal, setNoProposal] = useState(false);
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
    writeSeen(door);
  }, [door, onSidebar]);
  const next = useCallback(() => {
    if (typeof phase === "number" && phase + 1 >= STOPS.length) close();
    else setPhase((p) => (typeof p === "number" ? p + 1 : p));
  }, [phase, close]);

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
      onSidebar(key === "s2");
      let sel: string | null = null;
      if (key === "s2") sel = '[data-tour="book-switcher"]';
      if (key === "s3") { onNavigate("dashboard"); sel = '[data-tour="pnl"]'; }
      if (key === "s4") { onNavigate("audit"); sel = '[data-tour="ai-team"]'; }
      if (key === "s5") {
        onNavigate("audit");
        const p = await getGuidePick().catch(() => null);
        if (!alive) return;
        setPick(p);
        await clickWhenReady('[data-tour="tab-findings"]');
        if (p?.finding_id != null && await clickWhenReady(`[data-tour="finding-${p.finding_id}"]`)) sel = '[data-tour="detail"]';
        else sel = '[data-tour="tab-findings"]';
      }
      if (key === "s6") {
        onNavigate("audit");
        await clickWhenReady('[data-tour="tab-proposals"]');
        const has = await clickWhenReady('[data-tour="proposal-0"]', 2500);
        if (!alive) return;
        setNoProposal(!has);
        if (has) {
          // La prueba contra la historia, ya abierta: las filas que la regla cambiaría.
          await clickWhenReady('[data-tour="history-test"]', 1500);
          sel = '[data-tour="detail"]';
        } else sel = '[data-tour="tab-proposals"]';
      }
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
      const r = el?.getBoundingClientRect();
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

  // Tocar lo iluminado avanza (el clic sigue su camino: abre el menú, la fila…). En el demo en vivo, la parada 4
  // NO avanza al tocar: ahí se toca "Review the book" y se espera a que termine.
  useEffect(() => {
    if (typeof phase !== "number" || !target) return;
    if (STOPS[phase] === "s4" && door === "live") return;
    const onClick = (e: MouseEvent) => {
      const el = visible(target);
      if (el && el.contains(e.target as Node)) window.setTimeout(next, 150);
    };
    document.addEventListener("click", onClick, true);
    return () => document.removeEventListener("click", onClick, true);
  }, [phase, target, door, next]);

  useLayoutEffect(() => {
    const h = bubbleRef.current?.offsetHeight;
    if (h && Math.abs(h - bubbleH) > 1) setBubbleH(h);
  });

  if (phase === null) return null;

  if (phase === "intro") {
    return <IntroScreen intro={intro} onStart={() => setPhase(0)} onSkip={close} />;
  }

  const key = STOPS[phase];
  const centered = key === "s1" || key === "s7";
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

  const body = key === "s4" ? t(`demoGuide.s4.${door}`)
    : key === "s5" ? t(`demoGuide.s5.${pick?.kind ?? "finding"}`)
    : key === "s6" ? (noProposal ? t(`demoGuide.s6.none.${door}`) : t("demoGuide.s6.b"))
    : key === "s7" ? t(`demoGuide.s7.${door}.b`)
    : t(`demoGuide.${key}.b`);
  const title = key === "s7" ? t(`demoGuide.s7.${door}.t`) : t(`demoGuide.${key}.t`);
  const extra = key === "s4" ? t("demoGuide.s4.measured")
    : key === "s5" && pick?.kind !== "payroll" ? t("demoGuide.s5.judged")
    : key === "s6" && door === "tour" && !noProposal ? t("demoGuide.s6.tourOnly") : null;

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
        </>
      ) : (
        <div className="fixed inset-0 bg-slate-900/50 pointer-events-auto" />
      )}

      <div ref={bubbleRef} className="fixed pointer-events-auto rounded-2xl bg-white shadow-2xl border border-slate-200 p-4 space-y-2" style={bubbleStyle}>
        <div className="text-[11px] font-semibold text-brand-green">{t("demoGuide.progress", { n: phase + 1, total: STOPS.length })}</div>
        <h2 className="font-semibold text-slate-900 leading-snug">{title}</h2>
        <p className="text-sm text-slate-600">{body}</p>
        {extra && <p className="text-xs text-slate-500">{extra}</p>}
        <div className="flex items-center gap-2 pt-1">
          <button type="button" onClick={close} className="text-sm text-slate-500 hover:text-slate-800">{t("demoGuide.skip")}</button>
          <div className="ms-auto flex items-center gap-2">
            {last && door === "tour" && (
              <button type="button" onClick={() => { close(); void goLive(); }}
                      className="px-3 py-1.5 rounded-lg text-sm font-semibold border border-brand-green text-brand-green hover:bg-emerald-50">
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
  const pct = (a: number) => Math.round((a / NOTE.of) * 100);
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
                    {t(`demoGuide.agent.${a.agent}`)} · <span className="font-semibold">{a.model}</span>
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

          <div className={`grid gap-3 ${NOTE.pending ? "" : "sm:grid-cols-2"}`}>
            <div className="rounded-2xl border border-slate-200 p-4">
              <div className="text-2xl font-bold text-slate-900 tabular-nums">{BLIND.min}–{BLIND.max} / {BLIND.of}</div>
              <p className="text-sm text-slate-600">{t("demoGuide.intro.blind", { min: BLIND.min, max: BLIND.max, of: BLIND.of, rules: BLIND.rulesAlone })}</p>
            </div>
            {!NOTE.pending && (
              <div className="rounded-2xl border border-slate-200 p-4">
                <div className="text-2xl font-bold text-slate-900 tabular-nums">{pct(NOTE.before)}% → {pct(NOTE.after)}%</div>
                <p className="text-sm text-slate-600">{t("demoGuide.intro.note")}</p>
                <p className="text-[11px] text-slate-400 mt-0.5">{t("demoGuide.intro.noteSource")}</p>
              </div>
            )}
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
