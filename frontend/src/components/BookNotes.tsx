import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  BookNoteError, createBookNote, deleteBookNote, getBookNotes, updateBookNote,
  type BookNote, type BookNoteInput,
} from "../api";

// LAS NOTAS DEL LIBRO (FASE 11): lo que el bookkeeper le escribe al agente sobre ESTE cliente.
// 📌 tenlo en cuenta (para que encuentre más) · 🚫 no me reportes (para corregirlo).
// Una nota nunca categoriza: el agente ④ las lee y PROPONE cambios a la fórmula, que tú apruebas.
// Vive arriba en la pestaña Auditoría: es la entrada de los agentes.

const EMPTY: BookNoteInput = {
  kind: "keep_in_mind", priority: "normal", text: "", merchant: "", valid_from: "", valid_to: "",
};

function fmtDay(iso: string, lang: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(lang, { day: "numeric", month: "short", year: "numeric" });
}

export default function BookNotes({ compact = false, onProposeForNote }: {
  compact?: boolean;
  // 14-oct ("los agentes, separados"): al guardar una nota 📌, ofrecer que el ④ proponga filas SOLO para
  // ella. Devuelve cuántas filas propuso.
  onProposeForNote?: (noteId: number) => Promise<number>;
}) {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language || "en").split("-")[0];
  const [notes, setNotes] = useState<BookNote[] | null>(null);
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const [draft, setDraft] = useState<BookNoteInput>(EMPTY);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [offer, setOffer] = useState<{ id: number; state: "ask" | "running" | "done"; count?: number } | null>(null);

  const errText = (e: unknown) =>
    e instanceof BookNoteError
      ? t(`notes.error.${e.code}`, { defaultValue: t("notes.error.notes_error") })
      : t("notes.error.notes_error");

  async function load() {
    try { setNotes(await getBookNotes()); } catch (e) { setError(errText(e)); setNotes([]); }
  }
  useEffect(() => { void load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  function startNew() { setDraft(EMPTY); setEditing("new"); setError(null); }
  function startEdit(n: BookNote) {
    setDraft({
      kind: n.kind, priority: n.priority, text: n.text, merchant: n.merchant ?? "",
      valid_from: n.valid_from ?? "", valid_to: n.valid_to ?? "",
    });
    setEditing(n.id); setError(null);
  }

  async function save() {
    // "" → null: el backend entiende "sin comercio / sin vigencia".
    const body: BookNoteInput = {
      ...draft,
      merchant: draft.merchant?.trim() || null,
      valid_from: draft.valid_from || null,
      valid_to: draft.valid_to || null,
    };
    setSaving(true); setError(null);
    try {
      let saved: BookNote | null = null;
      if (editing === "new") saved = await createBookNote(body);
      else if (typeof editing === "number") saved = await updateBookNote(editing, body);
      setEditing(null);
      if (saved && saved.kind === "keep_in_mind" && onProposeForNote) setOffer({ id: saved.id, state: "ask" });
      await load();
    } catch (e) {
      setError(errText(e));
    } finally {
      setSaving(false);
    }
  }

  async function remove(n: BookNote) {
    if (!window.confirm(t("notes.confirmDelete"))) return;
    setError(null);
    try { await deleteBookNote(n.id); await load(); } catch (e) { setError(errText(e)); }
  }

  const input = "w-full border border-slate-200 rounded-lg px-2 py-1.5 text-sm bg-white";

  const form = (
    <div className="border border-emerald-200 bg-emerald-50/40 rounded-xl p-3 space-y-2">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        <label className="text-xs text-slate-600 space-y-1">
          <span>{t("notes.kindLabel")}</span>
          <select className={input} value={draft.kind}
                  onChange={(e) => setDraft({ ...draft, kind: e.target.value as BookNoteInput["kind"] })}>
            <option value="keep_in_mind">📌 {t("notes.kind.keep_in_mind")}</option>
            <option value="dont_report">🚫 {t("notes.kind.dont_report")}</option>
          </select>
        </label>
        <label className="text-xs text-slate-600 space-y-1">
          <span>{t("notes.priorityLabel")}</span>
          <select className={input} value={draft.priority}
                  onChange={(e) => setDraft({ ...draft, priority: e.target.value as BookNoteInput["priority"] })}>
            <option value="high">{t("notes.priority.high")}</option>
            <option value="normal">{t("notes.priority.normal")}</option>
          </select>
        </label>
      </div>
      <label className="text-xs text-slate-600 space-y-1 block">
        <span>{t("notes.textLabel")}</span>
        <textarea className={input} rows={3} maxLength={1000} value={draft.text ?? ""}
                  placeholder={t("notes.textPlaceholder")}
                  onChange={(e) => setDraft({ ...draft, text: e.target.value })} />
      </label>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
        <label className="text-xs text-slate-600 space-y-1">
          <span>{t("notes.merchantLabel")}</span>
          <input className={input} maxLength={80} value={draft.merchant ?? ""}
                 placeholder={t("notes.merchantPlaceholder")}
                 onChange={(e) => setDraft({ ...draft, merchant: e.target.value })} />
        </label>
        <label className="text-xs text-slate-600 space-y-1">
          <span>{t("notes.fromLabel")}</span>
          <input type="date" className={input} value={draft.valid_from ?? ""}
                 onChange={(e) => setDraft({ ...draft, valid_from: e.target.value })} />
        </label>
        <label className="text-xs text-slate-600 space-y-1">
          <span>{t("notes.toLabel")}</span>
          <input type="date" className={input} value={draft.valid_to ?? ""}
                 onChange={(e) => setDraft({ ...draft, valid_to: e.target.value })} />
        </label>
      </div>
      <div className="flex gap-2 justify-end">
        <button onClick={() => { setEditing(null); setError(null); }} disabled={saving}
                className="px-3 py-1.5 rounded-lg text-sm text-slate-600 hover:bg-slate-100">
          {t("notes.cancel")}
        </button>
        <button onClick={save} disabled={saving || !(draft.text ?? "").trim()}
                className="px-3 py-1.5 rounded-lg text-sm font-semibold text-white bg-emerald-700 disabled:opacity-50">
          {saving ? t("notes.saving") : t("notes.save")}
        </button>
      </div>
    </div>
  );

  // Compacta (pestaña Auditoría, 8-oct): sin notas, una sola línea; nada de bloques grandes vacíos.
  const empty = notes !== null && notes.length === 0 && editing === null;
  if (compact && empty) {
    return (
      <div className="bg-white rounded-xl border border-slate-200 px-3 py-2 flex items-center gap-3 text-sm">
        <span className="font-medium text-slate-800 shrink-0">{t("notes.title")}</span>
        <span className="text-xs text-slate-400 truncate">{t("notes.emptyShort")}</span>
        {error && <span className="text-xs text-rose-700">{error}</span>}
        <button onClick={startNew} className="ms-auto shrink-0 px-2.5 py-1 rounded-lg text-xs font-semibold text-emerald-800 hover:bg-emerald-50">
          {t("notes.add")}
        </button>
      </div>
    );
  }

  return (
    <div className={compact ? "bg-white rounded-xl border border-slate-200 px-3 py-2.5 space-y-2"
                            : "bg-white rounded-2xl shadow-sm border border-slate-100 p-4 space-y-3"}>
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          <h2 className={compact ? "text-sm font-semibold text-slate-900" : "font-semibold text-slate-900"}>{t("notes.title")}</h2>
          {!compact && <p className="text-sm text-slate-500">{t("notes.subtitle")}</p>}
        </div>
        {editing === null && (
          <button onClick={startNew}
                  className={compact ? "px-2.5 py-1 rounded-lg text-xs font-semibold text-emerald-800 hover:bg-emerald-50 shrink-0"
                                     : "px-4 py-2 rounded-xl font-semibold text-white shadow-sm shrink-0"}
                  style={compact ? undefined : { background: "linear-gradient(135deg, #15803d, #f4b400)" }}>
            {t("notes.add")}
          </button>
        )}
      </div>

      {error && <p className="text-sm text-rose-700 bg-rose-50 rounded-lg px-3 py-2">{error}</p>}
      {offer && onProposeForNote && (
        <div className="flex flex-wrap items-center gap-2 text-xs bg-emerald-50/60 border border-emerald-100 rounded-lg px-3 py-2">
          {offer.state === "ask" && <>
            <span className="text-slate-700">{t("auditView.noteOffer.ask")}</span>
            <span className="text-slate-400">{t("auditView.est.calls", { count: 1 })}</span>
            <button className="ms-auto px-2.5 py-1 rounded-lg font-semibold text-white bg-emerald-700"
                    onClick={async () => {
                      setOffer({ ...offer, state: "running" });
                      try { setOffer({ ...offer, state: "done", count: await onProposeForNote(offer.id) }); }
                      catch (e) { setOffer(null); setError(errText(e)); }
                    }}>
              {t("auditView.noteOffer.propose")}
            </button>
            <button className="px-2.5 py-1 rounded-lg text-slate-600 hover:bg-slate-100" onClick={() => setOffer(null)}>
              {t("auditView.noteOffer.later")}
            </button>
          </>}
          {offer.state === "running" && <span className="text-slate-600">{t("auditView.noteOffer.running")}</span>}
          {offer.state === "done" && <>
            <span className="text-slate-700">{offer.count ? t("auditView.noteOffer.done", { count: offer.count }) : t("auditView.noteOffer.none")}</span>
            <button className="ms-auto px-2 py-0.5 rounded text-slate-500 hover:bg-slate-100" onClick={() => setOffer(null)}>✕</button>
          </>}
        </div>
      )}
      {editing === "new" && form}

      {notes === null ? (
        <p className="text-sm text-slate-400">{t("notes.loading")}</p>
      ) : notes.length === 0 && editing !== "new" ? (
        <p className="text-sm text-slate-500 border border-dashed border-slate-200 rounded-xl px-3 py-4 text-center">
          {t("notes.empty")}
        </p>
      ) : (
        <ul className="divide-y divide-slate-100">
          {notes.map((n) => (
            <li key={n.id} className="py-2.5">
              {editing === n.id ? form : (
                <div className="flex items-start gap-3">
                  <span className="text-lg leading-6 shrink-0" title={t(`notes.kind.${n.kind}`)}>
                    {n.kind === "dont_report" ? "🚫" : "📌"}
                  </span>
                  <div className="min-w-0 flex-1 space-y-1">
                    <p className="text-sm text-slate-800 whitespace-pre-wrap break-words">{n.text}</p>
                    <div className="flex flex-wrap gap-1.5 text-xs">
                      <span className="px-2 py-0.5 rounded-full bg-slate-100 text-slate-600">
                        {t(`notes.kind.${n.kind}`)}
                      </span>
                      {n.priority === "high" && (
                        <span className="px-2 py-0.5 rounded-full bg-amber-100 text-amber-800 font-medium">
                          {t("notes.priority.high")}
                        </span>
                      )}
                      {n.merchant && (
                        <span className="px-2 py-0.5 rounded-full bg-sky-50 text-sky-800 font-mono">{n.merchant}</span>
                      )}
                      {(n.valid_from || n.valid_to) && (
                        <span className="px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-800">
                          {n.valid_from ? fmtDay(n.valid_from, lang) : "…"} → {n.valid_to ? fmtDay(n.valid_to, lang) : "…"}
                        </span>
                      )}
                    </div>
                  </div>
                  {editing === null && (
                    <div className="flex gap-1 shrink-0">
                      <button onClick={() => startEdit(n)}
                              className="px-2 py-1 rounded-lg text-xs text-slate-600 hover:bg-slate-100">
                        {t("notes.edit")}
                      </button>
                      <button onClick={() => remove(n)}
                              className="px-2 py-1 rounded-lg text-xs text-rose-700 hover:bg-rose-50">
                        {t("notes.delete")}
                      </button>
                    </div>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
