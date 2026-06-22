import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { Company } from "../types";

// Selector de empresa en el header (gestionar varias empresas desde una cuenta).
// Toca el nombre → lista de empresas (cambiar) + "Agregar empresa".
export default function CompanySwitcher({ companies, activeId, onSwitch, onAdd }: {
  companies: Company[];
  activeId: number;
  onSwitch: (c: Company) => void;
  onAdd: () => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const active = companies.find((c) => c.id === activeId);

  return (
    <div className="relative flex-1 min-w-0">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1 max-w-full text-start"
      >
        <span className="text-xs text-slate-500 truncate">{active?.name ?? "…"}</span>
        <svg viewBox="0 0 24 24" className="size-3.5 text-slate-400 shrink-0" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>

      {open && (
        <>
          {/* Capa para cerrar al tocar afuera */}
          <div className="fixed inset-0 z-20" onClick={() => setOpen(false)} />
          <div className="absolute z-30 mt-1 w-60 rounded-xl border border-slate-200 bg-white shadow-lg overflow-hidden">
            <div className="px-3 py-2 text-[10px] uppercase tracking-wide text-slate-400 bg-slate-50">
              {t("company.yours", { defaultValue: "Your businesses" })}
            </div>
            <div className="max-h-64 overflow-auto">
              {companies.map((c) => (
                <button
                  key={c.id}
                  onClick={() => { setOpen(false); if (c.id !== activeId) onSwitch(c); }}
                  className={`w-full flex items-center justify-between gap-2 px-3 py-2.5 text-start hover:bg-slate-50 ${
                    c.id === activeId ? "bg-brand-green/5" : ""
                  }`}
                >
                  <span className="text-sm text-slate-800 truncate">{c.name}</span>
                  {c.id === activeId && (
                    <svg viewBox="0 0 24 24" className="size-4 text-brand-green shrink-0" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M5 13l4 4L19 7" />
                    </svg>
                  )}
                </button>
              ))}
            </div>
            <button
              onClick={() => { setOpen(false); onAdd(); }}
              className="w-full flex items-center gap-2 px-3 py-2.5 border-t border-slate-100 text-brand-green font-medium hover:bg-brand-green/5"
            >
              <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 5v14M5 12h14" />
              </svg>
              <span className="text-sm">{t("company.add", { defaultValue: "Add business" })}</span>
            </button>
          </div>
        </>
      )}
    </div>
  );
}
