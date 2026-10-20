import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { BookNoteError, createBookNote, type BookNote, type BookNoteInput, type BookNoteKind, type BookNotePriority } from "../api";
import { openNoteNext } from "./NotesPage";
import { BTN_GHOST, BTN_PRIMARY } from "./PageKit";

// UNA NOTA RÁPIDA, SIN SALIR DE DONDE SE TE OCURRIÓ (FASE 11, Auditoría / IA · 19-oct-2026).
// Pedido de Jose: "cuando estoy trabajando en Movimientos es que se me ocurren las notas". Mismo editor
// que la página de Notas (texto · 📌/🚫 · comercio opcional, PRELLENABLE · fechas opcionales · prioridad),
// en una ventana que se abre encima de lo que estés haciendo — como `RuleDialog` (portal + Escape + clic
// afuera), para que viva sobre la Hoja sin que RevoGrid la tape ni le robe el foco.
// "Guardar" crea la nota con la MISMA API que la página de Notas (`createBookNote`); "Abrir en Notas →"
// lleva a verla completa (usa `openNoteNext`, la misma costura que "+ Nueva nota" del inicio).
// 🔴 Una nota nunca categoriza: solo marca filas para que el bookkeeper las mire, o calla hallazgos.

type Draft = { kind: BookNoteKind; priority: BookNotePriority; text: string; merchant: string; valid_from: string; valid_to: string };

export default function NoteQuickAdd({ merchant, onClose, onSaved }: {
  /** Comercio para PRELLENAR el campo (p. ej. el de la fila donde se pidió la nota); el bookkeeper lo puede borrar o cambiar. */
  merchant?: string | null;
  onClose: () => void;
  /** Tras guardar, por si quien abrió la ventana necesita refrescar algo (p. ej. la columna del puntito). */
  onSaved?: (note: BookNote) => void;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<Draft>({ kind: "keep_in_mind", priority: "normal", text: "", merchant: merchant ?? "", valid_from: "", valid_to: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<BookNote | null>(null);

  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } };
    document.addEventListener("keydown", esc, true);
    return () => document.removeEventListener("keydown", esc, true);
  }, [onClose]);

  async function save() {
    const text = draft.text.trim();
    if (!text || busy) return;
    setBusy(true); setError(null);
    try {
      const body: BookNoteInput = { kind: draft.kind, priority: draft.priority, text,
        merchant: draft.merchant.trim() || null, valid_from: draft.valid_from || null, valid_to: draft.valid_to || null };
      const note = await createBookNote(body);
      setSaved(note);
      onSaved?.(note);
    } catch (e) {
      setError(e instanceof BookNoteError ? t(`notes.error.${e.code}`, { defaultValue: t("notesQuick.error") }) : t("notesQuick.error"));
    } finally { setBusy(false); }
  }

  const input = "w-full border border-slate-200 rounded-lg px-2 py-1.5 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-emerald-200";

  return createPortal(
    <div onMouseDown={onClose} onMouseUp={(e) => e.preventDefault()}
         className="fixed inset-0 bg-slate-900/30 flex items-start justify-center p-4 overflow-y-auto" style={{ zIndex: 2500, paddingTop: "8vh" }}>
      <div onMouseDown={(e) => e.stopPropagation()} role="dialog" aria-label={t("notesQuick.title")}
           className="bg-white w-full max-w-md rounded-xl shadow-2xl p-4 space-y-3">
        {saved ? (
          <div className="space-y-2.5">
            <p className="text-sm text-emerald-800 font-medium">
              {saved.kind === "dont_report" ? t("notesQuick.savedDont") : t("notesQuick.saved")}
            </p>
            {saved.kind === "keep_in_mind" && <p className="text-xs text-slate-500">{t("notesQuick.willRead")}</p>}
            <div className="flex items-center gap-2 pt-1">
              <button className={BTN_PRIMARY} onClick={onClose}>{t("notesQuick.done")}</button>
              <button className={BTN_GHOST} onClick={() => { openNoteNext(saved.id); onClose(); }}>{t("notesQuick.openInNotes")}</button>
            </div>
          </div>
        ) : (
          <>
            <div className="flex items-start justify-between gap-2">
              <h3 className="text-sm font-semibold text-slate-900">{t("notesQuick.title")}</h3>
              <button onClick={onClose} aria-label={t("notesQuick.close")} className="text-slate-400 hover:text-slate-700 text-lg leading-none">×</button>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {(["keep_in_mind", "dont_report"] as const).map((k) => (
                <button key={k} onClick={() => setDraft({ ...draft, kind: k })}
                        className={`px-2.5 py-1 rounded-lg text-xs border ${draft.kind === k ? "border-emerald-600 bg-emerald-50 text-emerald-900 font-medium" : "border-slate-200 text-slate-600 hover:bg-slate-50"}`}>
                  {k === "dont_report" ? "🚫" : "📌"} {t(`notes.kind.${k}`)}
                </button>
              ))}
              <label className="ms-auto flex items-center gap-1.5 text-xs text-slate-600">
                <input type="checkbox" checked={draft.priority === "high"}
                       onChange={(e) => setDraft({ ...draft, priority: e.target.checked ? "high" : "normal" })} />
                {t("notesPage.highPriority")}
              </label>
            </div>
            <textarea autoFocus className={`${input} leading-relaxed`} rows={4} maxLength={1000} value={draft.text}
                      placeholder={t(draft.kind === "dont_report" ? "notesPage.placeholder.dont_report" : "notes.textPlaceholder")}
                      onChange={(e) => setDraft({ ...draft, text: e.target.value })} />
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
              <label className="text-[11px] text-slate-500 space-y-0.5">
                <span>{t("notes.merchantLabel")}</span>
                <input className={`${input} font-mono`} maxLength={80} value={draft.merchant} placeholder={t("notes.merchantPlaceholder")}
                       onChange={(e) => setDraft({ ...draft, merchant: e.target.value })} />
              </label>
              <label className="text-[11px] text-slate-500 space-y-0.5">
                <span>{t("notes.fromLabel")}</span>
                <input type="date" className={input} value={draft.valid_from} onChange={(e) => setDraft({ ...draft, valid_from: e.target.value })} />
              </label>
              <label className="text-[11px] text-slate-500 space-y-0.5">
                <span>{t("notes.toLabel")}</span>
                <input type="date" className={input} value={draft.valid_to} onChange={(e) => setDraft({ ...draft, valid_to: e.target.value })} />
              </label>
            </div>
            {error && <p className="text-sm text-rose-700 bg-rose-50 rounded-lg px-3 py-2">{error}</p>}
            <div className="flex items-center justify-end gap-2 pt-1">
              <button className={BTN_GHOST} disabled={busy} onClick={onClose}>{t("notes.cancel")}</button>
              <button className={BTN_PRIMARY} disabled={busy || !draft.text.trim()} onClick={save}>
                {busy ? t("notes.saving") : t("notes.save")}
              </button>
            </div>
          </>
        )}
      </div>
    </div>,
    document.body,
  );
}
