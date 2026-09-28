import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { RevoGrid } from "@revolist/react-datagrid";
import {
  getRules, createRule, updateRule, getCategories, type Rule, type ChartOfAccounts,
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
      matches: 0, scope: "COMPANY" as const, sector: null,
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
    if (!["match_text", "category", "subcategory"].includes(prop)) return;
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

  if (error && !rules) return <p style={{ color: "#991b1b", padding: 16 }}>{error}</p>;

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
        <button onClick={() => setDraft({ match_text: "", category: "", subcategory: "" })}
          style={{ border: "1px solid #1A8754", background: "#1A8754", color: "#fff",
                   borderRadius: 8, padding: "6px 14px", fontWeight: 600, fontSize: 13,
                   cursor: "pointer" }}>
          + {t("rules.add", { defaultValue: "New rule" })}
        </button>
        <span style={{ fontSize: 12.5, color: "#5b6b62" }}>
          {saving ?? t("rules.hint", {
            defaultValue: "The sheet looks up these rules. Editing a rule does not rewrite past months — the auditor flags what fell out of line.",
          })}
        </span>
        {error && <span style={{ fontSize: 12.5, color: "#991b1b" }}>{error}</span>}
      </div>

      <div style={{ flex: 1, minHeight: 0, border: "1px solid #e3ded2", borderRadius: 10,
                    overflow: "hidden" }}>
        {rules === null
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
