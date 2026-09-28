import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import i18n from "../i18n";
import { RevoGrid } from "@revolist/react-datagrid";
import {
  getSheetRows, getCategories, updateTransaction, getLabels, createLabel, followRule,
  setTransactionLabels, applyCellFormat, type ChartOfAccounts,
} from "../api";
import type { CellFormat, Observation, SheetLabel, SheetRow } from "../types";
import { CATEGORY_EDITOR, categoryEditors, setEditorChart } from "./CategoryEditor";
import Pivot from "./Pivot";
import RulesSheet from "./RulesSheet";

/**
 * LA HOJA (FASE 10, paso 6) — la consola del bookkeeper.
 *
 * Diseño (decidido 26-sep-2026): grilla SOBRE LA BASE DE DATOS, no documento de hoja de cálculo.
 * La base es la única fuente de verdad: cada edición va por `PATCH /transactions/{id}` y se
 * recarga. Nunca puede haber un número en pantalla que no esté en Postgres — con libros
 * contables de clientes, dos fuentes de verdad es el bug que cuesta el cliente.
 *
 * Las 6 columnas son las de Jose: fecha · descripción · monto · categoría · subcategoría ·
 * OBSERVACIÓN (lo que el agente auditor encontró y vale la pena mirar).
 */

const SEV_BG: Record<string, string> = {
  high: "rgba(220,38,38,.10)",
  medium: "rgba(244,180,0,.16)",
  low: "rgba(100,116,139,.10)",
};
const SEV_FG: Record<string, string> = { high: "#991b1b", medium: "#7a5b00", low: "#475569" };

export default function Sheet() {
  const { t } = useTranslation();
  const [rows, setRows] = useState<SheetRow[] | null>(null);
  const [chart, setChart] = useState<ChartOfAccounts>({});
  const [account, setAccount] = useState("");     // "" = todas
  const [onlyFlagged, setOnlyFlagged] = useState(false);
  const [onlyPending, setOnlyPending] = useState(false);
  const [search, setSearch] = useState("");
  const [saving, setSaving] = useState<string | null>(null);
  // DENSIDAD: cuántas transacciones ve el bookkeeper de un vistazo. Se recuerda por navegador.
  const [labels, setLabels] = useState<SheetLabel[]>([]);
  // Filas seleccionadas: el formato y las etiquetas se aplican sobre la selección, como en Excel.
  const [selected, setSelected] = useState<number[]>([]);
  const [labelsOpen, setLabelsOpen] = useState(false);
  const [tab, setTab] = useState<"sheet" | "pivot" | "rules">("sheet");
  // Puente hoja → reglas (idea de Jose): crear una regla PARTIENDO de la descripción de una
  // transacción. Lleva a la pestaña Reglas con la fila borrador ya cargada, para editarla ahí —
  // la descripción del banco casi nunca es el keyword bueno ("HOME DEPOT #1234" → "HOME DEPOT").
  const [ruleSeed, setRuleSeed] = useState<string | null>(null);

  const [density, setDensity] = useState<"compact" | "normal" | "roomy">(() => {
    try { return (localStorage.getItem("contabu-density") as any) || "normal"; } catch { return "normal"; }
  });
  useEffect(() => { try { localStorage.setItem("contabu-density", density); } catch { /* modo privado */ } }, [density]);
  const DENSITY = { compact: { row: 26, font: 12 }, normal: { row: 32, font: 13.5 }, roomy: { row: 40, font: 15 } }[density];
  const [error, setError] = useState<string | null>(null);
  const gridRef = useRef<any>(null);

  const load = useCallback(() => {
    getSheetRows().then(setRows).catch((e) => setError(String(e?.message ?? e)));
  }, []);
  useEffect(() => {
    load();
    getCategories().then((c) => { setChart(c); setEditorChart(c); }).catch(() => {});
    getLabels().then(setLabels).catch(() => {});
  }, [load]);

  // `t` de i18next cambia de identidad en cada render. Si el texto de la observación se calculara
  // dentro del useMemo de las filas, `source` sería un array NUEVO en cada render y RevoGrid
  // descartaría la sesión de edición abierta (bug encontrado 27-sep: el editor abría y no guardaba).
  // Por eso el idioma se resuelve aparte y las filas solo dependen de datos, no de `t`.
  const lang = i18n.language;

  /** Traduce la observación por `type` usando los campos estructurados.
   *  El backend manda el texto en INGLÉS (regla de oro); aquí se muestra en el idioma del usuario. */
  const obsText = useCallback((o: Observation | null): string => {
    if (!o) return "";
    switch (o.type) {
      case "sign_mismatch":
        return t("sheet.obs.signMismatch", {
          defaultValue: "{{section}} but the money came {{dir}}",
          section: o.account_type ?? "",
          dir: o.direction === "in" ? t("sheet.obs.in", { defaultValue: "IN" })
                                    : t("sheet.obs.out", { defaultValue: "OUT" }),
        });
      case "rule_deviation":
        return t("sheet.obs.ruleDeviation", {
          defaultValue: "Doesn't match its rule — should be {{s}}",
          s: `${o.suggested_account_type ?? "?"}/${o.suggested_category ?? "?"}`,
        });
      case "merchant_inconsistency":
        return t("sheet.obs.merchantInconsistency", {
          defaultValue: "Same merchant categorized differently ({{m}})",
          m: o.merchant ?? "",
        });
      case "uncategorized":
        return t("sheet.obs.uncategorized", { defaultValue: "Not categorized yet" });
      default:
        return o.text ?? "";
    }
  }, [lang]); // eslint-disable-line react-hooks/exhaustive-deps

  const accounts = useMemo(
    () => [...new Set((rows ?? []).map((r) => r.bank_account).filter(Boolean))] as string[],
    [rows],
  );

  /** Clase CSS de la fila. El fondo del resaltado y el color de la etiqueta se aplican a la FILA,
   *  no a cada celda — así el color cubre todo el ancho, incluida la columna de observación y el
   *  espacio sobrante. El nombre se deriva de los colores para poder generar el CSS una sola vez. */
  /** Texto legible sobre cualquier fondo: se mide la luminancia del color elegido y se decide
   *  blanco o negro. El bookkeeper puede pintar con el color que quiera (ya trae su propio código
   *  de colores) sin que la fila se vuelva ilegible. */
  const readableOn = (hex: string): string => {
    const h = hex.replace("#", "");
    if (h.length < 6) return "#0f172a";
    const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
    // Luminancia relativa (WCAG, aproximación rápida).
    const lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
    return lum > 0.55 ? "#0f172a" : "#ffffff";
  };

  const rowClassOf = (r: SheetRow): string => {
    const bg = r.formats?._row?.bg ?? null;
    const label = r.labels?.[0]?.color ?? null;
    if (!bg && !label) return "";
    return "cbrow" + [bg, label].map((c) => (c ?? "x").replace("#", "")).join("-");
  };

  // Se inyecta el CSS de las combinaciones que realmente están en uso.
  const rowCss = useMemo(() => {
    const seen = new Map<string, { bg: string | null; label: string | null }>();
    for (const r of rows ?? []) {
      const cls = rowClassOf(r);
      if (cls && !seen.has(cls)) {
        seen.set(cls, { bg: r.formats?._row?.bg ?? null, label: r.labels?.[0]?.color ?? null });
      }
    }
    return [...seen.entries()].map(([cls, { bg, label }]) => {
      const rules: string[] = [];
      // `!important` porque RevoGrid pinta el fondo de sus propias celdas.
      if (bg) rules.push(`background:${bg}!important`, `color:${readableOn(bg)}!important`);
      // La etiqueta marca la fila con una franja a la izquierda (su color entero) y, si no hay
      // resaltado, un tinte suave — para que se vea que está marcada sin gritar.
      if (label && !bg) rules.push(`background:${label}1f!important`);
      const stripe = label ? `.${cls} .rgCell:first-child{box-shadow:inset 3px 0 0 ${label}}` : "";
      return `.${cls} .rgCell{${rules.join(";")}}${stripe}`;
    }).join("\n");
  }, [rows]);

  const visible = useMemo(() => {
    let out = rows ?? [];
    if (account) out = out.filter((r) => r.bank_account === account);
    if (onlyFlagged) out = out.filter((r) => r.observation);
    if (onlyPending) out = out.filter((r) => !r.category);
    const q = search.trim().toLowerCase();
    if (q) out = out.filter((r) =>
      (r.description ?? "").toLowerCase().includes(q) ||
      (r.category ?? "").toLowerCase().includes(q) ||
      (r.subcategory ?? "").toLowerCase().includes(q));
    // La grilla necesita los valores ya planos para pintarlos y editarlos.
    // `_rowClass` pinta la fila COMPLETA (borde a borde). Estilar dentro de cada `cellTemplate`
    // no alcanzaba: dejaba sin color la columna de observación y el espacio sobrante a la derecha.
    return out.map((r) => ({
      ...r,
      _obs: obsText(r.observation),
      _sev: r.observation?.severity ?? "",
      _rowClass: rowClassOf(r),
    }));
  }, [rows, account, onlyFlagged, onlyPending, search, lang]); // eslint-disable-line react-hooks/exhaustive-deps

  const flagged = (rows ?? []).filter((r) => r.observation).length;
  const pending = (rows ?? []).filter((r) => !r.category).length;

  /** Una edición: se manda a la base y se recarga. Si falla, se revierte recargando. */
  const onEdit = useCallback(async (e: any) => {
    const { prop, val, model } = e.detail ?? {};
    if (!model?.id || !["category", "subcategory", "note", "description_extra"].includes(prop)) return;
    const value = String(val ?? "").trim();
    // OJO: RevoGrid ya aplicó el valor nuevo a `model` ANTES de disparar `afteredit`, así que
    // comparar contra `model[prop]` siempre da igual y descartaría toda edición (bug 27-sep).
    // El valor ANTERIOR real está en el estado de React, que es la copia de lo que hay en la base.
    const before = rowsRef.current?.find((r) => r.id === model.id)?.[prop as "category" | "subcategory" | "note" | "description_extra"];
    if (value === (before ?? "")) return;
    setSaving(t("sheet.saving", { defaultValue: "Saving…" }));
    try {
      // "only_this" = excepción puntual: editar una celda NO reescribe el reglamento.
      // Cambiar la regla es una acción explícita, no un efecto secundario de escribir en la hoja.
      // La categoría PERTENECE a una sección del P&L. Se manda junto para que la fila no quede
      // con la cuenta en una sección y el monto sumando en otra (el backend también lo deduce).
      const payload: Record<string, unknown> = { [prop]: value, rule_action: "only_this" };
      if (prop === "category") {
        const section = Object.entries(chartRef.current).find(([, cats]) => cats.includes(value))?.[0];
        if (section) payload.account_type = section;
      }
      await updateTransaction(model.id, payload as any);
      load();
      setSaving(null);
    } catch (err) {
      setError(String((err as Error)?.message ?? err));
      setSaving(null);
      load(); // revierte: la base manda
    }
  }, [load, t]);

  // El wrapper de React NO mapea los eventos del web component (verificado: el elemento no expone
  // ningún prop `on*edit`), así que se escucha el evento NATIVO `afteredit`.
  //
  // El enganche va por CALLBACK REF y el handler por ref mutable, a propósito: `t` de i18next cambia
  // de identidad en cada render, así que un `useEffect([onEdit])` se desataba y reataba sin parar y
  // se perdía el enganche real (bug encontrado 27-sep: el editor guardaba pero no llegaba el PATCH).
  // Así el listener se ata UNA vez al montar el elemento y siempre llama a la versión más reciente.
  // El handler se ata UNA vez (callback ref), así que lee las filas por ref, no por clausura.
  const chartRef = useRef<ChartOfAccounts>({});
  useEffect(() => { chartRef.current = chart; }, [chart]);

  const rowsRef = useRef<SheetRow[] | null>(null);
  useEffect(() => { rowsRef.current = rows; }, [rows]);

  const onEditRef = useRef(onEdit);
  useEffect(() => { onEditRef.current = onEdit; }, [onEdit]);

  const attachGrid = useCallback((el: HTMLElement | null) => {
    const prev = gridRef.current as any;
    if (prev?.__contabuHandler) {
      prev.removeEventListener("afteredit", prev.__contabuHandler);
      delete prev.__contabuHandler;
    }
    if (prev?.__contabuRange) {
      prev.removeEventListener("afterfocus", prev.__contabuRange);
      prev.removeEventListener("selectionchanged", prev.__contabuRange);
      delete prev.__contabuRange;
    }
    gridRef.current = el;
    if (!el) return;
    const handler = (e: Event) => void onEditRef.current(e as CustomEvent);
    (el as any).__contabuHandler = handler;
    el.addEventListener("afteredit", handler);

    // Selección: el rango que el bookkeeper marcó arrastrando (o la celda enfocada).
    const onRange = (e: Event) => {
      const d = (e as CustomEvent).detail ?? {};
      const rows = (rowsRef.current ?? []);
      const a = d.y ?? d.rowIndex, b = d.y1 ?? d.y ?? d.rowIndex;
      if (a == null) return;
      const [lo, hi] = [Math.min(a, b), Math.max(a, b)];
      setSelected(rows.slice(lo, hi + 1).map((r) => r.id));
    };
    (el as any).__contabuRange = onRange;
    el.addEventListener("afterfocus", onRange);
    el.addEventListener("selectionchanged", onRange);
  }, []);

  /** FIX DE UN TOQUE (FASE 10): el auditor ya dice a qué debería moverse (`suggested_*`),
   *  así que la observación no solo informa — se arregla desde ahí mismo, sin abrir otra pantalla.
   *  `rule_action: "only_this"` porque re-alinear con una regla NO debe crear otra regla redundante
   *  (mismo criterio que `applyFix` en `Audit.tsx`). */
  const applyFix = useCallback(async (id: number, o: Observation) => {
    if (!o.suggested_category || !o.suggested_account_type) return;
    setSaving(t("sheet.fixing", { defaultValue: "Fixing…" }));
    try {
      await updateTransaction(id, {
        account_type: o.suggested_account_type,
        category: o.suggested_category,
        rule_action: "only_this",
      } as any);
      load();
    } catch (err) {
      setError(String((err as Error)?.message ?? err));
    } finally {
      setSaving(null);
    }
  }, [load, t]);
  const applyFixRef = useRef(applyFix);
  useEffect(() => { applyFixRef.current = applyFix; }, [applyFix]);

  /** Formato sobre la selección. `column: null` = la fila entera (es lo que un bookkeeper
   *  espera al resaltar: se marca el movimiento, no una casilla suelta). */
  const format = useCallback(async (patch: Parameters<typeof applyCellFormat>[0]) => {
    if (!selected.length) return;
    setSaving(t("sheet.saving", { defaultValue: "Saving…" }));
    try {
      await applyCellFormat({ transaction_ids: selected, column: null, ...patch });
      load();
    } catch (err) { setError(String((err as Error)?.message ?? err)); }
    finally { setSaving(null); }
  }, [selected, load, t]);

  /** Alternar una etiqueta en todas las filas seleccionadas. */
  const toggleLabel = useCallback(async (labelId: number) => {
    if (!selected.length) return;
    const rs = (rowsRef.current ?? []).filter((r) => selected.includes(r.id));
    const allHave = rs.every((r) => r.labels?.some((l) => l.id === labelId));
    setSaving(t("sheet.saving", { defaultValue: "Saving…" }));
    try {
      for (const r of rs) {
        const ids = (r.labels ?? []).map((l) => l.id).filter((id) => id !== labelId);
        await setTransactionLabels(r.id, allHave ? ids : [...ids, labelId]);
      }
      load();
    } catch (err) { setError(String((err as Error)?.message ?? err)); }
    finally { setSaving(null); }
  }, [selected, load, t]);

  /** Estilo de una celda = formato de la fila + formato de esa columna (la columna gana). */
  const styleOf = (m: any, col: string): Record<string, string> => {
    const row: CellFormat = m.formats?._row ?? {};
    const cell: CellFormat = m.formats?.[col] ?? {};
    const f = { ...row, ...cell };
    const out: Record<string, string> = {};
    if (f.bold) out.fontWeight = "700";
    if (f.italic) out.fontStyle = "italic";
    // El fondo de la FILA lo pinta `_rowClass` (cubre todo el ancho); aquí solo el de la columna.
    if (cell.bg) { out.background = cell.bg; out.color = readableOn(cell.bg); }
    if (f.fg) out.color = f.fg;
    if (f.font_size) out.fontSize = `${f.font_size}px`;
    if (f.font_family) out.fontFamily = f.font_family;
    return out;
  };

  /** VOLVER A SEGUIR LA REGLA: la celda deja de ser un valor escrito y vuelve a ser "fórmula".
   *  Si ninguna regla la cubre, se dice claro — no se inventa nada. */
  const backToRule = useCallback(async (id: number) => {
    setSaving(t("sheet.following", { defaultValue: "Looking up the rule…" }));
    try {
      await followRule(id);
      load();
    } catch (err) {
      setError((err as Error).message === "no-rule"
        ? t("sheet.noRule", { defaultValue: "No rule covers this transaction. Create one in the Rules tab." })
        : String((err as Error).message));
    } finally { setSaving(null); }
  }, [load, t]);
  const backToRuleRef = useRef(backToRule);
  useEffect(() => { backToRuleRef.current = backToRule; }, [backToRule]);

  const columns = useMemo(() => ([
    { prop: "date", name: t("sheet.date", { defaultValue: "Date" }), size: 116, sortable: true, readonly: true,
      // Fecha SIEMPRE en formato de EE.UU. (mes-día-año), sin importar el idioma de la UI: es dato
      // contable y la regla de oro manda que vaya en inglés. En `es` el navegador la daría al revés
      // (28 mar 2026) y confundiría al leer un extracto estadounidense.
      cellTemplate: (h: any, p: any) => h("span", { style: styleOf(p.model, "date") }, p.model.date
        ? new Date(p.model.date + "T00:00:00").toLocaleDateString("en-US", { month: "short", day: "2-digit", year: "numeric" })
        : "") },
    // La descripción del BANCO no se edita nunca (es la evidencia). Lo que se edita es
    // `description_extra`: el texto que el bookkeeper AGREGA — a quién fue el cheque, qué era esa
    // ACH críptica — y que ENTRA AL MATCHEO DE REGLAS. Se pinta distinto para que nunca haya duda
    // de qué dijo el banco y qué escribiste tú.
    { prop: "description_extra", name: t("sheet.description", { defaultValue: "Description" }),
      size: 360, sortable: true,
      cellTemplate: (h: any, p: any) => {
        const kids: any[] = [
          h("span", { style: styleOf(p.model, "description") }, p.model.description ?? ""),
        ];
        if (p.model.description_extra) {
          kids.push(h("span", {
            title: t("sheet.yourText", { defaultValue: "Text you added — rules match on it too" }),
            style: { marginInlineStart: 6, color: "#1A8754", fontWeight: 600,
                     borderInlineStart: "2px solid #1A8754", paddingInlineStart: 6 },
          }, p.model.description_extra));
        } else {
          kids.push(h("span", {
            style: { marginInlineStart: 6, color: "#c9c2b2", fontStyle: "italic", fontSize: "12px" },
          }, t("sheet.addText", { defaultValue: "+ add" })));
        }
        for (const l of (p.model.labels ?? []) as SheetLabel[]) {
          kids.push(h("span", {
            title: l.name,
            style: { marginInlineStart: 6, background: l.color, color: "#fff", borderRadius: 4,
                     padding: "0 6px", fontSize: "10.5px", fontWeight: 700, whiteSpace: "nowrap" },
          }, l.name));
        }
        return h("span", { style: { display: "flex", alignItems: "center", overflow: "hidden" } }, ...kids);
      } },
    { prop: "amount", name: t("sheet.amount", { defaultValue: "Amount" }), size: 120, sortable: true,
      readonly: true,
      cellTemplate: (h: any, p: any) => {
        const inflow = p.model.is_inflow;
        return h("span", { style: {
          display: "block", textAlign: "right", fontVariantNumeric: "tabular-nums",
          color: inflow ? "#15803d" : "#0f172a", fontWeight: inflow ? 600 : 400,
          ...styleOf(p.model, "amount") } },
          (inflow ? "" : "−") + "$" + Number(p.model.amount ?? 0)
            .toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
      } },
    { prop: "category", name: t("sheet.category", { defaultValue: "Category" }), size: 190, sortable: true,
      editor: CATEGORY_EDITOR,  // plan de cuentas cerrado: se elige, no se inventa
      // PROCEDENCIA (modelo de hoja de cálculo, idea de Jose): `ƒ` = la decidió una regla —la celda
      // es una "fórmula"—; sin marca = escrita a mano. Igual que en Excel, un valor a mano anula la
      // fórmula, y con "volver a seguir la regla" se restaura.
      cellTemplate: (h: any, p: any) => {
        const fromRule = p.model.matched_rule_id != null;
        const manual = p.model.source === "manual";
        const kids: any[] = [];
        if (fromRule) kids.push(h("span", {
          title: t("sheet.fromRule", { defaultValue: "Comes from a rule — click to see which" }),
          style: { color: "#1A8754", fontWeight: 700, fontStyle: "italic",
                   marginInlineEnd: 5, fontSize: "12px" } }, "\u0192"));
        else if (manual) kids.push(h("button", {
          title: t("sheet.manualClick", {
            defaultValue: "Written by hand — click to follow the rule again" }),
          onClick: (e: MouseEvent) => { e.stopPropagation(); void backToRuleRef.current(p.model.id); },
          style: { border: 0, background: "transparent", cursor: "pointer", padding: 0,
                   color: "#b9b2a2", marginInlineEnd: 5, fontSize: "11px", lineHeight: 1 },
        }, "\u270e"));
        // PENDIENTE (decisión de Jose, 27-sep): una celda sin categoría se ve en ROJO y lo dice.
        // Es la base visual de "toda la categorización sale de las reglas": lo que ninguna regla
        // cubrió no se rellena con una adivinanza — se queda visiblemente sin terminar.
        if (!p.model.category) {
          return h("span", {
            title: t("sheet.pendingHint", {
              defaultValue: "No rule covers this yet — pick a category or create a rule" }),
            style: {
              background: "rgba(220,38,38,.10)", color: "#991b1b", fontWeight: 700,
              padding: "1px 8px", borderRadius: 5, fontSize: "12.5px",
              border: "1px dashed rgba(220,38,38,.45)",
            },
          }, t("sheet.pending", { defaultValue: "Pending" }));
        }
        kids.push(h("span", { style: styleOf(p.model, "category") }, p.model.category ?? ""));
        return h("span", { style: { display: "flex", alignItems: "center" } }, ...kids);
      } },
    { prop: "subcategory", name: t("sheet.subcategory", { defaultValue: "Subcategory" }), size: 165,
      cellTemplate: (h: any, p: any) => h("span", { style: styleOf(p.model, "subcategory") }, p.model.subcategory ?? "") },
    { prop: "_obs", name: t("sheet.observation", { defaultValue: "Observation" }), size: 330, readonly: true,
      cellTemplate: (h: any, p: any) => {
        const txt = p.model._obs, sev = p.model._sev;
        if (!txt) return h("span", {}, "");
        const o: Observation | null = p.model.observation;
        const fixable = !!(o?.suggested_category && o?.suggested_account_type);
        // El texto se lee COMPLETO: aquí va a escribir el agente su reporte con detalle, y un
        // reporte cortado con "…" no sirve de nada. `title` lo deja además en el tooltip.
        const chip = h("span", { title: txt, style: {
          background: SEV_BG[sev] ?? SEV_BG.low, color: SEV_FG[sev] ?? SEV_FG.low,
          padding: "2px 8px", borderRadius: 6, fontSize: "12.5px", fontWeight: 600,
          whiteSpace: "normal", lineHeight: 1.35, display: "block" } }, txt);
        if (!fixable) return h("span", { style: { display: "block" } }, chip);
        // Botón de arreglo: dice A QUÉ lo va a mover, para que el bookkeeper decida viendo.
        const btn = h("button", {
          title: `→ ${o!.suggested_account_type} / ${o!.suggested_category}`,
          onClick: (e: MouseEvent) => { e.stopPropagation(); void applyFixRef.current(p.model.id, o!); },
          style: {
            marginInlineStart: 6, border: "1px solid #1A8754", background: "#fff", color: "#1A8754",
            borderRadius: 5, fontSize: "11.5px", fontWeight: 700, padding: "1px 7px",
            cursor: "pointer", whiteSpace: "nowrap", flex: "none",
          },
        }, `${t("sheet.fix", { defaultValue: "Fix" })} → ${o!.suggested_category}`);
        return h("span", { style: { display: "flex", alignItems: "flex-start", gap: 0 } }, chip, btn);
      } },
    // NOTA: texto libre del bookkeeper sobre ESA línea. Va antes de la observación a propósito —
    // lo suyo primero, lo del agente después.
    { prop: "note", name: t("sheet.note", { defaultValue: "Note" }), size: 220,
      cellTemplate: (h: any, p: any) => h("span", {
        title: p.model.note || "",
        style: { ...styleOf(p.model, "note"), whiteSpace: "normal", lineHeight: 1.35,
                 display: "block", color: p.model.note ? "#0f172a" : "#b9b2a2",
                 fontStyle: p.model.note ? "normal" : "italic", fontSize: "12.5px" },
      }, p.model.note || t("sheet.notePlaceholder", { defaultValue: "add a note…" })) },
  ]), [t]);

  const TOOL: React.CSSProperties = {
    border: "1px solid #e3ded2", borderRadius: 5, background: "#fff", cursor: "pointer",
    fontSize: 12.5, padding: "2px 7px", lineHeight: 1.6, minWidth: 24,
  };

  const input: React.CSSProperties = {
    padding: "6px 10px", borderRadius: 8, border: "1px solid #d5d0c4",
    fontSize: 13.5, background: "#fff",
  };

  if (error && !rows) return <p style={{ color: "#991b1b", padding: 16 }}>{error}</p>;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 132px)", minHeight: 420 }}>
      {rowCss && <style>{rowCss}</style>}
      {/* Barra de control: el filtro por cuenta que pidió Jose, arriba de la hoja. */}
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginBottom: 10 }}>
        {/* Hoja / Pivote: el pivote es la MISMA verdad, agrupada — por eso vive aquí al lado
            y comparte el filtro de cuenta, no en otra sección del menú. */}
        <div style={{ display: "flex", gap: 2, background: "#fff", border: "1px solid #d5d0c4",
                      borderRadius: 8, padding: 2 }}>
          {(["sheet", "pivot", "rules"] as const).map((k) => (
            <button key={k} onClick={() => setTab(k)}
              style={{ border: 0, borderRadius: 6, cursor: "pointer", fontWeight: 600, fontSize: 13,
                       padding: "5px 13px",
                       background: tab === k ? "#1A8754" : "transparent",
                       color: tab === k ? "#fff" : "#5b6b62" }}>
              {k === "sheet" ? t("sheet.tabSheet", { defaultValue: "Sheet" })
               : k === "pivot" ? t("sheet.tabPivot", { defaultValue: "Pivot" })
               : t("sheet.tabRules", { defaultValue: "Rules" })}
            </button>
          ))}
        </div>

        <select value={account} onChange={(e) => setAccount(e.target.value)} style={input}>
          <option value="">{t("sheet.allAccounts", { defaultValue: "All accounts" })}</option>
          {accounts.map((a) => <option key={a} value={a}>{a}</option>)}
        </select>

        <input value={search} onChange={(e) => setSearch(e.target.value)} style={{ ...input, minWidth: 210 }}
               placeholder={t("sheet.search", { defaultValue: "Search…" })} />

        {tab === "sheet" && pending > 0 && (
          <button onClick={() => setOnlyPending((v) => !v)}
            style={{ ...input, cursor: "pointer", fontWeight: 600,
                     border: "1px solid " + (onlyPending ? "#dc2626" : "#d5d0c4"),
                     background: onlyPending ? "rgba(220,38,38,.10)" : "#fff",
                     color: onlyPending ? "#991b1b" : "#5b6b62" }}>
            {t("sheet.onlyPending", { defaultValue: "Pending ({{n}})", n: pending })}
          </button>
        )}

        {tab === "sheet" && flagged > 0 && (
          <button onClick={() => setOnlyFlagged((v) => !v)}
            style={{ ...input, cursor: "pointer", fontWeight: 600,
                     border: "1px solid " + (onlyFlagged ? "#f4b400" : "#d5d0c4"),
                     background: onlyFlagged ? "rgba(244,180,0,.18)" : "#fff",
                     color: onlyFlagged ? "#7a5b00" : "#5b6b62" }}>
            {t("sheet.onlyFlagged", { defaultValue: "Needs a look ({{n}})", n: flagged })}
          </button>
        )}

        {/* BARRA DE FORMATO — actúa sobre la SELECCIÓN. Deshabilitada si no hay nada marcado,
            para que nunca se aplique "a lo que sea" sin que el bookkeeper lo haya elegido. */}
        {tab === "sheet" && selected.length > 0 && (
          <div style={{ display: "flex", alignItems: "center", gap: 4, background: "#fff",
                        border: "1px solid #d5d0c4", borderRadius: 8, padding: "3px 6px" }}>
            <span style={{ fontSize: 11.5, color: "#5b6b62", fontWeight: 600, marginInlineEnd: 2 }}>
              {t("sheet.nSelected", { defaultValue: "{{count}} selected", count: selected.length })}
            </span>

            <button onClick={() => format({ bold: true })} title="Bold"
              style={{ ...TOOL, fontWeight: 800 }}>B</button>
            <button onClick={() => format({ italic: true })} title="Italic"
              style={{ ...TOOL, fontStyle: "italic", fontFamily: "Georgia, serif" }}>I</button>

            {/* Resaltado: los colores de Excel que un bookkeeper ya usa. */}
            {/* Colores rápidos. Antes eran cuadritos pálidos sin borde y no se distinguían
                (Jose: "no se cuál es cual"): ahora son cuadros sólidos, más grandes y con nombre. */}
            {([
              ["#f6c343", t("sheet.color.yellow", { defaultValue: "Yellow — check this" })],
              ["#5cc98f", t("sheet.color.green",  { defaultValue: "Green — done / OK" })],
              ["#ef6f6f", t("sheet.color.red",    { defaultValue: "Red — problem" })],
              ["#6aa9f0", t("sheet.color.blue",   { defaultValue: "Blue — ask the client" })],
            ] as const).map(([c, label]) => (
              <button key={c} onClick={() => format({ bg: c })} title={label}
                style={{
                  width: 22, height: 22, padding: 0, borderRadius: 5, cursor: "pointer",
                  background: c, border: "1px solid rgba(15,23,42,.28)",
                  boxShadow: "inset 0 -1px 0 rgba(15,23,42,.12)",
                }} />
            ))}

            {/* Cualquier color, no solo los 4 rápidos: el bookkeeper ya tiene su código de colores. */}
            <label title="Pick any color"
              style={{ ...TOOL, display: "inline-flex", alignItems: "center", gap: 4, padding: "2px 5px" }}>
              <span style={{ fontSize: 11 }}>🎨</span>
              <input type="color" defaultValue="#f6c343"
                onChange={(e) => format({ bg: e.target.value })}
                style={{ width: 24, height: 20, border: 0, padding: 0, background: "none", cursor: "pointer" }} />
            </label>

            <select onChange={(e) => e.target.value && format({ font_family: e.target.value })}
              defaultValue="" title="Font" style={{ ...TOOL, padding: "2px 4px", fontSize: 11.5 }}>
              <option value="">Font</option>
              <option value="inherit">Default</option>
              <option value="Georgia, serif">Georgia</option>
              <option value='"Courier New", monospace'>Courier</option>
              <option value="Arial, sans-serif">Arial</option>
            </select>

            {selected.length === 1 && (
              <button
                onClick={() => {
                  const row = (rowsRef.current ?? []).find((r) => r.id === selected[0]);
                  if (!row?.description) return;
                  // Se propone el texto COMBINADO: si escribiste a quién fue el cheque, eso es
                  // justamente lo que quieres como palabra clave, no "CHECK 1234".
                  setRuleSeed([row.description, row.description_extra].filter(Boolean).join(" "));
                  setTab("rules");
                }}
                title={t("sheet.ruleFromRow", {
                  defaultValue: "Create a rule from this description" })}
                style={{ ...TOOL, fontWeight: 700, color: "#1A8754", borderColor: "#1A8754" }}>
                ƒ+ {t("sheet.newRule", { defaultValue: "Rule" })}
              </button>
            )}

            <button onClick={() => format({ clear: true })} title="Clear formatting"
              style={{ ...TOOL, color: "#991b1b" }}>✕</button>

            {/* Etiquetas CON SIGNIFICADO (dato: se filtra, la IA las lee). */}
            <div style={{ position: "relative" }}>
              <button onClick={() => setLabelsOpen((v) => !v)} style={{ ...TOOL, fontWeight: 700 }}>
                🏷 {t("sheet.labels", { defaultValue: "Labels" })}
              </button>
              {labelsOpen && (
                <div style={{ position: "absolute", top: "110%", insetInlineStart: 0, zIndex: 100,
                              background: "#fff", border: "1px solid #d5d0c4", borderRadius: 8,
                              boxShadow: "0 8px 24px rgba(15,23,42,.16)", minWidth: 210, padding: 6 }}>
                  {labels.map((l) => (
                    <button key={l.id} onClick={() => { void toggleLabel(l.id); }}
                      style={{ display: "flex", alignItems: "center", gap: 7, width: "100%",
                               border: 0, background: "transparent", cursor: "pointer",
                               padding: "5px 6px", fontSize: 13, textAlign: "start" }}>
                      <span style={{ width: 10, height: 10, borderRadius: 3, background: l.color, flex: "none" }} />
                      {l.name}
                    </button>
                  ))}
                  <button
                    onClick={async () => {
                      const name = window.prompt(t("sheet.newLabel", { defaultValue: "Label name (what it means):" }));
                      if (!name?.trim()) return;
                      const l = await createLabel(name.trim(), "#f4b400");
                      setLabels((ls) => [...ls, l]);
                    }}
                    style={{ width: "100%", border: 0, borderTop: "1px solid #e3ded2", marginTop: 4,
                             paddingTop: 7, background: "transparent", cursor: "pointer",
                             fontSize: 12.5, color: "#1A8754", fontWeight: 600, textAlign: "start" }}>
                    + {t("sheet.addLabel", { defaultValue: "New label" })}
                  </button>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Densidad: tamaño de letra y alto de fila. Un bookkeeper revisando 300 movimientos
            quiere ver el máximo posible; uno mostrándole los números al cliente, lo contrario. */}
        <div style={{ display: "flex", gap: 2, background: "#fff", border: "1px solid #d5d0c4",
                      borderRadius: 8, padding: 2 }}>
          {([["compact", "A"], ["normal", "A"], ["roomy", "A"]] as const).map(([d, label], i) => (
            <button key={d} onClick={() => setDensity(d)}
              title={t(`sheet.density.${d}`, { defaultValue: d })}
              style={{
                border: 0, borderRadius: 6, cursor: "pointer", fontWeight: 700, lineHeight: 1,
                padding: "4px 9px", fontSize: [11, 13, 15][i],
                background: density === d ? "#1A8754" : "transparent",
                color: density === d ? "#fff" : "#5b6b62",
              }}>{label}</button>
          ))}
        </div>

        <span style={{ marginLeft: "auto", fontSize: 12.5, color: "#5b6b62" }}>
          {saving ?? t("sheet.count", { defaultValue: "{{count}} transactions", count: visible.length })}
        </span>
      </div>

      <div style={{ flex: 1, minHeight: 0,
                    border: tab === "sheet" ? "1px solid #e3ded2" : "0",
                    borderRadius: 10, overflow: "hidden" }}>
        {tab === "rules"
          ? <RulesSheet seedKeyword={ruleSeed} onSeedUsed={() => setRuleSeed(null)} />
          : tab === "pivot"
          ? <div style={{ height: "100%", overflowY: "auto" }}><Pivot account={account || undefined} /></div>
          : rows === null
          ? <p style={{ padding: 16, color: "#5b6b62" }}>{t("common.loading", { defaultValue: "Loading…" })}</p>
          : <RevoGrid
              ref={attachGrid as any}
              style={{ height: "100%", fontSize: `${DENSITY.font}px` }}
              theme="compact"
              columns={columns as any}
              source={visible as any}
              resize
              range
              canFocus
              rowSize={DENSITY.row}
              rowHeaders={true}   /* números de fila: para poder decir "la fila 47" al reportar */
              rowClass="_rowClass"
              editors={categoryEditors as any}
            />}
      </div>

      <p style={{ fontSize: 12, color: "#5b6b62", marginTop: 8 }}>
        {t("sheet.hint", {
          defaultValue:
            "Double-click or press Enter to edit · Tab / Enter / arrows to move · drag to select a range. " +
            "Editing a cell only changes that transaction — it does not rewrite your rules.",
        })}
      </p>
    </div>
  );
}
