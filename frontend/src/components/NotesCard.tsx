import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { getBookNotes, type BookNote } from "../api";
import { firstLine, openNewNoteNext, useNoteText } from "./NotesPage";

// LA TARJETA "NOTAS DEL LIBRO" DEL INICIO (FASE 11, Auditoría / IA · ronda de diseño 2, 18-oct-2026).
// Un resumen: las notas activas, una línea con lo que entendió la IA de cada una, "+ Nueva nota" y "Abrir
// notas". El clic lleva a la página de Notas. La monta Firm en `Dashboard.tsx` (su hueco marcado).
// El rol CLIENT no puede leer notas (el backend le niega la ruta): sin permiso o con error, la tarjeta no se
// muestra — nada de mensajes de error en el inicio del cliente.

const MAX_SHOWN = 4;

export default function NotesCard({ onOpen }: { onOpen: () => void }) {
  const { t } = useTranslation();
  const { short } = useNoteText();
  const [notes, setNotes] = useState<BookNote[] | null | "hidden">(null);
  useEffect(() => { getBookNotes().then(setNotes).catch(() => setNotes("hidden")); }, []);
  if (notes === "hidden") return null;

  const active = (notes ?? []).filter((n) => !n.paused_at)
    .sort((a, b) => Number(b.priority === "high") - Number(a.priority === "high") || b.id - a.id);
  const paused = (notes ?? []).length - active.length;

  return (
    <section className="bg-white rounded-xl border border-slate-200">
      <div className="px-3 py-2 flex items-center gap-2 border-b border-slate-100">
        <button onClick={onOpen} className="text-sm font-semibold text-slate-900 hover:text-emerald-800">{t("notes.title")}</button>
        <span className="text-xs text-slate-400">
          {notes === null ? "" : [t("notesPage.count", { count: active.length }), ...(paused ? [t("notesPage.pausedCount", { count: paused })] : [])].join(" · ")}
        </span>
        <button onClick={onOpen} className="ms-auto text-xs font-semibold text-emerald-800 hover:underline">{t("notesPage.card.open")}</button>
      </div>
      {notes === null ? (
        <p className="px-3 py-3 text-sm text-slate-400">{t("notes.loading")}</p>
      ) : active.length === 0 ? (
        <p className="px-3 py-3 text-sm text-slate-500">{t("notes.emptyShort")}</p>
      ) : (
        <ul className="divide-y divide-slate-100">
          {active.slice(0, MAX_SHOWN).map((n) => {
            const s = short(n);
            return (
              <li key={n.id}>
                <button onClick={onOpen} className="w-full text-start px-3 py-2 hover:bg-slate-50 flex items-start gap-2">
                  {/* El emoji fuera del texto truncado (Chromium no pintaba la línea con él dentro, 18-oct). */}
                  <span className="text-sm shrink-0">{n.kind === "dont_report" ? "🚫" : "📌"}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm text-slate-800 truncate">{firstLine(n.text)}</span>
                    <span className={`block text-xs truncate ${s.ok ? "text-slate-500" : "text-amber-700"}`}>
                      {s.ok && n.kind === "keep_in_mind" ? "✓ " : ""}{s.text}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
          {active.length > MAX_SHOWN && (
            <li className="px-3 py-1.5 text-xs text-slate-400">{t("notesPage.card.more", { count: active.length - MAX_SHOWN })}</li>
          )}
        </ul>
      )}
      <div className="px-3 py-2 border-t border-slate-100">
        <button onClick={() => { openNewNoteNext(); onOpen(); }} className="text-xs font-semibold text-emerald-800 hover:underline">{t("notes.add")}</button>
      </div>
    </section>
  );
}
