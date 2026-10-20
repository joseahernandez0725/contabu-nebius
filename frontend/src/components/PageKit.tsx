import { forwardRef, useEffect, useRef, type ReactNode } from "react";

// EL DISEÑO DE PÁGINA COMPARTIDO (FASE 13, área Firm · 13-oct-2026).
// Decisión de Jose (tablero, 13-oct): "un solo diseño de página para toda la app". El modelo es la
// pestaña Auditoría (`AuditWorkspace.tsx`, de Auditoría/IA): de ahí sale la ESTRUCTURA — no el contenido:
//   · PageHeader  → la línea de estado: cifras clave + "actualizado hace X" + UNA acción principal + "⋯"
//   · ListDetail  → la lista a la izquierda y el detalle a la derecha (debajo, en pantalla angosta)
//   · DetailCard  → el detalle; el panel lateral de la transacción usa el MISMO (con `onClose`)
// El título de la página NO se repite aquí: vive en la barra de arriba de la cáscara (App.tsx), un solo lugar.
//
// Colores de estado (los mismos en toda la app): rojo = problema · ámbar = por revisar / Pendiente ·
// azul = observación · verde = bien / acción principal · gris = apagado.

export const DOT: Record<string, string> = {
  problem: "bg-rose-500", high: "bg-rose-500",
  review: "bg-amber-500", medium: "bg-amber-500", pending: "bg-amber-400",
  info: "bg-sky-500", ok: "bg-emerald-500", low: "bg-slate-400", off: "bg-slate-300",
};

export const TONE = {
  neutral: "text-slate-900", pending: "text-amber-700", problem: "text-rose-700", good: "text-emerald-700",
} as const;

export const BTN_PRIMARY =
  "px-3.5 py-1.5 rounded-lg text-sm font-semibold text-white bg-emerald-700 hover:bg-emerald-800 disabled:opacity-60";
export const BTN_SECONDARY =
  "px-2.5 py-1 rounded-lg text-xs font-medium border border-slate-300 text-slate-700 hover:bg-slate-50 disabled:opacity-40";
export const BTN_SMALL_PRIMARY =
  "px-2.5 py-1 rounded-lg text-xs font-semibold text-white bg-emerald-700 hover:bg-emerald-800 disabled:opacity-40";
export const BTN_GHOST = "px-2 py-1 rounded-lg text-xs text-slate-600 hover:bg-slate-100 disabled:opacity-40";

/** "hace 5 minutos" en el idioma de la pantalla. `secs` = cuánto hace (positivo). */
export function agoText(lang: string, secs: number): string {
  const rtf = new Intl.RelativeTimeFormat(lang, { numeric: "auto" });
  if (secs < 3600) return rtf.format(-Math.max(1, Math.round(secs / 60)), "minute");
  if (secs < 86400) return rtf.format(-Math.round(secs / 3600), "hour");
  return rtf.format(-Math.round(secs / 86400), "day");
}

/** Segundos desde una fecha ISO del backend (UTC sin zona, como la guarda la base). */
export function secsSince(isoUtc: string): number {
  return Math.max(0, (Date.now() - Date.parse(isoUtc.endsWith("Z") ? isoUtc : isoUtc + "Z")) / 1000);
}

// ── La línea de estado ───────────────────────────────────────────────────────────────────────────

export type MoreItem = { label: string; onClick: () => void; disabled?: boolean; danger?: boolean };

/** Cifras clave a la izquierda, "actualizado hace X", y a la derecha UNA acción principal + "⋯".
 *  `below`: una fila de ancho completo debajo (p. ej. el avance de un flujo que está corriendo). */
export function PageHeader({ children, meta, primary, more, moreLabel, below }: {
  children?: ReactNode; meta?: ReactNode; primary?: ReactNode; more?: MoreItem[]; moreLabel?: string; below?: ReactNode;
}) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 px-3 py-2.5 flex flex-wrap items-center gap-x-5 gap-y-2">
      {children}
      {meta && <div className="text-xs text-slate-500">{meta}</div>}
      {(primary || (more && more.length > 0)) && (
        <div className="ms-auto flex items-center gap-1.5">
          {primary}
          {more && more.length > 0 && <MoreMenu items={more} label={moreLabel} />}
        </div>
      )}
      {below && <div className="basis-full">{below}</div>}
    </div>
  );
}

export function Stat({ value, label, tone = TONE.neutral }: { value?: ReactNode; label: string; tone?: string }) {
  return (
    <div className="flex items-baseline gap-1.5">
      <span className={`text-lg font-semibold tabular-nums ${tone}`}>{value ?? "–"}</span>
      <span className="text-xs text-slate-500">{label}</span>
    </div>
  );
}

/** El menú "⋯": las acciones secundarias de la página. Se cierra al elegir y al hacer clic afuera. */
export function MoreMenu({ items, label }: { items: MoreItem[]; label?: string }) {
  const ref = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) ref.current.open = false; };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  return (
    <details ref={ref} className="relative">
      <summary className="list-none cursor-pointer px-2 py-1.5 rounded-lg text-slate-500 hover:bg-slate-100 text-sm" title={label} aria-label={label}>⋯</summary>
      <div className="absolute end-0 mt-1 w-56 bg-white border border-slate-200 rounded-lg shadow-lg py-1 z-20 text-sm">
        {items.map((it, i) => (
          <button key={i} disabled={it.disabled}
                  onClick={() => { if (ref.current) ref.current.open = false; it.onClick(); }}
                  className={`w-full text-start px-3 py-1.5 hover:bg-slate-50 disabled:opacity-50 ${it.danger ? "text-rose-700" : ""}`}>
            {it.label}
          </button>
        ))}
      </div>
    </details>
  );
}

// ── Lista + detalle ──────────────────────────────────────────────────────────────────────────────

/** La lista a la izquierda y el detalle a la derecha. En pantalla angosta, el detalle va DEBAJO y, al
 *  elegir algo (`selKey` cambia), la página baja hasta él.
 *  `wide="detail"`: cuando lo que se lee es el DETALLE (un P&L, un banco), la lista va angosta (300 px)
 *  y el detalle toma el resto. `wide="split"`: 60/40 (Auditoría, 19-oct) — las dos partes pesan, el
 *  detalle queda fijo como en `wide="list"`. Por defecto (`"list"`) la lista es lo ancho y el detalle
 *  mide 420 px. */
export function ListDetail({ list, detail, selKey, wide = "list" }: {
  list: ReactNode; detail: ReactNode; selKey?: unknown; wide?: "list" | "detail" | "split";
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (selKey != null && window.innerWidth < 1024) ref.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [selKey]);
  const cols = wide === "detail" ? "lg:grid-cols-[300px_minmax(0,1fr)]"
    : wide === "split" ? "lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]"
    : "lg:grid-cols-[minmax(0,1fr)_420px]";
  return (
    <div className={`grid gap-3 ${cols} items-start`}>
      {list}
      <DetailCard ref={ref} sticky={wide !== "detail"}>{detail}</DetailCard>
    </div>
  );
}

/** La tarjeta de la lista: pestañas opcionales arriba, los renglones, y un pie opcional. */
export function ListCard({ tabs, children, footer, header }: {
  tabs?: ReactNode; header?: ReactNode; children: ReactNode; footer?: ReactNode;
}) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
      {header}
      {tabs}
      <ul className="divide-y divide-slate-100 max-h-[60vh] overflow-y-auto">{children}</ul>
      {footer && <p className="px-3 py-1.5 text-[11px] text-slate-400 border-t border-slate-100">{footer}</p>}
    </div>
  );
}

export function Tabs<K extends string>({ items, active, onChange }: {
  items: { key: K; label: string; count?: number }[]; active: K; onChange: (k: K) => void;
}) {
  return (
    <div className="flex border-b border-slate-200 text-sm">
      {items.map((it) => (
        <button key={it.key} onClick={() => onChange(it.key)}
                className={`flex-1 px-3 py-2 font-medium ${active === it.key ? "text-emerald-800 border-b-2 border-emerald-700 -mb-px" : "text-slate-500 hover:text-slate-800"}`}>
          {it.label} {it.count != null && <span className="text-xs text-slate-400">{it.count}</span>}
        </button>
      ))}
    </div>
  );
}

/** Un renglón de la lista: puntito de estado · título · subtítulo · lo de la derecha. */
export function ListRow({ active, onClick, dot, title, sub, right }: {
  active: boolean; onClick: () => void; dot: string; title: ReactNode; sub?: ReactNode; right?: ReactNode;
}) {
  return (
    <li>
      <button onClick={onClick}
              className={`w-full text-start px-3 py-2 flex items-center gap-2.5 ${active ? "bg-emerald-50" : "hover:bg-slate-50"}`}>
        <span className={`w-2 h-2 rounded-full shrink-0 ${dot}`} />
        <span className="min-w-0 flex-1">
          <span className="block text-sm text-slate-900 truncate">{title}</span>
          {sub && <span className="block text-xs text-slate-500 truncate">{sub}</span>}
        </span>
        <span className="flex items-center gap-1.5 shrink-0">{right}</span>
      </button>
    </li>
  );
}

export function EmptyRow({ text }: { text: string }) {
  return <li className="px-3 py-4 text-sm text-slate-400">{text}</li>;
}

export function Badge({ cls, children, title }: { cls: string; children: ReactNode; title?: string }) {
  return <span title={title} className={`text-[11px] px-1.5 py-0.5 rounded ring-1 ring-inset whitespace-nowrap shrink-0 ${cls}`}>{children}</span>;
}

// ── El detalle ───────────────────────────────────────────────────────────────────────────────────

/** La tarjeta del detalle. `sticky`: se queda a la vista al bajar (lista + detalle). `onClose`: el
 *  mismo detalle como PANEL LATERAL (la transacción en Bookkeeping), con su botón ×. */
export const DetailCard = forwardRef<HTMLDivElement, {
  children: ReactNode; sticky?: boolean; onClose?: () => void; closeLabel?: string; className?: string;
}>(function DetailCard({ children, sticky, onClose, closeLabel, className = "" }, ref) {
  return (
    <div ref={ref}
         className={`relative bg-white rounded-xl border border-slate-200 p-3 min-h-[8rem] ${sticky ? "lg:sticky lg:top-16 scroll-mt-16" : ""} ${className}`}>
      {onClose && (
        <button onClick={onClose} aria-label={closeLabel} title={closeLabel}
                className="absolute top-2 end-2 size-7 rounded-lg text-slate-400 hover:text-slate-700 hover:bg-slate-100 text-lg leading-none">×</button>
      )}
      {children}
    </div>
  );
});

/** Encabezado del detalle: puntito + qué es (renglón chico), el título y, si lo hay, un VALOR a la
 *  derecha del título (el panel de la transacción pone ahí su monto, 19-oct). */
export function DetailHeader({ dot, kicker, kickerTone = "text-slate-600", title, sub, amount }: {
  dot?: string; kicker?: ReactNode; kickerTone?: string; title: ReactNode; sub?: ReactNode; amount?: ReactNode;
}) {
  return (
    <div className="pe-7">
      {kicker && (
        <div className={`flex items-center gap-1.5 text-xs ${kickerTone}`}>
          {dot && <span className={`w-2 h-2 rounded-full ${dot}`} />}
          <span>{kicker}</span>
        </div>
      )}
      <div className="flex items-start justify-between gap-2 mt-0.5">
        <h3 className="min-w-0 truncate text-sm font-semibold text-slate-900">{title}</h3>
        {amount && <div className="shrink-0">{amount}</div>}
      </div>
      {sub && <p className="text-xs text-slate-500">{sub}</p>}
    </div>
  );
}

/** Una sección del detalle, separada por una línea fina. */
export function DetailSection({ title, children, right }: { title?: ReactNode; children: ReactNode; right?: ReactNode }) {
  return (
    <section className="border-t border-slate-100 pt-2 space-y-1.5">
      {(title || right) && (
        <div className="flex items-center gap-2">
          {title && <h4 className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">{title}</h4>}
          {right && <div className="ms-auto">{right}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

export function DetailEmpty({ text }: { text: string }) {
  return <p className="text-sm text-slate-400">{text}</p>;
}
