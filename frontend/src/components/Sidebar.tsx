import { useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { signOut } from "firebase/auth";
import { auth } from "../firebase";
import CompanySwitcher from "./CompanySwitcher";
import LanguageSwitcher from "./LanguageSwitcher";
import type { Company } from "../types";

// Las vistas de UN libro (la empresa abierta). Es la fuente del tipo `View` que usa App.tsx.
// Las reglas NO tienen pestaña propia: viven dentro de Movimientos / Transactions (Bookkeeping · Reglas · Pivote). Jose, 30-sep.
export type View = "dashboard" | "transactions" | "audit" | "upload" | "pnl" | "settings";

// BARRA LATERAL (FASE 13). Dos niveles, que es como trabaja un bookkeeper — y como está el modelo:
//   · ESTE LIBRO → la empresa que está abierta ahora (la capa `Company`)
//   · OFICINA    → herramientas para TODOS los clientes (la capa `Firm`) — escondida hasta que exista
//
// En escritorio vive COMPRIMIDA (solo íconos, 64 px) y se EXPANDE al acercar el mouse (pedido de
// Jose, 30-sep). Se abre ENCIMA del contenido, no lo empuja: si lo empujara, la hoja se redibujaría
// cada vez que el mouse pasa por el borde. El contenido siempre empieza a 64 px.

const ic = (d: ReactNode) => (
  <svg viewBox="0 0 24 24" className="size-[18px] shrink-0" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    {d}
  </svg>
);

const BOOK_ICONS: Record<View, ReactNode> = {
  dashboard: ic(<><rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" /></>),
  transactions: ic(<><line x1="8" y1="7" x2="20" y2="7" /><line x1="8" y1="12" x2="20" y2="12" /><line x1="8" y1="17" x2="20" y2="17" /><circle cx="4" cy="7" r="1" /><circle cx="4" cy="12" r="1" /><circle cx="4" cy="17" r="1" /></>),
  audit: ic(<><rect x="5" y="4" width="14" height="17" rx="2" /><path d="M9 4h6v3H9z" /><path d="M9 13l2 2 4-4" /></>),
  pnl: ic(<><path d="M4 19V5M4 19h16" /><path d="M8 16v-5M12 16V8M16 16v-3" /></>),
  upload: ic(<><path d="M3 10l9-6 9 6M5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 21h18" /></>), // "Bancos" (la clave sigue siendo upload)
  settings: ic(<><circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1" /></>),
};

// La sección OFICINA (Clientes, Usuarios, Calendario, Solicitudes, Nómina) se ESCONDIÓ el 3-oct: eran
// marcadores "Pronto" y el demo del concurso no puede llevar a páginas vacías (docs/DEMO Y ENTREGA.md).
// No se construyen antes del 30-oct. Cuando exista la primera, vuelve aquí con su sección.

// Orden (Jose, 4-oct): Resumen → Bancos → Movimientos → Auditoría → P&L → Ajustes.
const BOOK_ORDER: View[] = ["dashboard", "upload", "transactions", "audit", "pnl", "settings"];

// Retardos del hover: abrir con una pausa corta evita que la barra salte cuando el mouse solo CRUZA
// el borde camino a otra cosa; cerrar con otra pausa evita que se cierre por un temblor del mouse.
const OPEN_DELAY_MS = 120;
const CLOSE_DELAY_MS = 220;

export function viewLabel(t: (k: string) => string, v: View): string {
  return v === "settings" ? t("shell.settings") : t(`nav.${v}`);
}

function initials(name: string | undefined): string {
  const parts = (name ?? "").trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase() || "·";
}

export default function Sidebar({
  view, onNavigate, company, companies, onSwitch, onAdd, reviewCount, auditCount, open, onClose,
}: {
  view: View;
  onNavigate: (v: View) => void;
  company: Company | null;
  companies: Company[];
  onSwitch: (c: Company) => void;
  onAdd: () => void;
  reviewCount: number;   // pendientes de confirmar (badge rojo)
  auditCount: number;    // hallazgos del auditor (badge ámbar)
  open: boolean;         // solo en pantallas chicas: la barra entra como cajón
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [hover, setHover] = useState(false);
  const timer = useRef<number | null>(null);
  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);

  const later = (value: boolean, ms: number) => {
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setHover(value), ms);
  };

  // `expanded` decide si se pintan los TEXTOS. Comprimida no se pinta ninguno: así nada se desborda
  // de los 64 px ni crea una zona invisible que abra la barra al pasar el mouse por el contenido.
  const body = (expanded: boolean) => (
    <div className="h-full flex flex-col bg-white">
      {/* Marca: mismo alto que la barra de arriba (h-14). El logo no se mueve al expandir. */}
      <div className="h-14 shrink-0 flex items-center gap-2.5 px-4 border-b border-slate-200 overflow-hidden">
        <img src="/icon.svg" alt="ContaBu" className="size-8 shrink-0 rounded-lg shadow-sm" />
        {expanded && (
          <div className="font-bold tracking-tight whitespace-nowrap">
            <span className="text-brand-amber">Conta</span>
            <span className="text-brand-green">Bu</span>
          </div>
        )}
        {expanded && (
          <button onClick={onClose} aria-label={t("shell.close")} className="ms-auto md:hidden p-1 text-slate-400 hover:text-slate-600">✕</button>
        )}
      </div>

      {/* El CLIENTE abierto, fijo arriba y FUERA del área con scroll (su menú flotante se recortaría). */}
      <div className="shrink-0 h-[60px] px-4 flex items-center border-b border-slate-200">
        {expanded ? (
          <div className="min-w-0 w-full">
            <div className="text-[10px] uppercase tracking-wide text-slate-400 mb-0.5 whitespace-nowrap">{t("shell.client")}</div>
            {company && companies.length > 0 ? (
              <CompanySwitcher companies={companies} activeId={company.id} onSwitch={onSwitch} onAdd={onAdd} />
            ) : (
              <div className="text-sm text-slate-500 truncate">{company?.name ?? "…"}</div>
            )}
          </div>
        ) : (
          <div title={company?.name} className="size-8 shrink-0 rounded-lg bg-brand-green/10 text-brand-green text-[11px] font-bold flex items-center justify-center">
            {initials(company?.name)}
          </div>
        )}
      </div>

      <nav className="flex-1 overflow-y-auto overflow-x-hidden px-2 py-3 space-y-4">
        <Section label={t("shell.book")} expanded={expanded}>
          {BOOK_ORDER.map((v) => {
            const active = view === v;
            const audit = v === "audit" ? auditCount : 0;
            const review = v === "audit" ? reviewCount : 0;
            return (
              <button
                key={v}
                onClick={() => { onNavigate(v); onClose(); }}
                aria-label={viewLabel(t, v)}
                className={`w-full h-9 flex items-center gap-2.5 ps-[15px] pe-2.5 rounded-lg text-sm text-start transition-colors ${
                  active ? "bg-brand-green/10 text-brand-green font-semibold" : "text-slate-600 hover:bg-slate-50"
                }`}
              >
                <span className="relative">
                  {BOOK_ICONS[v]}
                  {/* Comprimida: un punto en el ícono avisa que hay algo, sin espacio para el número. */}
                  {!expanded && (audit > 0 || review > 0) && (
                    <span className={`absolute -top-1 -end-1 size-2 rounded-full ring-2 ring-white ${review > 0 ? "bg-rose-500" : "bg-amber-500"}`} />
                  )}
                </span>
                {expanded && <span className="flex-1 truncate whitespace-nowrap">{viewLabel(t, v)}</span>}
                {expanded && audit > 0 && (
                  <span className="bg-amber-500 text-white text-[10px] leading-none rounded-full px-1.5 py-0.5">{audit}</span>
                )}
                {expanded && review > 0 && (
                  <span className="bg-rose-500 text-white text-[10px] leading-none rounded-full px-1.5 py-0.5">{review}</span>
                )}
              </button>
            );
          })}
        </Section>

      </nav>

      <div className="shrink-0 h-12 border-t border-slate-200 px-3 flex items-center gap-2 overflow-hidden">
        {expanded && <LanguageSwitcher />}
        <button
          onClick={() => signOut(auth)}
          aria-label={t("login.logout")}
          title={t("login.logout")}
          className={`flex items-center gap-1.5 text-sm text-slate-500 hover:text-rose-500 py-1 rounded-lg whitespace-nowrap ${
            expanded ? "ms-auto px-2" : "ps-[11px]"
          }`}
        >
          {ic(<><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><path d="M16 17l5-5-5-5" /><path d="M21 12H9" /></>)}
          {expanded && t("login.logout")}
        </button>
      </div>
    </div>
  );

  return (
    <>
      {/* ESCRITORIO: un hueco fijo de 64 px en el flujo (el contenido nunca se mueve) y, encima, la
          barra fija que se ensancha a 240 px al acercar el mouse — o al llegar con el teclado. */}
      <div className="hidden md:block w-16 shrink-0">
        <aside
          onMouseEnter={() => later(true, OPEN_DELAY_MS)}
          onMouseLeave={() => later(false, CLOSE_DELAY_MS)}
          onFocus={() => later(true, 0)}
          onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) later(false, 0); }}
          className={`fixed inset-y-0 start-0 z-30 border-e border-slate-200 bg-white transition-[width,box-shadow] duration-200 ease-out ${
            hover ? "w-60 shadow-xl" : "w-16"
          }`}
        >
          {body(hover)}
        </aside>
      </div>

      {/* CELULAR: cajón completo que entra desde la izquierda (no hay mouse que acercar). */}
      {open && (
        <div className="md:hidden fixed inset-0 z-40">
          <div className="absolute inset-0 bg-slate-900/30" onClick={onClose} />
          <aside className="absolute inset-y-0 start-0 w-64 shadow-xl">{body(true)}</aside>
        </div>
      )}
    </>
  );
}

// Encabezado de sección. Mantiene el MISMO alto expandida o no: comprimida muestra una raya en vez del
// texto, así los íconos no saltan de arriba a abajo al abrir la barra.
function Section({ label, expanded, children }: { label: string; expanded: boolean; children: ReactNode }) {
  return (
    <div>
      <div className="h-5 mb-1 flex items-center px-2.5">
        {expanded ? (
          <span className="text-[10.5px] font-semibold uppercase tracking-wider text-slate-400 whitespace-nowrap">{label}</span>
        ) : (
          <span className="block w-full h-px bg-slate-200" />
        )}
      </div>
      <div className="space-y-0.5">{children}</div>
    </div>
  );
}
