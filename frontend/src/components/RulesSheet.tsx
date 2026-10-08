import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent } from "react";
import { useTranslation } from "react-i18next";
import { RevoGrid } from "@revolist/react-datagrid";
import {
  getRules, createRule, updateRule, deleteRule, previewRuleDelete, getCategories, importRules, applyRuleProposals,
  type Rule, type RuleStyle, type ChartOfAccounts,
} from "../api";
import {
  CATEGORY_EDITOR, categoryEditors, sectionOfCategory, setEditorChart,
} from "./CategoryEditor";
import {
  TEXT_EDITOR, textEditors, useUndo, useUndoShortcut, useCaseStyle, applyCase, CellMenu, CellViewer,
  cellFromEvent, FormulaBar, moveFocus, type MenuItem,
} from "./gridKit";

const RULE_EDITORS = { ...categoryEditors, ...textEditors };

/**
 * LA HOJA DE REGLAS (FASE 10) — la tercera pestaña, al lado de la hoja y el pivote.
 *
 * Es la "tabla de búsqueda" del modelo de hoja de cálculo que planteó Jose: las celdas de categoría
 * y subcategoría de la hoja son una **fórmula** que busca aquí. Crear o editar una regla re-aplica
 * la fórmula a las filas que decide una regla (o que están Pendientes). Lo **escrito a mano manda**
 * (decisión de Jose, 3-oct-2026): una fila `manual` está desconectada de las reglas — ninguna la
 * toca — y en la Hoja se ve en amarillo tenue. (Antes este comentario decía "editar una regla NO
 * recalcula hacia atrás", y el código sí recalculaba: se corrigió al protegerse lo manual.)
 *
 * Columnas, las cinco que pidió Jose:
 *   palabra clave · categoría · subcategoría · sector (este libro / un sector) · parte del P&L
 * + PRIORIDAD (1-oct): vacía = orden automático (la más larga gana); con número, 1 va primero —
 *   el "subir la fila" del Excel de Jose. El backend re-decide qué regla gana en lo ya registrado.
 */

const SECTION_ORDER = ["REVENUE", "COGS", "OPERATING_EXPENSE", "OTHER_INCOME", "OTHER_EXPENSE", "NON_PL"];
const SECTION_LABEL: Record<string, string> = {
  REVENUE: "Revenue", COGS: "COGS", OPERATING_EXPENSE: "Operating expenses",
  OTHER_INCOME: "Other income", OTHER_EXPENSE: "Other expenses", NON_PL: "Not in the P&L",
};
const SECTION_COLOR: Record<string, string> = {
  REVENUE: "#15803d", COGS: "#b45309", OPERATING_EXPENSE: "#0369a1",
  OTHER_INCOME: "#15803d", OTHER_EXPENSE: "#991b1b", NON_PL: "#5b6b62",
};

type Row = Rule & { _scopeLabel: string; _draft?: boolean; _rowClass?: string; _clash?: string; _clashLoses?: boolean };

/** Texto legible sobre cualquier fondo (mismo cálculo que la Hoja). */
function readableOn(hex: string): string {
  const h = hex.replace("#", "");
  if (h.length < 6) return "#0f172a";
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
  return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255 > 0.55 ? "#0f172a" : "#ffffff";
}
const styleClass = (st?: RuleStyle | null) => st && (st.bg || st.bold || st.italic)
  ? `rs-${(st.bg ?? "none").replace("#", "")}-${st.bold ? 1 : 0}${st.italic ? 1 : 0}` : "";

// Una fila del archivo importado, antes de aprobarla. `status` lo pone el backend:
// ok · exists (ya hay regla con ese keyword) · duplicate (repetido en el archivo) · needs_section.
type ImportRow = {
  keyword: string; category: string; subcategory: string; account_type: string;
  priority: number | null; status: string; approved: boolean;
  valid_from?: string | null; valid_to?: string | null;
};

// EXPORTAR: CSV con los mismos encabezados que el import reconoce sin IA (`_own_rule_columns` en
// main.py) — lo que sale de aquí vuelve a entrar idéntico. BOM para que Excel lea bien el UTF-8.
const EXPORT_HEADERS = ["Keyword", "Category", "Subcategory", "Section", "Priority", "From", "To"];

/** Fecha de la ventana escrita por el bookkeeper → "YYYY-MM-DD". Acepta 2026-08-03 y 8/3/2026 (como
 *  la escribe un bookkeeper en EE. UU.). "" → null (sin límite). Ilegible → undefined. */
function parseDay(v: string): string | null | undefined {
  const s = v.trim();
  if (!s) return null;
  let y: number, m: number, d: number;
  const iso = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(s);
  const us = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(s);
  if (iso) [y, m, d] = [Number(iso[1]), Number(iso[2]), Number(iso[3])];
  else if (us) [y, m, d] = [Number(us[3]), Number(us[1]), Number(us[2])];
  else return undefined;
  const dt = new Date(Date.UTC(y, m - 1, d));
  if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== m - 1 || dt.getUTCDate() !== d) return undefined;
  return dt.toISOString().slice(0, 10);
}
const csvCell = (v: string) => (/[",\r\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);

function downloadRulesCsv(rules: Rule[]) {
  const lines = [EXPORT_HEADERS, ...rules.map((r) => [
    r.match_text, r.category ?? "", r.subcategory ?? "", r.account_type ?? "",
    r.priority != null ? String(r.priority) : "", r.valid_from ?? "", r.valid_to ?? "",
  ])];
  const csv = "\uFEFF" + lines.map((l) => l.map(csvCell).join(",")).join("\r\n") + "\r\n";
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = `rules-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

const FMT: React.CSSProperties = {
  border: "1px solid #e3ded2", borderRadius: 5, background: "#fff", cursor: "pointer",
  fontSize: 12.5, padding: "2px 7px", lineHeight: 1.6, minWidth: 24,
};

const TOOL_BTN: React.CSSProperties = {
  border: "1px solid #d5d0c4", background: "#fff", color: "#1f2a24", borderRadius: 8,
  padding: "6px 14px", fontWeight: 600, fontSize: 13, cursor: "pointer", whiteSpace: "nowrap",
};

const FIELD: React.CSSProperties = {
  display: "block", marginTop: 4, padding: "7px 10px", borderRadius: 8,
  border: "1px solid #d5d0c4", fontSize: 13.5, fontWeight: 400, minWidth: 170,
};

/** Lo que la barra de menús de la Hoja puede pedirle a la hoja de Reglas (Jose, 4-oct). */
export type RulesCommands = {
  newRule: () => void; importRules: () => void; exportRules: () => void; canExport: boolean;
  undo: () => void; canUndo: boolean; lastUndo: string | null;
  restyle: (patch: Partial<RuleStyle> | "clear") => void; selectedCount: number;
  /** Eliminar la regla de la celda enfocada (Edición → Eliminar regla). */
  deleteRule: () => void; canDelete: boolean;
};

export default function RulesSheet({ seedKeyword, onSeedUsed, toolbar = true, formulaBar = true, onCommands,
  focusRule, onFocusRuleDone }: {
  /** Descripción de una transacción con la que arrancar la regla (viene de la hoja). */
  seedKeyword?: string | null;
  onSeedUsed?: () => void;
  /** Ver / ocultar la barra de botones (menú Ver). Los menús de arriba hacen lo mismo. */
  toolbar?: boolean;
  formulaBar?: boolean;
  onCommands?: (c: RulesCommands | null) => void;
  /** "Ir a la regla" desde la Hoja: esta regla se selecciona y queda a la vista. */
  focusRule?: { id: number; n: number } | null;
  onFocusRuleDone?: () => void;
} = {}) {
  const { t } = useTranslation();
  const [rules, setRules] = useState<Rule[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState<string | null>(null);
  // Formulario de regla nueva. Antes era un `window.prompt` de UN campo que mandaba una categoría
  // fija ("Other") — que NO está en el plan de cuentas, así que el backend devolvía 422 siempre.
  // FILA BORRADOR (idea de Jose): "+ Nueva regla" no abre un formulario aparte — agrega una FILA
  // en blanco arriba de la grilla y ahí se escribe, como agregar un renglón en Excel. La fila vive
  // solo en el frontend hasta tener palabra clave Y categoría; ahí se crea de verdad.
  const [draft, setDraft] = useState<{ match_text: string; category: string; subcategory: string;
                                       valid_from?: string | null; valid_to?: string | null } | null>(null);
  const [chart, setChart] = useState<ChartOfAccounts>({});
  useEffect(() => { getCategories().then((c) => { setChart(c); setEditorChart(c); }).catch(() => {}); }, []);
  const allCats = useMemo(
    () => SECTION_ORDER.flatMap((sec) => (chart[sec] ?? []).map((c) => ({ sec, c }))),
    [chart],
  );
  const gridRef = useRef<HTMLElement | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);
  // IMPORTAR: el archivo se lee, se muestra una vista previa y NADA se crea hasta aprobarla.
  const [imp, setImp] = useState<ImportRow[] | null>(null);
  const [importing, setImporting] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(() => {
    getRules().then(setRules).catch((e) => setError(String(e?.message ?? e)));
  }, []);
  useEffect(() => { load(); }, [load]);

  // Llegó una descripción desde la hoja: se abre la fila borrador con ella, lista para editar.
  useEffect(() => {
    // "" = regla EN BLANCO (el botón "Crear regla" de la Hoja sin fila seleccionada).
    if (seedKeyword != null) {
      setDraft({ match_text: seedKeyword.trim(), category: "", subcategory: "" });
      onSeedUsed?.();
    }
  }, [seedKeyword, onSeedUsed]);

  const draftRef = useRef(draft);
  useEffect(() => { draftRef.current = draft; }, [draft]);

  const rulesRef = useRef<Rule[] | null>(null);
  useEffect(() => { rulesRef.current = rules; }, [rules]);

  const rows: Row[] = useMemo(() => [
    ...(draft ? [{
      id: -1, match_text: draft.match_text, category: draft.category,
      subcategory: draft.subcategory || null, account_type: sectionOfCategory(draft.category),
      priority: null, matches: 0, scope: "COMPANY" as const, sector: null,
      valid_from: draft.valid_from ?? null, valid_to: draft.valid_to ?? null,
      _scopeLabel: t("rules.thisBook", { defaultValue: "This book" }),
      _draft: true,
    } as Row] : []),
    ...(rules ?? []).map((r) => ({
    ...r,
    _rowClass: styleClass(r.style),
    // "Sector" en una sola columna legible: de este libro, o de un sector compartido.
    _scopeLabel: r.scope === "SECTOR"
      ? (r.sector || t("rules.anySector", { defaultValue: "Sector" }))
      : t("rules.thisBook", { defaultValue: "This book" }),
    // CHOQUES (4-oct): con qué reglas caza los mismos movimientos. Primero contra quién PIERDE.
    _clash: [
      ...(r.lost_to ?? []).map((c) => t("rules.losesTo", { defaultValue: "loses {{n}} to {{k}}", n: c.rows, k: c.match_text })),
      ...(r.beats ?? []).map((c) => t("rules.beats", { defaultValue: "wins {{n}} over {{k}}", n: c.rows, k: c.match_text })),
    ].join(" · "),
    _clashLoses: !!r.lost_to?.length,
    _draft: false,
  })),
  ], [rules, draft, t]);

  const { push: pushUndo, undo, canUndo, last: lastUndo } = useUndo((m) => setError(m));
  const [caseStyle, setCaseStyle] = useCaseStyle();

  /** Guarda UNA celda (edición, pegado, borrado o "Pegar" del menú) y deja cómo deshacerla.
   *  Devuelve false si no guardó nada. `reload=false` cuando son varias celdas (rango). */
  const saveCell = useCallback(async (model: any, prop: string, raw: unknown, reload = true): Promise<boolean> => {
    if (!model?.id) return false;
    if (!["match_text", "category", "subcategory", "priority", "valid_from", "valid_to"].includes(prop)) return false;
    const value = String(raw ?? "").trim();
    // FILA BORRADOR: se va llenando en memoria y se crea sola al tener palabra clave + categoría.
    if (model.id === -1) {
      // Las fechas de la ventana se ponen ANTES de la categoría: la regla se crea sola al tener
      // palabra clave + categoría, y "STARBUCKS" sin fechas chocaría con la general.
      let cell: string | null = value;
      if (prop === "valid_from" || prop === "valid_to") {
        const day = parseDay(value);
        if (day === undefined) {
          setError(t("rules.dateInvalid", { defaultValue: "Write the date as YYYY-MM-DD (e.g. 2026-08-03), or leave it blank." }));
          return false;
        }
        cell = day;
      }
      const next = { ...draftRef.current!, [prop]: cell };
      if (next.valid_from && next.valid_to && next.valid_from > next.valid_to) {
        setError(t("rules.windowBackwards", { defaultValue: "The start date is after the end date." }));
        return false;
      }
      setDraft(next);
      draftRef.current = next;
      const section = sectionOfCategory(next.category);
      if (next.match_text.trim().length >= 2 && section) {
        setSaving(t("rules.saving", { defaultValue: "Saving…" }));
        try {
          const created = await createRule({
            match_text: next.match_text.trim(), account_type: section,
            // La subcategoría no queda vacía (Jose, 4-oct): sin escribirla, la sugerencia en gris.
            category: next.category, subcategory: next.subcategory.trim() || next.match_text.trim(),
            valid_from: next.valid_from ?? null, valid_to: next.valid_to ?? null,
          });
          // Deshacer la creación = borrar la regla Y devolver lo que movió a como estaba.
          pushUndo({ label: t("rules.undoCreate", { defaultValue: "create rule {{k}}", k: next.match_text.trim() }),
                     run: async () => { await deleteRule(created.id, true); load(); } });
          setDraft(null);
          if (reload) load();
        } catch (e) {
          setError((e as Error).message === "dup"
            ? t("rules.dup", { defaultValue: "There is already a rule with that keyword." })
            : String((e as Error).message));
        } finally { setSaving(null); }
      }
      return true;
    }

    const before = rulesRef.current?.find((r) => r.id === model.id);
    if (!before) return false;

    // PRIORIDAD: vacía = automático; si no, un entero ≥ 1. Solo cambia QUÉ regla gana.
    if (prop === "priority") {
      const priority = value === "" ? null : Number(value);
      if (priority !== null && !(Number.isInteger(priority) && priority >= 1)) {
        setError(t("rules.priorityInvalid", {
          defaultValue: "Priority must be a whole number from 1 up, or blank for the automatic order.",
        }));
        load();
        return false;
      }
      if (priority === before.priority) return false;
      setSaving(t("rules.saving", { defaultValue: "Saving…" }));
      setError(null);
      try {
        const res = await updateRule(model.id, { priority });
        const prev = before.priority;
        pushUndo({ label: t("rules.undoPriority", { defaultValue: "priority of {{k}}", k: before.match_text }),
                   run: async () => { await updateRule(model.id, { priority: prev }); load(); } });
        setNote(res.reassigned
          ? t("rules.reassigned", { defaultValue: "{{count}} transactions now follow a different rule.",
                                    count: res.reassigned })
          : null);
        if (reload) load();
      } catch (err) {
        setError(String((err as Error)?.message ?? err));
        load();
      } finally { setSaving(null); }
      return true;
    }

    // VENTANA DE FECHAS (4-oct): la regla solo vale entre estos días (inclusivos). Vacía = sin límite.
    if (prop === "valid_from" || prop === "valid_to") {
      const day = parseDay(value);
      if (day === undefined) {
        setError(t("rules.dateInvalid", { defaultValue: "Write the date as YYYY-MM-DD (e.g. 2026-08-03), or leave it blank." }));
        load();
        return false;
      }
      if (day === (before[prop] ?? null)) return false;
      const from = prop === "valid_from" ? day : (before.valid_from ?? null);
      const to = prop === "valid_to" ? day : (before.valid_to ?? null);
      if (from && to && from > to) {
        setError(t("rules.windowBackwards", { defaultValue: "The start date is after the end date." }));
        load();
        return false;
      }
      setSaving(t("rules.saving", { defaultValue: "Saving…" }));
      setError(null);
      try {
        const res = await updateRule(model.id, { [prop]: day });
        const prev = before[prop] ?? null;
        pushUndo({ label: t("rules.undoWindow", { defaultValue: "dates of {{k}}", k: before.match_text }),
                   run: async () => { await updateRule(model.id, { [prop]: prev }); load(); } });
        setNote(res.released
          ? t("rules.released", { defaultValue: "{{count}} transactions fell outside the new dates and went back to the other rules.",
                                  count: res.released })
          : null);
        if (reload) load();
      } catch (err) {
        setError((err as Error).message === "dup"
          ? t("rules.dup", { defaultValue: "There is already a rule with that keyword and those dates." })
          : String((err as Error)?.message ?? err));
        load();
      } finally { setSaving(null); }
      return true;
    }

    if (!["category", "subcategory"].includes(prop)) return false;
    if (value === String((before as any)[prop] ?? "")) return false;
    if (prop === "category" && !value) { load(); return false; }  // Supr no deja una regla sin cuenta
    if (prop === "category" && !sectionOfCategory(value)) {
      // Pegar un texto que no es una cuenta del plan: no se inventan cuentas (candado del plan).
      setError(t("rules.notInChart", { defaultValue: "“{{v}}” isn't an account in this book's chart.", v: value }));
      load();
      return false;
    }

    setSaving(t("rules.saving", { defaultValue: "Saving…" }));
    try {
      const section = prop === "category"
        ? (sectionOfCategory(value) ?? before.account_type ?? "")
        : (before.account_type ?? "");
      await updateRule(model.id, {
        account_type: section,
        category: prop === "category" ? value : before.category,
        subcategory: prop === "subcategory" ? value : (before.subcategory ?? undefined),
      });
      // Deshacer = volver a la regla como estaba (y la regla re-alinea lo que decide).
      pushUndo({ label: t("rules.undoEdit", { defaultValue: "edit rule {{k}}", k: before.match_text }),
                 run: async () => {
                   await updateRule(model.id, { account_type: before.account_type ?? undefined,
                                                category: before.category, subcategory: before.subcategory ?? "" });
                   load();
                 } });
      if (reload) load();
    } catch (err) {
      setError(String((err as Error)?.message ?? err));
      load();  // la base manda
    } finally { setSaving(null); }
    return true;
  }, [load, t, pushUndo]);

  /** `afteredit` trae UNA celda ({prop, val, model}) o un RANGO ({data, models}: pegar con Ctrl+V,
   *  borrar varias con Supr). Antes el rango cambiaba la pantalla pero NO se guardaba. */
  const onEdit = useCallback(async (e: CustomEvent) => {
    const d = e.detail ?? {};
    if (d.data && d.models) {
      for (const [rowIndex, changes] of Object.entries(d.data as Record<string, Record<string, unknown>>)) {
        const model = (d.models as Record<string, any>)[rowIndex];
        for (const [prop, val] of Object.entries(changes ?? {})) await saveCell(model, prop, val, false);
      }
      load();
      return;
    }
    await saveCell(d.model, d.prop, d.val);
  }, [saveCell, load]);

  const onEditRef = useRef(onEdit);
  useEffect(() => { onEditRef.current = onEdit; }, [onEdit]);

  // Mismo patrón que la hoja: el wrapper de React no mapea los eventos del web component, y
  // engancharse por `useEffect` con dependencias inestables pierde el listener.
  // BARRA DE FÓRMULAS (Jose, 4-oct): la celda enfocada → su contenido completo arriba de la grilla.
  const [focus, setFocus] = useState<{ id: number; prop: string; row: number } | null>(null);
  const [selected, setSelected] = useState<number[]>([]);

  // RESALTAR (Jose, 4-oct): lo mismo que en la Hoja — negrita, cursiva, los 4 colores con nombre,
  // cualquier color y limpiar —, por FILA y guardado en la regla (`style`). Deja su deshacer.
  const restyle = useCallback(async (patch: Partial<RuleStyle> | "clear") => {
    const targets = (rulesRef.current ?? []).filter((r) => selected.includes(r.id) && r.scope === "COMPANY");
    if (!targets.length) return;
    const before = targets.map((r) => ({ id: r.id, style: r.style ?? null }));
    const first = targets[0].style ?? { bg: null, bold: false, italic: false };
    // B / I alternan (si la primera ya está en negrita, se quita a todas), como en Excel.
    const next = (st: RuleStyle | null | undefined): RuleStyle | null => {
      if (patch === "clear") return null;
      const cur = st ?? { bg: null, bold: false, italic: false };
      return { bg: patch.bg !== undefined ? patch.bg : cur.bg,
               bold: patch.bold !== undefined ? !first.bold : cur.bold,
               italic: patch.italic !== undefined ? !first.italic : cur.italic };
    };
    setSaving(t("rules.saving", { defaultValue: "Saving…" }));
    try {
      for (const r of targets) await updateRule(r.id, { style: next(r.style) });
      pushUndo({ label: t("rules.undoStyle", { defaultValue: "highlight {{count}} rules", count: targets.length }),
                 run: async () => { for (const b of before) await updateRule(b.id, { style: b.style }); load(); } });
      load();
    } catch (err) { setError(String((err as Error)?.message ?? err)); load(); }
    finally { setSaving(null); }
  }, [selected, load, t, pushUndo]);

  const rowCss = useMemo(() => {
    const seen = new Map<string, RuleStyle>();
    for (const r of rules ?? []) { const c = styleClass(r.style); if (c && !seen.has(c)) seen.set(c, r.style!); }
    return [...seen.entries()].map(([c, st]) => {
      const rr: string[] = [];
      if (st.bg) rr.push(`background:${st.bg}!important`, `color:${readableOn(st.bg)}!important`);
      if (st.bold) rr.push("font-weight:700!important");
      if (st.italic) rr.push("font-style:italic!important");
      return `.${c} .rgCell, .${c} .rgCell *{${rr.join(";")}}`;
    }).join("\n");
  }, [rules]);
  const attach = useCallback((el: HTMLElement | null) => {
    const prev = gridRef.current as any;
    if (prev?.__h) { prev.removeEventListener("afteredit", prev.__h); delete prev.__h; }
    if (prev?.__f) { prev.removeEventListener("afterfocus", prev.__f); delete prev.__f; }
    if (prev?.__s) { prev.removeEventListener("afterfocus", prev.__s); prev.removeEventListener("setrange", prev.__s); delete prev.__s; }
    gridRef.current = el;
    if (!el) return;
    const h = (ev: Event) => void onEditRef.current(ev as CustomEvent);
    (el as any).__h = h;
    el.addEventListener("afteredit", h);
    const f = (ev: Event) => {
      const d = (ev as CustomEvent).detail ?? {};
      if (d.model?.id == null || d.column?.prop == null) return;
      setFocus({ id: d.model.id, prop: String(d.column.prop), row: (d.rowIndex ?? 0) + 1 });
    };
    (el as any).__f = f;
    el.addEventListener("afterfocus", f);
    // SELECCIÓN de filas (rango arrastrado o celda enfocada) para el resaltado, como en la Hoja.
    const sel = async (ev: Event) => {
      const d = (ev as CustomEvent).detail ?? {};
      const a = d.y ?? d.rowIndex, b = d.y1 ?? d.y ?? d.rowIndex;
      if (a == null) return;
      const src = (await (el as any).getVisibleSource?.()) ?? [];
      const [lo, hi] = [Math.min(a, b), Math.max(a, b)];
      setSelected(src.slice(lo, hi + 1).map((r: any) => r.id).filter((id: number) => id > 0));
    };
    (el as any).__s = sel;
    el.addEventListener("afterfocus", sel);
    el.addEventListener("setrange", sel);
  }, []);

  const hostRef = useRef<HTMLDivElement | null>(null);
  useUndoShortcut(hostRef, () => void undo());

  // TAB SOBRE LA SUGERENCIA (Jose, 4-oct): parado en una subcategoría vacía (la que se ve en gris),
  // Tab la pone y pasa a la celda de la derecha, sin abrir el editor.
  // (Se vuelve a enganchar en cada render: lee `focus`, `rows` y `columns` del render actual.)
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Tab" || e.shiftKey || e.ctrlKey || e.metaKey || e.altKey) return;
      const tg = e.target as HTMLElement | null;
      if (tg && (tg.tagName === "INPUT" || tg.tagName === "TEXTAREA")) return;  // el editor decide
      const f = focus;
      if (!f || f.prop !== "subcategory") return;
      const m: any = rows.find((r) => r.id === f.id);
      if (!m || m._draft || m.subcategory || !m.match_text || !editable(m, "subcategory")) return;
      e.preventDefault(); e.stopPropagation();
      const grid = gridRef.current;
      const x = (columns as any[]).findIndex((c) => c.prop === "subcategory");
      void saveCell(m, "subcategory", m.match_text).then(() => moveFocus(grid, x + 1, f.row - 1));
    };
    host.addEventListener("keydown", onKey, true);
    return () => host.removeEventListener("keydown", onKey, true);
  });

  // MENÚ DE CLIC DERECHO (Jose, 4-oct): ver, editar, copiar, pegar, deshacer, la sugerencia y cómo
  // se ve el texto. Funciona sobre la celda bajo el mouse (se enfoca al abrir el menú).
  const [menu, setMenu] = useState<{ at: { x: number; y: number }; model: any; prop: string; row: number } | null>(null);
  const [viewer, setViewer] = useState<{ title: string; text: string } | null>(null);
  const onContextMenu = useCallback(async (e: React.MouseEvent) => {
    e.preventDefault();
    const at = { x: e.clientX, y: e.clientY };
    const c = await cellFromEvent(e.nativeEvent);
    if (c) setMenu({ at, model: c.model, prop: c.prop, row: c.y });
  }, []);
  const cellText = (m: any, prop: string) => String(
    prop === "subcategory" ? (m.subcategory ?? "") : prop === "account_type"
      ? (m.account_type ?? "") : (m[prop] ?? ""));
  const editable = (m: any, prop: string) => m?._draft
    ? ["match_text", "category", "subcategory", "valid_from", "valid_to"].includes(prop)
    : ["category", "subcategory", "priority", "valid_from", "valid_to"].includes(prop) && m?.scope === "COMPANY";
  const menuItems = (m: any, prop: string, row: number): MenuItem[] => {
    const colName = (columns as any[]).find((c) => c.prop === prop)?.name ?? prop;
    const items: MenuItem[] = [
      { label: t("sheet.menu.view", { defaultValue: "View full content" }),
        onClick: () => setViewer({ title: colName, text: cellText(m, prop) }) },
      { label: t("sheet.menu.edit", { defaultValue: "Edit" }), disabled: !editable(m, prop),
        hint: "Enter", onClick: () => void (gridRef.current as any)?.setCellEdit?.(row, prop) },
      { label: t("sheet.menu.copy", { defaultValue: "Copy" }), hint: "Ctrl+C",
        onClick: () => void navigator.clipboard?.writeText(cellText(m, prop)) },
      { label: t("sheet.menu.paste", { defaultValue: "Paste" }), hint: "Ctrl+V", disabled: !editable(m, prop),
        onClick: async () => {
          try { const txt = await navigator.clipboard.readText(); await saveCell(m, prop, txt.split(/\r?\n/)[0]); }
          catch { setError(t("sheet.menu.pasteBlocked", { defaultValue: "The browser didn't allow reading the clipboard. Use Ctrl+V." })); }
        } },
    ];
    if (!m._draft && m.scope === "COMPANY") items.push("sep",
      { label: t("rules.deleteRule", { defaultValue: "Delete rule…" }), onClick: () => void removeRule(m) });
    if (prop === "subcategory" && !m._draft && !m.subcategory && m.match_text && editable(m, prop)) {
      items.push({ label: t("sheet.menu.useSuggestion", { defaultValue: "Use the suggestion: {{s}}", s: m.match_text }),
                   onClick: () => void saveCell(m, "subcategory", m.match_text) });
    }
    items.push("sep",
      { label: lastUndo ? t("sheet.undoWhat", { defaultValue: "Undo: {{what}}", what: lastUndo }) : t("sheet.undo", { defaultValue: "Undo" }),
        hint: "Ctrl+Z", disabled: !canUndo, onClick: () => void undo() },
      "sep",
      { label: t("sheet.case.asis", { defaultValue: "Text as written" }), checked: caseStyle === "asis", onClick: () => setCaseStyle("asis") },
      { label: t("sheet.case.upper", { defaultValue: "ALL CAPS" }), checked: caseStyle === "upper", onClick: () => setCaseStyle("upper") },
      { label: t("sheet.case.title", { defaultValue: "First Letter Capitalized" }), checked: caseStyle === "title", onClick: () => setCaseStyle("title") },
    );
    return items;
  };

  const columns = useMemo(() => ([
    // ORDEN REAL de evaluación (4-oct): en qué lugar corre cada regla — prioridad → con ventana de
    // fechas → la palabra más larga → alfabético. Así no hay que deducirlo.
    { prop: "order", name: t("rules.order", { defaultValue: "Order" }), size: 72, sortable: true, readonly: true,
      cellTemplate: (h: any, p: any) => h("span", {
        title: t("rules.orderHint", { defaultValue: "The order in which the sheet tries the rules: priority first, then rules with dates, then the longest keyword, then A–Z. The first one that matches decides." }),
        style: { display: "block", textAlign: "center", fontVariantNumeric: "tabular-nums", fontWeight: 700,
                 color: p.model.order != null ? "#1A8754" : "#b9b2a2" },
      }, p.model._draft ? "" : (p.model.order != null ? `#${p.model.order}` : "—")) },
    { prop: "priority", name: t("rules.priority", { defaultValue: "Priority" }), size: 96,
      sortable: true, editor: TEXT_EDITOR,
      readonly: (p: any) => !!p.model?._draft,
      cellTemplate: (h: any, p: any) => h("span", {
        title: t("rules.priorityHint", {
          defaultValue: "1 runs first, like moving a row up in Excel. Blank = automatic (longest keyword wins).",
        }),
        style: p.model.priority != null
          ? { display: "block", textAlign: "center", fontWeight: 700, fontVariantNumeric: "tabular-nums" }
          : { display: "block", textAlign: "center", color: "#b9b2a2", fontStyle: "italic" },
      }, p.model.priority != null ? String(p.model.priority)
          : (p.model._draft ? "" : t("rules.priorityAuto", { defaultValue: "auto" }))) },
    { prop: "match_text", name: t("rules.keyword", { defaultValue: "Keyword" }), size: 230,
      sortable: true, editor: TEXT_EDITOR,
      // Solo la fila borrador deja editar la palabra clave: cambiarla en una regla viva movería
      // en silencio todas las transacciones que ya cubría.
      readonly: (p: any) => !p.model?._draft,
      cellTemplate: (h: any, p: any) => h("span", {
        style: { fontWeight: 600, color: p.model._draft && !p.model.match_text ? "#b9b2a2" : undefined,
                 fontStyle: p.model._draft && !p.model.match_text ? "italic" : "normal" },
      }, p.model.match_text || (p.model._draft
          ? t("rules.keywordHint", { defaultValue: "e.g. HOME DEPOT" }) : "")) },
    { prop: "category", name: t("rules.category", { defaultValue: "Category" }), size: 190,
      sortable: true, editor: CATEGORY_EDITOR,
      cellTemplate: (h: any, p: any) => h("span", {}, applyCase(p.model.category, caseStyle)) },
    // La SUGERENCIA en gris (la palabra clave). Tab sobre la celda vacía la pone; al escribir, lo que
    // falta de la sugerencia se ve en gris y Tab / Enter lo aceptan (autocompletar, Jose 4-oct).
    { prop: "subcategory", name: t("rules.subcategory", { defaultValue: "Subcategory" }), size: 190,
      editor: TEXT_EDITOR,
      suggestion: (m: any) => (m?.subcategory ? "" : (m?.match_text ?? "")),
      cellTemplate: (h: any, p: any) => h("span",
        { title: p.model.subcategory ? undefined : t("rules.suggestionHint", {
            defaultValue: "Suggestion (the keyword). Press Tab to use it." }),
          style: p.model.subcategory ? {} : { color: "#b9b2a2", fontStyle: "italic" } },
        applyCase(p.model.subcategory || p.model.match_text || "", caseStyle)) },
    // VENTANA DE FECHAS: vacía = sin límite (la regla de siempre). Gris "—" si no hay.
    ...(["valid_from", "valid_to"] as const).map((k) => ({
      prop: k, name: k === "valid_from" ? t("rules.validFrom", { defaultValue: "From" }) : t("rules.validTo", { defaultValue: "To" }),
      size: 108, sortable: true, editor: TEXT_EDITOR,
      readonly: (p: any) => !p.model?._draft && p.model?.scope !== "COMPANY",
      cellTemplate: (h: any, p: any) => h("span", {
        title: t("rules.windowHint", { defaultValue: "The rule only applies to transactions dated between these days (inclusive). Blank = no limit." }),
        style: p.model[k]
          ? { display: "block", textAlign: "center", fontVariantNumeric: "tabular-nums", fontWeight: 600 }
          : { display: "block", textAlign: "center", color: "#b9b2a2" },
      }, p.model[k] ?? (p.model._draft ? "" : "—")),
    })),
    { prop: "_scopeLabel", name: t("rules.scope", { defaultValue: "Sector" }), size: 150,
      sortable: true, readonly: true,
      cellTemplate: (h: any, p: any) => h("span", {
        style: {
          fontSize: "11.5px", fontWeight: 700, padding: "1px 8px", borderRadius: 5,
          background: p.model.scope === "SECTOR" ? "rgba(244,180,0,.18)" : "rgba(26,135,84,.12)",
          color: p.model.scope === "SECTOR" ? "#7a5b00" : "#15803d",
        },
      }, p.model._scopeLabel) },
    // La sección NO se edita suelta: se deduce de la categoría elegida, igual que en la hoja.
    // Una cuenta pertenece a una sección — dejarlas divergir es justo el bug que se cerró ayer.
    { prop: "account_type", name: t("rules.section", { defaultValue: "P&L section" }), size: 190,
      sortable: true, readonly: true,
      cellTemplate: (h: any, p: any) => h("span", {
        style: { color: SECTION_COLOR[p.model.account_type] ?? "#5b6b62", fontWeight: 600,
                 fontSize: "12.5px" },
      }, p.model.account_type ? t(`sheet.section.${p.model.account_type}`, { defaultValue: SECTION_LABEL[p.model.account_type] }) : "—") },
    { prop: "matches", name: t("rules.matches", { defaultValue: "Rows" }), size: 90, sortable: true,
      readonly: true,
      cellTemplate: (h: any, p: any) => h("span",
        { style: { display: "block", textAlign: "right", color: "#5b6b62",
                   fontVariantNumeric: "tabular-nums" } }, String(p.model.matches ?? 0)) },
    { prop: "_clash", name: t("rules.clash", { defaultValue: "Overlaps" }), size: 260, readonly: true,
      cellTemplate: (h: any, p: any) => h("span", {
        title: p.model._clash ? t("rules.clashHint", { defaultValue: "Rules that match the same transactions. Only the first one in the Order decides each row." }) : "",
        style: { display: "block", fontSize: "12px", whiteSpace: "normal", lineHeight: 1.3,
                 color: p.model._clashLoses ? "#b45309" : "#1A8754", fontWeight: p.model._clashLoses ? 600 : 400 },
      }, p.model._clash ? (p.model._clashLoses ? "⚠ " : "") + p.model._clash : "") },
  ]), [t, caseStyle]);

  const editableCell = (id: number, prop: string) => {
    const m: any = rows.find((r) => r.id === id);
    return !!m && editable(m, prop);
  };
  const formula = useMemo(() => {
    if (!focus) return null;
    const m: any = rows.find((r) => r.id === focus.id);
    const col = (columns as any[]).find((c) => c.prop === focus.prop);
    if (!m || !col) return null;
    let text = "", hint: string | null = null, suggestion: string | null = null;
    switch (focus.prop) {
      case "priority":
        text = m.priority != null ? String(m.priority) : "";
        hint = m._draft ? null : t("rules.priorityAuto", { defaultValue: "auto" });
        break;
      case "subcategory":
        text = m.subcategory ?? "";
        hint = m.match_text ? t("rules.formulaSuggestion", { defaultValue: "Suggestion: {{s}}", s: m.match_text }) : null;
        suggestion = m.subcategory ? null : (m.match_text ?? null);
        break;
      case "account_type":
        text = m.account_type ? t(`sheet.section.${m.account_type}`, { defaultValue: SECTION_LABEL[m.account_type] }) : "";
        break;
      default:
        text = String(m[focus.prop] ?? "");
    }
    return { ref: `${col.name} · ${focus.row}`, text, hint, suggestion };
  }, [focus, rows, columns, t]);

  async function onImportFile(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // permite volver a elegir el mismo archivo
    if (!file) return;
    setImporting(true); setError(null); setNote(null);
    try {
      const { rules: read } = await importRules(file);
      if (read.length === 0) {
        setNote(t("rules.importEmpty", { defaultValue: "Couldn't read any rules from that file." }));
        return;
      }
      setImp(read.map((r) => ({ ...r, subcategory: r.subcategory ?? "", approved: r.status === "ok" })));
    } catch (err) {
      setError(String((err as Error)?.message ?? err));
    } finally { setImporting(false); }
  }

  const setImpRow = (i: number, patch: Partial<ImportRow>) =>
    setImp((rs) => rs && rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  // Solo se crean las aprobadas que tienen sección del P&L resuelta.
  const applicable = imp?.filter((r) => r.approved && r.account_type) ?? [];

  async function applyImport() {
    if (applicable.length === 0) return;
    setImporting(true); setError(null);
    try {
      const res = await applyRuleProposals(applicable.map((r) => ({
        keyword: r.keyword, account_type: r.account_type, category: r.category,
        subcategory: r.subcategory || undefined, priority: r.priority ?? undefined,
        valid_from: r.valid_from ?? null, valid_to: r.valid_to ?? null,
      })));
      setNote(t("rules.imported", {
        defaultValue: "Imported {{c}} rules and aligned {{a}} transactions.", c: res.created, a: res.applied,
      }));
      setImp(null);
      load();
    } catch (err) {
      setError(String((err as Error)?.message ?? err));
    } finally { setImporting(false); }
  }

  /** La regla de la celda enfocada (solo las de ESTE libro se pueden borrar). */
  const focusedRule = (): Rule | null => {
    const r = focus ? (rulesRef.current ?? []).find((x) => x.id === focus.id) : null;
    return r && r.scope === "COMPANY" ? r : null;
  };

  /** ELIMINAR UNA REGLA (Jose, 4-oct): primero dice cuántos movimientos decide y a dónde irán (otra
   *  regla → transferencia → Pendiente; lo escrito a mano no se toca). Se deshace re-creándola. */
  async function removeRule(r: Rule) {
    setError(null); setNote(null);
    let p;
    try { p = await previewRuleDelete(r.id); } catch (e) { setError(String((e as Error).message)); return; }
    const lines = [p.rows === 0
      ? t("rules.deleteNone", { defaultValue: "Delete the rule “{{k}}”? It doesn't decide any transaction right now.", k: r.match_text })
      : t("rules.deleteAsk", { defaultValue: "Delete the rule “{{k}}”? It decides {{count}} transactions. If you delete it:", k: r.match_text, count: p.rows })];
    for (const x of p.to_rules) lines.push("• " + t("rules.deleteToRule", { defaultValue: "{{count}} go to the rule {{k}} ({{c}})", count: x.rows, k: x.match_text, c: x.category }));
    if (p.to_transfer) lines.push("• " + t("rules.deleteToTransfer", { defaultValue: "{{count}} become transfers", count: p.to_transfer }));
    if (p.to_pending) lines.push("• " + t("rules.deleteToPending", { defaultValue: "{{count}} stay Pending", count: p.to_pending }));
    if (p.rows) lines.push(t("rules.deleteManual", { defaultValue: "What you wrote by hand doesn't change. You can undo it." }));
    if (!window.confirm(lines.join("\n"))) return;
    setSaving(t("rules.saving", { defaultValue: "Saving…" }));
    try {
      const res = await deleteRule(r.id);
      const snap = { ...r };
      pushUndo({ label: t("rules.undoDelete", { defaultValue: "delete rule {{k}}", k: r.match_text }),
                 run: async () => {
                   // Volver a crearla igual: la regla re-toma lo que gane (lo manual sigue sin tocarse).
                   const again = await createRule({
                     match_text: snap.match_text, account_type: snap.account_type ?? "", category: snap.category,
                     subcategory: snap.subcategory ?? undefined, priority: snap.priority ?? null,
                     valid_from: snap.valid_from ?? null, valid_to: snap.valid_to ?? null,
                   });
                   if (snap.style) await updateRule(again.id, { style: snap.style });
                   load();
                 } });
      setNote(t("rules.deleted", {
        defaultValue: "Rule deleted. {{r}} went to another rule, {{tr}} to transfers, {{p}} are Pending.",
        r: res.to_rule, tr: res.to_transfer, p: res.to_pending }));
      load();
    } catch (e) {
      setError(String((e as Error).message));
    } finally { setSaving(null); }
  }

  // "IR A LA REGLA" desde la Hoja: cuando las reglas llegan, se selecciona esa fila y se desplaza ahí.
  useEffect(() => {
    if (!focusRule || !rules) return;
    const y = rows.findIndex((r) => r.id === focusRule.id);
    onFocusRuleDone?.();
    if (y < 0) {
      setError(t("rules.goMissing", { defaultValue: "The rule that decided this row no longer exists. Its transactions went back to the other rules or are Pending." }));
      return;
    }
    const grid = gridRef.current as any;
    setTimeout(async () => {
      try {
        await grid?.scrollToRow?.(y);
        const cols = (await grid?.getColumns?.()) ?? [];
        await grid?.setCellsFocus?.({ x: 0, y }, { x: Math.max(0, cols.length - 1), y });
      } catch { /* la grilla aún no está lista */ }
    }, 120);
  }, [focusRule, rules]); // eslint-disable-line react-hooks/exhaustive-deps

  // Los menús de arriba (en la Hoja) usan estas mismas acciones.
  useEffect(() => {
    onCommands?.({
      newRule: () => setDraft({ match_text: "", category: "", subcategory: "" }),
      importRules: () => fileRef.current?.click(),
      exportRules: () => downloadRulesCsv((rulesRef.current ?? []).filter((r) => r.scope === "COMPANY")),
      canExport: !!rules?.length,
      undo: () => void undo(), canUndo, lastUndo,
      restyle: (p) => void restyle(p), selectedCount: selected.length,
      deleteRule: () => { const r = focusedRule(); if (r) void removeRule(r); }, canDelete: !!focusedRule(),
    });
  });
  useEffect(() => () => onCommands?.(null), []);  // eslint-disable-line react-hooks/exhaustive-deps

  if (error && !rules) return <p style={{ color: "#991b1b", padding: 16 }}>{error}</p>;

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8, flexWrap: "wrap" }}>
        {toolbar && <>
        <button onClick={() => setDraft({ match_text: "", category: "", subcategory: "" })}
          style={{ border: "1px solid #1A8754", background: "#1A8754", color: "#fff",
                   borderRadius: 8, padding: "6px 14px", fontWeight: 600, fontSize: 13,
                   cursor: "pointer", whiteSpace: "nowrap" }}>
          + {t("rules.add", { defaultValue: "New rule" })}
        </button>
        <button onClick={() => fileRef.current?.click()} disabled={importing || imp !== null}
          style={{ ...TOOL_BTN, opacity: importing || imp !== null ? 0.5 : 1 }}>
          {importing && imp === null
            ? t("rules.importReading", { defaultValue: "Reading file…" })
            : t("rules.import", { defaultValue: "Import" })}
        </button>
        <button onClick={() => downloadRulesCsv((rules ?? []).filter((r) => r.scope === "COMPANY"))}
          disabled={!rules?.length} style={{ ...TOOL_BTN, opacity: rules?.length ? 1 : 0.5 }}>
          {t("rules.export", { defaultValue: "Export" })}
        </button>
        <button onClick={() => { const r = focusedRule(); if (r) void removeRule(r); }} disabled={!focusedRule()}
          title={focusedRule() ? undefined : t("rules.deletePick", { defaultValue: "Select a cell of the rule to delete" })}
          style={{ ...TOOL_BTN, color: "#991b1b", opacity: focusedRule() ? 1 : 0.5 }}>
          {t("rules.deleteRule", { defaultValue: "Delete rule…" })}
        </button>
        {selected.length > 0 && imp === null && (
          <div style={{ display: "flex", alignItems: "center", gap: 4, background: "#fff",
                        border: "1px solid #d5d0c4", borderRadius: 8, padding: "3px 6px" }}>
            <span style={{ fontSize: 11.5, color: "#5b6b62", fontWeight: 600, marginInlineEnd: 2, whiteSpace: "nowrap" }}>
              {t("sheet.nSelected", { defaultValue: "{{count}} selected", count: selected.length })}
            </span>
            <button onClick={() => void restyle({ bold: true })} title={t("sheet.fmt.bold")}
              style={{ ...FMT, fontWeight: 800 }}>B</button>
            <button onClick={() => void restyle({ italic: true })} title={t("sheet.fmt.italic")}
              style={{ ...FMT, fontStyle: "italic", fontFamily: "Georgia, serif" }}>I</button>
            {([
              ["#f6c343", t("sheet.color.yellow", { defaultValue: "Yellow — check this" })],
              ["#5cc98f", t("sheet.color.green", { defaultValue: "Green — done / OK" })],
              ["#ef6f6f", t("sheet.color.red", { defaultValue: "Red — problem" })],
              ["#6aa9f0", t("sheet.color.blue", { defaultValue: "Blue — ask the client" })],
            ] as const).map(([c, label]) => (
              <button key={c} onClick={() => void restyle({ bg: c })} title={label}
                style={{ width: 22, height: 22, padding: 0, borderRadius: 5, cursor: "pointer", background: c,
                         border: "1px solid rgba(15,23,42,.28)", boxShadow: "inset 0 -1px 0 rgba(15,23,42,.12)" }} />
            ))}
            <label title={t("sheet.fmt.pick")} style={{ ...FMT, display: "inline-flex", alignItems: "center", gap: 4, padding: "2px 5px" }}>
              <span style={{ fontSize: 11 }}>🎨</span>
              <input type="color" defaultValue="#f6c343" onChange={(e) => void restyle({ bg: e.target.value })}
                style={{ width: 24, height: 20, border: 0, padding: 0, background: "none", cursor: "pointer" }} />
            </label>
            <button onClick={() => void restyle("clear")} title={t("sheet.fmt.clear")}
              style={{ ...FMT, color: "#991b1b" }}>✕</button>
          </div>
        )}
        <button onClick={() => void undo()} disabled={!canUndo}
          title={lastUndo ? t("sheet.undoWhat", { defaultValue: "Undo: {{what}}", what: lastUndo }) + " (Ctrl+Z)" : "Ctrl+Z"}
          style={{ ...TOOL_BTN, opacity: canUndo ? 1 : 0.45 }}>
          ↶ {t("sheet.undo", { defaultValue: "Undo" })}
        </button>
        </>}
        <input ref={fileRef} type="file" hidden onChange={onImportFile}
          accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" />
        <span style={{ fontSize: 12.5, color: "#5b6b62", flex: "1 1 260px", minWidth: 0 }}>
          {note ?? saving ?? t("rules.hint", {
            defaultValue: "The sheet looks up these rules. Editing a rule re-applies it to what it decides; what you wrote by hand is never touched. Right-click a cell for more options.",
          })}
        </span>
        {error && <span style={{ fontSize: 12.5, color: "#991b1b" }}>{error}</span>}
      </div>

      {formulaBar && imp === null && rules !== null && rows.length > 0 && (
        <FormulaBar cellRef={formula?.ref ?? null} text={formula?.text ?? null} hint={formula?.hint}
          suggestion={formula?.suggestion}
          // Editable como en Excel (Jose, 4-oct) cuando la celda se puede editar. La categoría tiene que
          // ser una cuenta del plan: lo escrito se compara sin importar mayúsculas.
          onCommit={focus && formula && editableCell(focus.id, focus.prop) ? (v) => {
            const m = rows.find((r) => r.id === focus.id);
            if (!m) return;
            const value = focus.prop === "category"
              ? (allCats.find((c) => c.c.toLowerCase() === v.trim().toLowerCase())?.c ?? v)
              : v;
            void saveCell(m, focus.prop, value);
          } : undefined}
          emptyLabel={t("sheet.formulaEmpty", { defaultValue: "Select a cell to see its full content" })}
          ariaLabel={t("sheet.formulaLabel", { defaultValue: "Cell content" })} />
      )}

      <div ref={hostRef} onContextMenu={(e) => void onContextMenu(e)}
        style={{ flex: 1, minHeight: 0, border: "1px solid #e3ded2", borderRadius: 10,
                    overflow: "hidden" }}>
        {imp !== null
          ? <ImportPreview rows={imp} chart={chart} allCats={allCats} onRow={setImpRow}
              applicable={applicable.length} busy={importing}
              onCancel={() => setImp(null)} onApply={() => void applyImport()} />
          : rules === null
          ? <p style={{ padding: 16, color: "#5b6b62" }}>{t("common.loading", { defaultValue: "Loading…" })}</p>
          : rows.length === 0
          ? <p style={{ padding: 16, color: "#5b6b62" }}>
              {t("rules.empty", { defaultValue: "No rules yet. Create one and the sheet will start using it." })}
            </p>
          : <RevoGrid
              ref={attach as any}
              style={{ height: "100%", fontSize: "13.5px" }}
              theme="compact"
              columns={columns as any}
              source={rows as any}
              editors={RULE_EDITORS as any}
              rowClass="_rowClass"
              resize range canFocus rowSize={32} rowHeaders
            />}
      </div>
      {rowCss && <style>{rowCss}</style>}
      {menu && <CellMenu at={menu.at} items={menuItems(menu.model, menu.prop, menu.row)} onClose={() => setMenu(null)} />}
      {viewer && <CellViewer title={viewer.title} text={viewer.text} onClose={() => setViewer(null)}
                    closeLabel={t("sheet.menu.close", { defaultValue: "Close" })} />}
    </div>
  );
}

const STATUS_STYLE: Record<string, React.CSSProperties> = {
  exists: { background: "rgba(244,180,0,.18)", color: "#7a5b00" },
  duplicate: { background: "#efece4", color: "#5b6b62" },
  needs_section: { background: "rgba(153,27,27,.1)", color: "#991b1b" },
};

/** Vista previa del import: el bookkeeper revisa, corrige la categoría si hace falta y aprueba.
 *  Por defecto solo vienen marcadas las filas `ok` (las que ya existen se pisan solo si las marca). */
function ImportPreview({ rows, chart, allCats, onRow, applicable, busy, onCancel, onApply }: {
  rows: ImportRow[];
  chart: ChartOfAccounts;
  allCats: { sec: string; c: string }[];
  onRow: (i: number, patch: Partial<ImportRow>) => void;
  applicable: number;
  busy: boolean;
  onCancel: () => void;
  onApply: () => void;
}) {
  const { t } = useTranslation();
  const th: React.CSSProperties = { textAlign: "left", padding: "7px 10px", fontSize: 11.5,
    fontWeight: 700, color: "#5b6b62", textTransform: "uppercase", letterSpacing: ".03em",
    borderBottom: "1px solid #e3ded2", position: "sticky", top: 0, background: "#faf8f3" };
  const td: React.CSSProperties = { padding: "6px 10px", borderBottom: "1px solid #f0ece3",
    fontSize: 13.5, verticalAlign: "middle" };
  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <div style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              <th style={{ ...th, width: 36 }} />
              <th style={th}>{t("rules.keyword", { defaultValue: "Keyword" })}</th>
              <th style={th}>{t("rules.category", { defaultValue: "Category" })}</th>
              <th style={th}>{t("rules.subcategory", { defaultValue: "Subcategory" })}</th>
              <th style={th}>{t("rules.section", { defaultValue: "P&L section" })}</th>
              <th style={th}>{t("rules.priority", { defaultValue: "Priority" })}</th>
              <th style={th}>{t("rules.window", { defaultValue: "Dates" })}</th>
              <th style={th} />
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} style={{ opacity: r.approved ? 1 : 0.45 }}>
                <td style={td}>
                  <input type="checkbox" checked={r.approved} disabled={!r.account_type}
                    onChange={(e) => onRow(i, { approved: e.target.checked })} />
                </td>
                <td style={{ ...td, fontWeight: 600 }}>{r.keyword}</td>
                <td style={td}>
                  <select value={r.category}
                    onChange={(e) => {
                      // Elegir una cuenta del plan resuelve la sección: una fila `needs_section`
                      // queda lista y se marca sola.
                      const sec = SECTION_ORDER.find((s) => chart[s]?.includes(e.target.value)) ?? "";
                      const fixed = r.status === "needs_section" && !!sec;
                      onRow(i, { category: e.target.value, account_type: sec,
                                 status: fixed ? "ok" : r.status,
                                 approved: !!sec && (fixed || r.approved) });
                    }}
                    style={{ ...FIELD, marginTop: 0, minWidth: 0, padding: "4px 8px",
                             borderColor: r.account_type ? "#d5d0c4" : "#991b1b" }}>
                    {!r.account_type && <option value={r.category}>{r.category} — ?</option>}
                    {SECTION_ORDER.filter((s) => chart[s]?.length).map((s) => (
                      <optgroup key={s} label={t(`sheet.section.${s}`, { defaultValue: SECTION_LABEL[s] })}>
                        {allCats.filter((x) => x.sec === s).map((x) => (
                          <option key={x.c} value={x.c}>{x.c}</option>
                        ))}
                      </optgroup>
                    ))}
                  </select>
                </td>
                <td style={{ ...td, color: r.subcategory ? undefined : "#b9b2a2" }}>
                  {r.subcategory || r.keyword}
                </td>
                <td style={{ ...td, color: SECTION_COLOR[r.account_type] ?? "#991b1b", fontWeight: 600,
                             fontSize: 12.5 }}>
                  {r.account_type ? t(`sheet.section.${r.account_type}`, { defaultValue: SECTION_LABEL[r.account_type] }) : "—"}
                </td>
                <td style={{ ...td, textAlign: "center", color: r.priority != null ? undefined : "#b9b2a2" }}>
                  {r.priority ?? t("rules.priorityAuto", { defaultValue: "auto" })}
                </td>
                <td style={{ ...td, fontSize: 12.5, whiteSpace: "nowrap", color: r.valid_from || r.valid_to ? undefined : "#b9b2a2" }}>
                  {r.valid_from || r.valid_to
                    ? `${r.valid_from ?? "…"} → ${r.valid_to ?? "…"}`
                    : t("rules.windowAny", { defaultValue: "any date" })}
                </td>
                <td style={td}>
                  {r.status !== "ok" && (
                    <span style={{ fontSize: 11, fontWeight: 700, padding: "1px 7px", borderRadius: 5,
                                   ...(STATUS_STYLE[r.status] ?? STATUS_STYLE.duplicate) }}>
                      {t(`rules.importStatus.${r.status}`, { defaultValue: r.status.replace("_", " ") })}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ display: "flex", gap: 8, padding: 10, borderTop: "1px solid #e3ded2",
                    background: "#faf8f3" }}>
        <button onClick={onCancel} disabled={busy} style={TOOL_BTN}>
          {t("rules.cancel", { defaultValue: "Cancel" })}
        </button>
        <button onClick={onApply} disabled={busy || applicable === 0}
          style={{ ...TOOL_BTN, background: "#1A8754", borderColor: "#1A8754", color: "#fff",
                   opacity: busy || applicable === 0 ? 0.5 : 1 }}>
          {busy
            ? t("rules.saving", { defaultValue: "Saving…" })
            : t("rules.importApprove", { defaultValue: "Create {{count}} rules", count: applicable })}
        </button>
      </div>
    </div>
  );
}
