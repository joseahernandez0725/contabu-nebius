import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  BookNoteError, createBookNote, deleteBookNote, getBookNoteEffects, getBookNotes, money, previewBookNote,
  setBookNotePaused, updateBookNote,
  type BookNote, type BookNoteInput, type NotePreview, type NoteTouched, type NoteUnderstood, type TxSummary,
} from "../api";
import {
  BTN_GHOST, BTN_PRIMARY, BTN_SECONDARY, BTN_SMALL_PRIMARY, Badge, DetailEmpty, DetailHeader, DetailSection, EmptyRow,
  ListCard, ListDetail, ListRow, PageHeader, secsSince, agoText,
} from "./PageKit";
import { LiveOnly } from "./DemoDoors";   // visita guiada del demo (Firm, 23-oct)

// LAS NOTAS DEL LIBRO, CON PÁGINA PROPIA (FASE 11, Auditoría / IA · ronda de diseño 2, 18-oct-2026).
// La nota es "el prompt que escribe el bookkeeper": lo que la IA debe saber de ESTE cliente. Vive entre
// Movimientos (los datos) y Auditoría (lo que hace la IA). Lista + detalle (`PageKit`), con 4 herramientas:
//   · qué entendió la IA (el chequeo del ⑥ traductor, `audit_check`)
//   · qué movimientos tocó (lo que marcó 📌 y lo que calla 🚫), con "Ver en Bookkeeping" a la fila
//   · probarla ANTES de guardarla (`POST /notes/preview`, dice antes lo que cuesta)
//   · pausarla (ningún agente la lee ni el código la aplica; el texto se queda)
// 🔴 Una nota nunca categoriza: solo señala filas para que el bookkeeper las mire, o calla hallazgos.
// (Antes vivía dentro de Auditoría como `BookNotes.tsx`, borrado en este cambio.)

type Draft = Required<{ [K in keyof BookNoteInput]: NonNullable<BookNoteInput[K]> }>;
type Sel = number | "new" | null;

const EMPTY: Draft = { kind: "keep_in_mind", priority: "normal", text: "", merchant: "", valid_from: "", valid_to: "" };

function toDraft(n: BookNote): Draft {
  return { kind: n.kind, priority: n.priority, text: n.text, merchant: n.merchant ?? "",
           valid_from: n.valid_from ?? "", valid_to: n.valid_to ?? "" };
}
function toBody(d: Draft): BookNoteInput {
  // "" → null: el backend entiende "sin comercio / sin vigencia".
  return { ...d, text: d.text.trim(), merchant: d.merchant.trim() || null, valid_from: d.valid_from || null, valid_to: d.valid_to || null };
}
const sameDraft = (a: Draft, b: Draft) => (Object.keys(a) as (keyof Draft)[]).every((k) => (a[k] ?? "") === (b[k] ?? ""));
/** La primera línea de la nota: su "título" en la lista. */
export const firstLine = (text: string) => (text.split(/\r?\n/).find((l) => l.trim()) ?? text).trim();

// ── Los textos (también los usa la tarjeta del inicio, `NotesCard`) ──────────────────────────────

export function useNoteText() {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language || "en").split("-")[0];
  const fmtDay = (iso: string) => {
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(y, m - 1, d).toLocaleDateString(lang, { day: "numeric", month: "short", year: "numeric" });
  };
  const list = (items: string[]) => {
    try { return new Intl.ListFormat(lang, { type: "conjunction" }).format(items); } catch { return items.join(", "); }
  };
  const typeLabel = (bt: string) => t(`agents.webType.${bt}`, { defaultValue: bt });
  /** "Marca como posible gasto personal las salidas de más de $50." — la parte que no son listas. */
  const sentence = (u: NoteUnderstood) => t(`notesPage.u.${u.meaning === "personal" ? "personal" : "review"}`, {
    what: t(`notesPage.u.what.${u.direction}`),
    amount: u.amount_over != null ? t("notesPage.u.over", { amount: money(u.amount_over) }) : "",
  });
  /** Una línea corta para la lista y el inicio: "✓ Posible gasto personal · Supermercado, COSTCO · más de $50". */
  const short = (n: BookNote): { text: string; ok: boolean } => {
    if (n.kind === "dont_report")
      return n.merchant ? { text: t("notesPage.short.silences", { merchant: n.merchant }), ok: true }
                        : { text: t("notesPage.short.readOnly"), ok: true };
    const u = n.understood;
    if (!u) return { text: t("notesPage.short.notYet"), ok: false };
    if (!u.current) return { text: t("notesPage.short.stale"), ok: false };
    if (!u.applies) return { text: t("notesPage.short.dropped"), ok: false };
    const where = [...u.business_types.map(typeLabel), ...u.merchants];
    return { text: [t(`notesPage.short.${u.meaning === "personal" ? "personal" : "review"}`), list(where),
                    ...(u.amount_over != null ? [t("notesPage.short.over", { amount: money(u.amount_over) })] : [])].join(" · "),
             ok: true };
  };
  const period = (from?: string | null, to?: string | null) =>
    from && to ? `${fmtDay(from)} – ${fmtDay(to)}` : from ? t("notesPage.u.from", { day: fmtDay(from) })
      : to ? t("notesPage.u.until", { day: fmtDay(to) }) : "";
  return { lang, fmtDay, list, typeLabel, sentence, short, period };
}

// ── La página ────────────────────────────────────────────────────────────────────────────────────

// "+ Nueva nota" desde la tarjeta del inicio, o una nota recién creada en `NoteQuickAdd` (19-oct): la
// página abre con el editor vacío o con ESA nota ya elegida. Una marca de módulo (como `openDemoTour`)
// para que el montaje en `Dashboard.tsx` (de Firm) siga siendo de una línea.
let selOnOpen: Sel = null;
export function openNewNoteNext() { selOnOpen = "new"; }
export function openNoteNext(id: number) { selOnOpen = id; }

export default function NotesPage({ onOpenSheet, onProposeRules }: {
  onOpenSheet?: (txId: number) => void;          // "Ver en Bookkeeping" → la fila exacta (App.openInSheet)
  onProposeRules?: (noteId: number) => void;     // el ④ redacta reglas para la nota, en Auditoría (donde viven las propuestas)
}) {
  const { t } = useTranslation();
  const { lang, short } = useNoteText();
  const [notes, setNotes] = useState<BookNote[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Se lee en el inicializador y se limpia en un efecto (en modo estricto el inicializador corre dos veces).
  const [sel, setSel] = useState<Sel>(() => selOnOpen);
  useEffect(() => { selOnOpen = null; }, []);

  const errText = useCallback((e: unknown) => e instanceof BookNoteError
    ? t(`notes.error.${e.code}`, { defaultValue: t(`agents.error.${e.code}`, { defaultValue: t("notes.error.notes_error") }) })
    : t("notes.error.notes_error"), [t]);

  const load = useCallback(async () => {
    try { const ns = await getBookNotes(); setNotes(ns); setLoadError(null); return ns; }
    catch (e) { setLoadError(errText(e)); setNotes([]); return [] as BookNote[]; }
  }, [errText]);
  useEffect(() => { void load(); }, [load]);

  // Activas primero (con las de prioridad alta arriba, como las lee el agente); las pausadas al final.
  const ordered = useMemo(() => [...(notes ?? [])].sort((a, b) =>
    Number(!!a.paused_at) - Number(!!b.paused_at) || Number(b.priority === "high") - Number(a.priority === "high") || b.id - a.id), [notes]);
  const active = (notes ?? []).filter((n) => !n.paused_at).length;
  const selNote = typeof sel === "number" ? notes?.find((n) => n.id === sel) : undefined;

  return (
    <div className="space-y-3">
      <PageHeader
        meta={<span className="max-w-2xl block">{t("notesPage.lead")}</span>}
        primary={<button className={BTN_PRIMARY} onClick={() => setSel("new")} disabled={sel === "new"}>{t("notes.add")}</button>}
      />
      {loadError && <p className="text-sm text-rose-700 bg-rose-50 rounded-lg px-3 py-2">{loadError}</p>}

      <ListDetail
        wide="split"
        selKey={sel ?? undefined}
        list={
          <ListCard header={
            <div className="px-3 py-2 border-b border-slate-200 text-xs text-slate-500">
              {notes === null ? t("notes.loading") : [t("notesPage.count", { count: active }), ...(notes.length > active ? [t("notesPage.pausedCount", { count: notes.length - active })] : [])].join(" · ")}
            </div>
          }>
            {notes !== null && notes.length === 0 && sel !== "new" && <EmptyRow text={t("notes.empty")} />}
            {sel === "new" && (
              <ListRow active onClick={() => setSel("new")} dot="bg-emerald-500" title={t("notesPage.newTitle")} sub={t("notesPage.notSaved")} />
            )}
            {ordered.map((n) => {
              const s = short(n);
              const dot = n.paused_at ? "bg-slate-300" : s.ok ? "bg-emerald-500" : "bg-amber-400";
              return (
                <ListRow key={n.id} active={sel === n.id} onClick={() => setSel(n.id)} dot={dot}
                         title={firstLine(n.text)}
                         sub={n.paused_at ? t("notesPage.paused") : `${s.ok && n.kind === "keep_in_mind" ? "✓ " : ""}${s.text}`}
                         // El 📌/🚫 va aquí y no en el título: dentro del texto truncado, Chromium no pintaba la línea (18-oct).
                         right={<>
                           {n.priority === "high" && !n.paused_at && <Badge cls="bg-amber-50 text-amber-800 ring-amber-200">{t("notes.priority.high")}</Badge>}
                           <span className="text-sm" title={t(`notes.kind.${n.kind}`)}>{n.kind === "dont_report" ? "🚫" : "📌"}</span>
                         </>} />
              );
            })}
          </ListCard>
        }
        detail={
          sel === null || (sel !== "new" && !selNote) ? <Intro /> :
          <NoteDetail key={String(sel)} note={sel === "new" ? null : selNote ?? null} lang={lang} errText={errText}
                      onOpenSheet={onOpenSheet} onProposeRules={onProposeRules}
                      onSaved={async (id) => { await load(); setSel(id); }}
                      onDeleted={async () => { await load(); setSel(null); }}
                      onChanged={async () => { await load(); }}
                      onCancelNew={() => setSel(null)} />
        }
      />
    </div>
  );
}

/** Sin nota elegida: qué es una nota y los dos tipos, en pocas líneas. */
function Intro() {
  const { t } = useTranslation();
  return (
    <div className="space-y-2 text-sm text-slate-600">
      <DetailEmpty text={t("notesPage.pickOne")} />
      <p><span className="me-1">📌</span><b className="font-medium text-slate-800">{t("notes.kind.keep_in_mind")}</b> — {t("notesPage.kindHelp.keep_in_mind")}</p>
      <p><span className="me-1">🚫</span><b className="font-medium text-slate-800">{t("notes.kind.dont_report")}</b> — {t("notesPage.kindHelp.dont_report")}</p>
      <p className="text-xs text-slate-400">{t("notesPage.never")}</p>
    </div>
  );
}

// ── El detalle: la nota abierta, con sus 4 herramientas ──────────────────────────────────────────

function NoteDetail({ note, lang, errText, onOpenSheet, onProposeRules, onSaved, onDeleted, onChanged, onCancelNew }: {
  note: BookNote | null; lang: string; errText: (e: unknown) => string;
  onOpenSheet?: (txId: number) => void; onProposeRules?: (noteId: number) => void;
  onSaved: (id: number) => Promise<void>; onDeleted: () => Promise<void>; onChanged: () => Promise<void>; onCancelNew: () => void;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<Draft>(note ? toDraft(note) : EMPTY);
  const [busy, setBusy] = useState<"" | "save" | "test" | "pause" | "delete">("");
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<NotePreview | null>(null);
  const [effects, setEffects] = useState<{ flagged: NoteTouched[]; silenced: NoteTouched[] } | null>(null);
  const isNew = note === null;
  const dirty = isNew ? !!draft.text.trim() : !sameDraft(draft, toDraft(note));
  // La prueba vale para el texto con que se hizo: si se sigue escribiendo, deja de mostrarse.
  const [previewFor, setPreviewFor] = useState<Draft | null>(null);
  const showPreview = preview && previewFor && sameDraft(previewFor, draft) ? preview : null;

  const loadEffects = useCallback(() => {
    if (note) getBookNoteEffects(note.id).then(setEffects).catch(() => setEffects({ flagged: [], silenced: [] }));
  }, [note]);
  useEffect(() => { loadEffects(); }, [loadEffects]);

  // Lo que costaría PROBAR, antes: 🚫 lo aplica el código (gratis); una 📌 guardada que no cambió y que la IA ya
  // entendió, tampoco; si no, 1 llamada al traductor de notas.
  const sameMeaning = note && note.kind === draft.kind && note.text === draft.text.trim()
    && (note.valid_from ?? "") === draft.valid_from && (note.valid_to ?? "") === draft.valid_to;
  const testCalls = draft.kind === "dont_report" || (sameMeaning && note?.understood?.current) ? 0 : 1;

  async function test() {
    setBusy("test"); setError(null);
    try {
      const snap = { ...draft };
      setPreview(await previewBookNote(toBody(draft), note?.id));
      setPreviewFor(snap);
    } catch (e) { setError(errText(e)); } finally { setBusy(""); }
  }
  async function save() {
    setBusy("save"); setError(null);
    try {
      const saved = isNew ? await createBookNote(toBody(draft)) : await updateBookNote(note.id, toBody(draft));
      await onSaved(saved.id);
    } catch (e) { setError(errText(e)); setBusy(""); }
  }
  async function togglePause() {
    if (!note) return;
    setBusy("pause"); setError(null);
    try { await setBookNotePaused(note.id, !note.paused_at); await onChanged(); loadEffects(); }
    catch (e) { setError(errText(e)); } finally { setBusy(""); }
  }
  async function remove() {
    if (!note || !window.confirm(t("notes.confirmDelete"))) return;
    setBusy("delete"); setError(null);
    try { await deleteBookNote(note.id); await onDeleted(); } catch (e) { setError(errText(e)); setBusy(""); }
  }

  const input = "w-full border border-slate-200 rounded-lg px-2 py-1.5 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-emerald-200";
  const kicker = isNew ? t("notesPage.newTitle")
    : [t(`notes.kind.${note.kind}`), note.paused_at
        ? t("notesPage.pausedSince", { when: agoText(lang, secsSince(note.paused_at)) }) : t("notesPage.active")].join(" · ");

  return (
    <div className="space-y-3">
      <DetailHeader dot={isNew ? "bg-emerald-500" : note.paused_at ? "bg-slate-300" : "bg-emerald-500"} kicker={kicker}
                    title={isNew ? t("notesPage.newHint") : firstLine(note.text)} />

      {/* 1. LA NOTA: el "prompt" del bookkeeper, editable aquí mismo. */}
      <DetailSection title={t("notesPage.sec.note")}>
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
        <textarea className={`${input} leading-relaxed`} rows={5} maxLength={1000} value={draft.text}
                  placeholder={t(draft.kind === "dont_report" ? "notesPage.placeholder.dont_report" : "notes.textPlaceholder")}
                  onChange={(e) => setDraft({ ...draft, text: e.target.value })} autoFocus={isNew} />
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
        {draft.kind === "dont_report" && !draft.merchant.trim() && (
          <p className="text-[11px] text-amber-700">{t("notesPage.needsMerchant")}</p>
        )}
        {/* Probar · Guardar: escribes → Probar → ves qué entendería y qué marcaría → Guardar. */}
        <div className="flex flex-wrap items-center gap-2 pt-1">
          <LiveOnly compact><button className={BTN_SECONDARY} onClick={test} disabled={!!busy || !draft.text.trim()}>
            {busy === "test" ? t("notesPage.testing") : t("notesPage.test")}
          </button></LiveOnly>
          <span className="text-[11px] text-slate-400">
            {testCalls ? t("auditView.est.calls", { count: testCalls }) : t("notesPage.free")}
          </span>
          <div className="ms-auto flex items-center gap-1.5">
            {(isNew || dirty) && (
              <button className={BTN_GHOST} disabled={!!busy}
                      onClick={() => { if (isNew) onCancelNew(); else { setDraft(toDraft(note)); setPreview(null); } }}>
                {isNew ? t("notes.cancel") : t("notesPage.discard")}
              </button>
            )}
            <button className={BTN_SMALL_PRIMARY} onClick={save} disabled={!!busy || !dirty || !draft.text.trim()}>
              {busy === "save" ? t("notes.saving") : t("notes.save")}
            </button>
          </div>
        </div>
        {error && <p className="text-sm text-rose-700 bg-rose-50 rounded-lg px-3 py-2">{error}</p>}
      </DetailSection>

      {/* 2. LO QUE ENTENDIÓ LA IA: de la prueba (sin guardar) o de la nota guardada. */}
      <DetailSection title={t("notesPage.sec.understood")}
                     right={showPreview && <Badge cls="bg-sky-50 text-sky-800 ring-sky-200">{t("notesPage.testBadge")}</Badge>}>
        <Understood kind={draft.kind} merchant={draft.merchant.trim() || null}
                    u={showPreview ? showPreview.understood : dirty ? undefined : note?.understood ?? null}
                    from={draft.valid_from} to={draft.valid_to} dirty={dirty && !showPreview} />
        {showPreview && (
          <p className="text-[11px] text-slate-400">
            {showPreview.kind === "dont_report" ? t("notesPage.testFreeCode") : showPreview.cached ? t("notesPage.testFree")
              : t("notesPage.testCost", { cost: `$${showPreview.spent.cost_usd.toFixed(5)}`, count: showPreview.spent.calls })}
          </p>
        )}
      </DetailSection>

      {/* 3. LO QUE TOCÓ (o, en la prueba, lo que marcaría / callaría hoy). */}
      <DetailSection title={showPreview ? t("notesPage.sec.wouldTouch") : t("notesPage.sec.touched")}>
        {showPreview ? (
          <Touched kind={draft.kind} flagged={showPreview.flagged} silenced={showPreview.silenced} preview onOpenSheet={onOpenSheet} />
        ) : isNew ? (
          <p className="text-xs text-slate-400">{t("notesPage.touchedNew")}</p>
        ) : effects === null ? (
          <p className="text-xs text-slate-400">{t("notes.loading")}</p>
        ) : (
          <Touched kind={note.kind} flagged={effects.flagged} silenced={effects.silenced} paused={!!note.paused_at} onOpenSheet={onOpenSheet} />
        )}
      </DetailSection>

      {/* Las acciones de la nota guardada. */}
      {!isNew && (
        <div className="border-t border-slate-100 pt-2 flex flex-wrap items-center gap-1.5">
          {note.kind === "keep_in_mind" && !note.paused_at && onProposeRules && (
            <LiveOnly compact><button className={BTN_SECONDARY} disabled={!!busy || dirty} onClick={() => onProposeRules(note.id)}
                    title={t("auditView.est.calls", { count: 1 })}>
              {t("notesPage.proposeRules")}
            </button></LiveOnly>
          )}
          <div className="ms-auto flex items-center gap-1.5">
            <button className={BTN_GHOST} disabled={!!busy} onClick={togglePause}>
              {note.paused_at ? t("notesPage.resume") : t("notesPage.pause")}
            </button>
            <button className={`${BTN_GHOST} text-rose-700 hover:bg-rose-50`} disabled={!!busy} onClick={remove}>{t("notes.delete")}</button>
          </div>
        </div>
      )}
    </div>
  );
}

/** "Lo que entendió la IA". `u`: undefined = la nota cambió y no se probó; null = todavía no la tradujo. */
function Understood({ kind, merchant, u, from, to, dirty }: {
  kind: string; merchant: string | null; u: NoteUnderstood | null | undefined; from: string; to: string; dirty: boolean;
}) {
  const { t } = useTranslation();
  const { sentence, typeLabel, period } = useNoteText();
  if (kind === "dont_report") {
    // 🚫 lo aplica el CÓDIGO, sin IA: no hay nada que "entender".
    return (
      <div className="text-sm text-slate-700 space-y-1">
        <p>{merchant ? t("notesPage.dont.silences", { merchant }) : t("notesPage.dont.noMerchant")}</p>
        <p className="text-[11px] text-slate-400">{t("notesPage.dont.agentsRead")}</p>
      </div>
    );
  }
  if (dirty) return <p className="text-xs text-slate-500">{t("notesPage.changedTest")}</p>;
  if (!u) return <p className="text-xs text-slate-500">{t("notesPage.notYet")}</p>;
  const when = period(from || null, to || null);
  return (
    <div className="space-y-1.5">
      {!u.current && <p className="text-[11px] text-amber-700">{t("notesPage.staleLong")}</p>}
      {u.applies ? (
        <>
          <p className="text-sm text-slate-800"><span className="text-emerald-700 me-1">✓</span>{sentence(u)}</p>
          {(u.business_types.length > 0 || u.merchants.length > 0) && (
            <div className="flex flex-wrap items-center gap-1.5 text-xs">
              <span className="text-[11px] text-slate-400">{t("notesPage.u.where")}</span>
              {u.business_types.map((b) => <span key={b} className="px-2 py-0.5 rounded-full bg-sky-50 text-sky-800">{typeLabel(b)}</span>)}
              {u.merchants.map((m) => <span key={m} className="px-2 py-0.5 rounded-full bg-slate-100 text-slate-700 font-mono">{m}</span>)}
            </div>
          )}
          {when && <p className="text-xs text-slate-600"><span className="text-[11px] text-slate-400 me-1.5">{t("notesPage.u.when")}</span>{when}</p>}
          <p className="text-[11px] text-slate-400">{t("notesPage.u.byCode")}</p>
        </>
      ) : (
        <p className="text-sm text-amber-800">{t(`notesPage.dropped.${u.dropped ?? "not_checkable"}`)} {t("notesPage.dropped.tail")}</p>
      )}
    </div>
  );
}

const STATUS_STYLE: Record<string, string> = {
  open: "bg-amber-50 text-amber-800 ring-amber-200", ok: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  gone: "bg-slate-50 text-slate-500 ring-slate-200",
};

/** "Lo que tocó": lo que la nota marcó (📌) o calla (🚫), por comercio; cada grupo se abre a sus movimientos. */
function Touched({ kind, flagged, silenced, preview = false, paused = false, onOpenSheet }: {
  kind: string; flagged: NoteTouched[]; silenced: NoteTouched[]; preview?: boolean; paused?: boolean;
  onOpenSheet?: (txId: number) => void;
}) {
  const { t } = useTranslation();
  const groups = kind === "dont_report" ? silenced : flagged;
  if (!groups.length) {
    const key = paused ? "pausedNothing" : kind === "dont_report" ? (preview ? "wouldSilenceNone" : "silencesNone") : (preview ? "wouldFlagNone" : "flaggedNone");
    return <p className="text-xs text-slate-500">{t(`notesPage.touch.${key}`)}</p>;
  }
  const rows = groups.reduce((a, g) => a + g.rows, 0);
  const lead = kind === "dont_report"
    ? t(preview ? "notesPage.touch.wouldSilence" : "notesPage.touch.silences", { count: groups.length })
    : t(preview ? "notesPage.touch.wouldFlag" : "notesPage.touch.flagged", { count: rows });
  return (
    <div className="space-y-1">
      <p className="text-xs text-slate-600">{lead}</p>
      <ul className="divide-y divide-slate-100 border border-slate-100 rounded-lg">
        {groups.map((g, i) => (
          <li key={g.finding_id ?? `${g.merchant}-${i}`}>
            <details className="group">
              <summary className="list-none cursor-pointer px-2.5 py-1.5 flex items-center gap-2 text-sm hover:bg-slate-50">
                <span className="text-slate-400 text-xs group-open:rotate-90 transition-transform">▸</span>
                <span className="min-w-0 flex-1 truncate text-slate-800">
                  {g.merchant ?? "—"} <span className="text-xs text-slate-400">×{g.rows}</span>
                  {kind === "dont_report" && g.type && (
                    <span className="text-xs text-slate-500"> · {t(`auditView.type.${g.type}`, { defaultValue: g.type })}</span>
                  )}
                </span>
                {g.status && kind === "keep_in_mind" && (
                  <Badge cls={STATUS_STYLE[g.status] ?? STATUS_STYLE.gone}>{t(`notesPage.status.${g.status}`)}</Badge>
                )}
              </summary>
              <ul className="pb-1.5">
                {g.transactions.map((x) => <TxLine key={x.id} x={x} onOpenSheet={onOpenSheet} />)}
                {g.rows > g.transactions.length && (
                  <li className="px-7 text-[11px] text-slate-400">{t("notesPage.touch.more", { count: g.rows - g.transactions.length })}</li>
                )}
              </ul>
            </details>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Un movimiento: fecha · comercio · monto · categoría, y "Ver en Bookkeeping" a esa fila. */
function TxLine({ x, onOpenSheet }: { x: TxSummary; onOpenSheet?: (txId: number) => void }) {
  const { t } = useTranslation();
  const { fmtDay } = useNoteText();
  return (
    <li className="ps-7 pe-2.5 py-0.5 flex items-center gap-2 text-xs" title={x.description}>
      <span className="text-slate-500 tabular-nums shrink-0">{fmtDay(x.date)}</span>
      <span className="min-w-0 flex-1 truncate text-slate-700">{x.merchant}</span>
      <span className={`tabular-nums shrink-0 ${x.is_inflow ? "text-emerald-700" : "text-slate-700"}`}>{x.is_inflow ? "+" : "−"}{money(Math.abs(x.amount))}</span>
      <span className="hidden sm:inline w-32 truncate text-slate-400 shrink-0">{x.category ?? t("agents.pending")}</span>
      {onOpenSheet && (
        <button className="shrink-0 text-emerald-800 hover:underline" onClick={() => onOpenSheet(x.id)}>{t("notesPage.openInSheet")}</button>
      )}
    </li>
  );
}
