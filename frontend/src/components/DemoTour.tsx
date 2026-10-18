import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { OPEN_TOUR_EVENT } from "../demo";

// EL RECORRIDO GUIADO del demo (FASE 13, 3-oct; rehecho el 14-oct con lo que existe hoy). Los 8 pasos de
// docs/DEMO Y ENTREGA.md → "🧭 El recorrido guiado": lo que hace el jurado en ~3 minutos, y la base del
// guion del video. SOLO se monta en el despliegue de demo (App.tsx lo condiciona a `DEMO_MODE`).
//
// Tarjeta flotante abajo a la derecha, minimizable a una pastilla con el avance. Lo marcado se guarda
// en el navegador del visitante (localStorage): es SU sesión del demo, nadie más la ve. Si el
// navegador no deja guardar (ventana privada), el recorrido funciona igual, solo no se recuerda.

const STEPS = ["s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8"] as const;
// v2 (14-oct): los pasos cambiaron de sentido; con la clave vieja, lo marcado en v1 caería en otros pasos.
const KEY_DONE = "contabu.demoTour.done.v2";
// v2 (18-oct): arranca MINIMIZADO (la pastilla); el resumen del inicio lo ofrece con "Recorrido guiado".
// Hace falta clave nueva: el efecto de abajo guarda el valor en cada visita, así que todo visitante de antes
// ya tiene "desplegado" guardado en v1 y un cambio solo del valor por defecto no le llegaría.
const KEY_MIN = "contabu.demoTour.minimized.v2";

function readJSON<T>(key: string, fallback: T): T {
  try { const raw = localStorage.getItem(key); return raw ? (JSON.parse(raw) as T) : fallback; }
  catch { return fallback; }
}
function writeJSON(key: string, value: unknown) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* sin almacenamiento: no pasa nada */ }
}

export default function DemoTour() {
  const { t } = useTranslation();
  // El paso 1 ("Probar el demo") ya está hecho por estar aquí: arranca marcado.
  const [done, setDone] = useState<string[]>(() => readJSON<string[]>(KEY_DONE, ["s1"]));
  const [minimized, setMinimized] = useState<boolean>(() => readJSON<boolean>(KEY_MIN, true));
  useEffect(() => writeJSON(KEY_DONE, done), [done]);
  useEffect(() => writeJSON(KEY_MIN, minimized), [minimized]);
  // "Abrir el recorrido" desde el resumen del inicio (18-oct): lo despliega si estaba minimizado.
  useEffect(() => {
    const open = () => setMinimized(false);
    window.addEventListener(OPEN_TOUR_EVENT, open);
    return () => window.removeEventListener(OPEN_TOUR_EVENT, open);
  }, []);

  const toggle = (s: string) => setDone((d) => (d.includes(s) ? d.filter((x) => x !== s) : [...d, s]));
  const count = STEPS.filter((s) => done.includes(s)).length;
  const progress = t("shell.tour.progress", { done: count, total: STEPS.length });

  if (minimized) {
    return (
      <button
        onClick={() => setMinimized(false)}
        className="fixed bottom-4 end-4 z-30 flex items-center gap-2 rounded-full bg-white border border-slate-200 shadow-lg px-4 py-2 text-sm hover:bg-slate-50"
      >
        <span className="font-semibold text-slate-800">{t("shell.tour.title")}</span>
        <span className="text-xs text-white bg-brand-green rounded-full px-2 py-0.5">{progress}</span>
      </button>
    );
  }

  return (
    <aside
      aria-label={t("shell.tour.title")}
      className="fixed bottom-4 end-4 z-30 w-[22rem] max-w-[calc(100vw-2rem)] max-h-[70vh] flex flex-col rounded-2xl bg-white border border-slate-200 shadow-xl"
    >
      <div className="shrink-0 flex items-center gap-2 px-4 py-3 border-b border-slate-100">
        <span className="font-semibold text-slate-900">{t("shell.tour.title")}</span>
        <span className="text-xs text-white bg-brand-green rounded-full px-2 py-0.5">{progress}</span>
        <button
          onClick={() => setMinimized(true)}
          aria-label={t("shell.tour.minimize")}
          title={t("shell.tour.minimize")}
          className="ms-auto p-1 rounded text-slate-400 hover:text-slate-700"
        >
          <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round"><path d="M5 12h14" /></svg>
        </button>
      </div>
      <div className="h-1 bg-slate-100 shrink-0">
        <div className="h-full bg-brand-green transition-all" style={{ width: `${(count / STEPS.length) * 100}%` }} />
      </div>

      <ol className="overflow-y-auto px-3 py-2 space-y-1">
        {STEPS.map((s, i) => {
          const isDone = done.includes(s);
          return (
            <li key={s}>
              <label className={`flex gap-3 rounded-lg px-2 py-2 cursor-pointer hover:bg-slate-50 ${isDone ? "opacity-60" : ""}`}>
                <input type="checkbox" checked={isDone} onChange={() => toggle(s)}
                  className="mt-0.5 size-4 accent-[var(--color-brand-green)] shrink-0" />
                <span className="min-w-0">
                  <span className={`block text-sm font-medium text-slate-900 ${isDone ? "line-through" : ""}`}>
                    {i + 1}. {t(`shell.tour.steps.${s}.t`)}
                  </span>
                  <span className="block text-xs text-slate-500 mt-0.5">{t(`shell.tour.steps.${s}.d`)}</span>
                </span>
              </label>
            </li>
          );
        })}
      </ol>

      <div className="shrink-0 flex items-center gap-2 px-4 py-2.5 border-t border-slate-100 text-xs">
        {count === STEPS.length
          ? <span className="text-brand-green font-medium">{t("shell.tour.finished")}</span>
          : <span className="text-slate-400">{progress}</span>}
        <button onClick={() => setDone(["s1"])} className="ms-auto text-slate-400 hover:text-slate-700 underline">
          {t("shell.tour.reset")}
        </button>
      </div>
    </aside>
  );
}
