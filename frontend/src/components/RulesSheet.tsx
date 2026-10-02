import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent } from "react";
import { useTranslation } from "react-i18next";
import { RevoGrid } from "@revolist/react-datagrid";
import {
  getRules, createRule, updateRule, getCategories, importRules, applyRuleProposals,
  type Rule, type ChartOfAccounts,
} from "../api";
import {
  CATEGORY_EDITOR, categoryEditors, sectionOfCategory, setEditorChart,
} from "./CategoryEditor";

/**
 * LA HOJA DE REGLAS (FASE 10) — la tercera pestaña, al lado de la hoja y el pivote.
 *
 * Es la "tabla de búsqueda" del modelo de hoja de cálculo que planteó Jose: las celdas de categoría
 * y subcategoría de la hoja son una **fórmula** que busca aquí. Editar una regla NO recalcula hacia
 * atrás (decisión de Jose, opción B): el auditor detecta las filas que quedaron desalineadas y
 * ofrece el fix de un toque. Así no cambian solos los meses ya cerrados.
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

type Row = Rule & { _scopeLabel: string; _draft?: boolean };

// Una fila del archivo importado, antes de aprobarla. `status` lo pone el backend:
// ok · exists (ya hay regla con ese keyword) · duplicate (repetido en el archivo) · needs_section.
type ImportRow = {
  keyword: string; category: string; subcategory: string; account_type: string;
  priority: number | null; status: string; approved: boolean;
};

// EXPORTAR: CSV con los mismos encabezados que el import reconoce sin IA (`_own_rule_columns` en
// main.py) — lo que sale de aquí vuelve a entrar idéntico. BOM para que Excel lea bien el UTF-8.
const EXPORT_HEADERS = ["Keyword", "Category", "Subcategory", "Section", "Priority"];
const csvCell = (v: string) => (/[",\r\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);

function downloadRulesCsv(rules: Rule[]) {
  const lines = [EXPORT_HEADERS, ...rules.map((r) => [
    r.match_text, r.category ?? "", r.subcategory ?? "", r.account_type ?? "",
    r.priority != null ? String(r.priority) : "",
  ])];
  const csv = "\uFEFF" + lines.map((l) => l.map(csvCell).join(",")).join("\r\n") + "\r\n";
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = `rules-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

const TOOL_BTN: React.CSSProperties = {
  border: "1px solid #d5d0c4", background: "#fff", color: "#1f2a24", borderRadius: 8,
  padding: "6px 14px", fontWeight: 600, fontSize: 13, cursor: "pointer", whiteSpace: "nowrap",
};

const FIELD: React.CSSProperties = {
  display: "block", marginTop: 4, padding: "7px 10px", borderRadius: 8,
  border: "1px solid #d5d0c4", fontSize: 13.5, fontWeight: 400, minWidth: 170,
};

export default function RulesSheet({ seedKeyword, onSeedUsed }: {
  /** Descripción de una transacción con la que arrancar la regla (viene de la hoja). */
  seedKeyword?: string | null;
  onSeedUsed?: () => void;
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
  const [draft, setDraft] = useState<{ match_text: string; category: string; subcategory: string } | null>(null);
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
    if (seedKeyword) {
      setDraft({ match_text: seedKeyword, category: "", subcategory: "" });
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
      _scopeLabel: t("rules.thisBook", { defaultValue: "This book" }),
      _draft: true,
    } as Row] : []),
    ...(rules ?? []).map((r) => ({
    ...r,
    // "Sector" en una sola columna legible: de este libro, o de un sector compartido.
    _scopeLabel: r.scope === "SECTOR"
      ? (r.sector || t("rules.anySector", { defaultValue: "Sector" }))
      : t("rules.thisBook", { defaultValue: "This book" }),
    _draft: false,
  })),
  ], [rules, draft, t]);

  const onEdit = useCallback(async (e: CustomEvent) => {
    const { prop, val, model } = e.detail ?? {};
    if (!model?.id) return;
    if (!["match_text", "category", "subcategory", "priority"].includes(prop)) return;
    const value = String(val ?? "").trim();
    // FILA BORRADOR: se va llenando en memoria y se crea sola al tener palabra clave + categoría.
    if (model.id === -1) {
      const next = { ...draftRef.current!, [prop]: value };
      setDraft(next);
      const section = sectionOfCategory(next.category);
      if (next.match_text.trim().length >= 2 && section) {
        setSaving(t("rules.saving", { defaultValue: "Saving…" }));
        try {
          await createRule({
            match_text: next.match_text.trim(), account_type: section,
            category: next.category, subcategory: next.subcategory.trim() || undefined,
          });
          setDraft(null);
          load();
        } catch (e) {
          setError((e as Error).message === "dup"
            ? t("rules.dup", { defaultValue: "There is already a rule with that keyword." })
            : String((e as Error).message));
        } finally { setSaving(null); }
      }
      return;
    }

    const before = rulesRef.current?.find((r) => r.id === model.id);
    if (!before) return;

    // PRIORIDAD: vacía = automático; si no, un entero ≥ 1. Solo cambia QUÉ regla gana.
    if (prop === "priority") {
      const priority = value === "" ? null : Number(value);
      if (priority !== null && !(Number.isInteger(priority) && priority >= 1)) {
        setError(t("rules.priorityInvalid", {
          defaultValue: "Priority must be a whole number from 1 up, or blank for the automatic order.",
        }));
        load();
        return;
      }
      if (priority === before.priority) return;
      setSaving(t("rules.saving", { defaultValue: "Saving…" }));
      setError(null);
      try {
        const res = await updateRule(model.id, { priority });
        setNote(res.reassigned
          ? t("rules.reassigned", { defaultValue: "{{count}} transactions now follow a different rule.",
                                    count: res.reassigned })
          : null);
        load();
      } catch (err) {
        setError(String((err as Error)?.message ?? err));
        load();
      } finally { setSaving(null); }
      return;
    }

    if (!["category", "subcategory"].includes(prop)) return;
    if (value === String((before as any)[prop] ?? "")) return;

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
      load();
    } catch (err) {
      setError(String((err as Error)?.message ?? err));
      load();  // la base manda
    } finally { setSaving(null); }
  }, [load, t]);

  const onEditRef = useRef(onEdit);
  useEffect(() => { onEditRef.current = onEdit; }, [onEdit]);

  // Mismo patrón que la hoja: el wrapper de React no mapea los eventos del web component, y
  // engancharse por `useEffect` con dependencias inestables pierde el listener.
  const attach = useCallback((el: HTMLElement | null) => {
    const prev = gridRef.current as any;
    if (prev?.__h) { prev.removeEventListener("afteredit", prev.__h); delete prev.__h; }
    gridRef.current = el;
    if (!el) return;
    const h = (ev: Event) => void onEditRef.current(ev as CustomEvent);
    (el as any).__h = h;
    el.addEventListener("afteredit", h);
  }, []);

  const columns = useMemo(() => ([
    { prop: "priority", name: t("rules.priority", { defaultValue: "Priority" }), size: 96,
      sortable: true,
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
      sortable: true,
      // Solo la fila borrador deja editar la palabra clave: cambiarla en una regla viva movería
      // en silencio todas las transacciones que ya cubría.
      readonly: (p: any) => !p.model?._draft,
      cellTemplate: (h: any, p: any) => h("span", {
        style: { fontWeight: 600, color: p.model._draft && !p.model.match_text ? "#b9b2a2" : undefined,
                 fontStyle: p.model._draft && !p.model.match_text ? "italic" : "normal" },
      }, p.model.match_text || (p.model._draft
          ? t("rules.keywordHint", { defaultValue: "e.g. HOME DEPOT" }) : "")) },
    { prop: "category", name: t("rules.category", { defaultValue: "Category" }), size: 190,
      sortable: true, editor: CATEGORY_EDITOR },
    { prop: "subcategory", name: t("rules.subcategory", { defaultValue: "Subcategory" }), size: 190,
      cellTemplate: (h: any, p: any) => h("span",
        { style: p.model.subcategory ? {} : { color: "#b9b2a2", fontStyle: "italic" } },
        p.model.subcategory || p.model.match_text || "") },
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
      }, SECTION_LABEL[p.model.account_type] ?? "—") },
    { prop: "matches", name: t("rules.matches", { defaultValue: "Rows" }), size: 90, sortable: true,
      readonly: true,
      cellTemplate: (h: any, p: any) => h("span",
        { style: { display: "block", textAlign: "right", color: "#5b6b62",
                   fontVariantNumeric: "tabular-nums" } }, String(p.model.matches ?? 0)) },
  ]), [t]);

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

  if (error && !rules) return <p style={{ color: "#991b1b", padding: 16 }}>{error}</p>;

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
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
        <input ref={fileRef} type="file" hidden onChange={onImportFile}
          accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" />
        <button onClick={() => downloadRulesCsv((rules ?? []).filter((r) => r.scope === "COMPANY"))}
          disabled={!rules?.length} style={{ ...TOOL_BTN, opacity: rules?.length ? 1 : 0.5 }}>
          {t("rules.export", { defaultValue: "Export" })}
        </button>
        <span style={{ fontSize: 12.5, color: "#5b6b62" }}>
          {note ?? saving ?? t("rules.hint", {
            defaultValue: "The sheet looks up these rules. Editing a rule does not rewrite past months — the auditor flags what fell out of line.",
          })}
        </span>
        {error && <span style={{ fontSize: 12.5, color: "#991b1b" }}>{error}</span>}
      </div>

      <div style={{ flex: 1, minHeight: 0, border: "1px solid #e3ded2", borderRadius: 10,
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
              editors={categoryEditors as any}
              resize range canFocus rowSize={32} rowHeaders
            />}
      </div>
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
                      <optgroup key={s} label={SECTION_LABEL[s]}>
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
                  {SECTION_LABEL[r.account_type] ?? "—"}
                </td>
                <td style={{ ...td, textAlign: "center", color: r.priority != null ? undefined : "#b9b2a2" }}>
                  {r.priority ?? t("rules.priorityAuto", { defaultValue: "auto" })}
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
