import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import i18n from "../i18n";
import { RevoGrid } from "@revolist/react-datagrid";
import {
  getSheetRows, getCategories, updateTransaction, undoTransactionChange, getLabels, createLabel, followRule, deleteRule,
  decideBankRemoved, getBankConnections, syncBankConnection, BankApiError, getTransactionFlags,
  type FlagLevel,
  type BankConnection, type BankSync,
  setTransactionLabels, applyCellFormat, type ChartOfAccounts, previewRule, currentCompanyId,
} from "../api";
import type { CellFormat, SheetLabel, SheetRow } from "../types";
import { CATEGORY_EDITOR, categoryEditors, setEditorChart } from "./CategoryEditor";
import RuleDialog from "./RuleDialog";
import {
  TEXT_EDITOR, textEditors, useUndo, useUndoShortcut, useExternalClipboardGuard, useCaseStyle, applyCase,
  CellMenu, CellViewer, cellFromEvent, FormulaBar, MenuBar, type Menu, type MenuItem, type CaseStyle,
} from "./gridKit";
import type { View } from "./Sidebar";
import { SyncSummary, sumSyncs } from "./BankConnections";
import TransactionPanel from "./TransactionPanel";
import { BTN_PRIMARY, PageHeader } from "./PageKit";

/** "hace 5 minutos" / "5 minutes ago" — `iso` es UTC sin zona (como lo guarda el backend). */
function timeAgo(iso: string, lang: string): string {
  const sec = (Date.parse(iso + "Z") - Date.now()) / 1000;
  const rtf = new Intl.RelativeTimeFormat(lang, { numeric: "auto" });
  const a = Math.abs(sec);
  if (a < 3600) return rtf.format(Math.round(sec / 60), "minute");
  if (a < 86400) return rtf.format(Math.round(sec / 3600), "hour");
  return rtf.format(Math.round(sec / 86400), "day");
}

const SHEET_EDITORS = { ...categoryEditors, ...textEditors };
import Pivot from "./Pivot";
import { emptySpot, restoreSpot, trackScroll, rememberedDateFilter, rememberDateFilter, type GridSpot } from "./gridMemory";
import { DateFilterButton, dateRangeOf, inRange, type DateFilter } from "./DateFilter";
import NoteQuickAdd from "./NoteQuickAdd";
import RulesSheet, { type RulesCommands } from "./RulesSheet";

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


// EL PUNTITO de la fila (13-oct, Firm): rojo = problema · ámbar = por revisar · azul = observación. El
// detalle vive en el PANEL LATERAL (`TransactionPanel`), que reemplaza a la celda Observación.
const FLAG_COLOR: Record<FlagLevel, string> = { problem: "#f43f5e", review: "#f59e0b", info: "#0ea5e9" };

// Va ANTES que `rowCss` en el DOM: con la misma especificidad, el resaltado de fila gana.
const MANUAL_CSS = ".rgCell.cb-manual{background:rgba(250,204,21,.18)!important}"
  // BUSCAR: lo encontrado se resalta en ámbar; la coincidencia activa, con borde (como Excel).
  + ".rgCell.cb-hit{background:rgba(251,191,36,.28)!important}"
  + ".rgCell.cb-hit-cur{background:rgba(251,191,36,.55)!important;box-shadow:inset 0 0 0 2px #d97706}";

// LAS COLUMNAS LLENAN EL ANCHO (18-oct, ronda de diseño): desde que la Observación salió el 13-oct,
// ese espacio quedaba en blanco a la derecha de las columnas. Anchos base (iguales a los de antes)
// de las 4 columnas que NO crecen; TODO el sobrante del contenedor va a Descripción (19-oct: la columna
// Nota salió; la nota se escribe en el panel lateral y la fila con nota lleva un 📝 en su Descripción).
const FIXED_COLS_WIDTH = 116 + 120 + 190 + 165; // date + amount + category + subcategory
const BASE_DESC_SIZE = 360;
// Ancho aproximado de lo que RevoGrid dibuja fuera de las columnas (números de fila + la barra de
// scroll): no hay una API para leerlo exacto, así que es una estimación — de sobrar unos px, quedan
// en blanco como antes (nunca un scroll horizontal nuevo); verificado contra
// `calculateRowHeaderSize()` de RevoGrid 4.28 (mínimo 50 px hasta los 9,999 filas) + ~17 px de scroll.
const ROW_HEADER_AND_SCROLLBAR = 67;

/** Preferencia de vista recordada en este navegador (si no hay almacenamiento, solo esta sesión). */
function usePref(key: string, initial: boolean): [boolean, (v: boolean) => void] {
  const [v, setV] = useState<boolean>(() => {
    try { const x = localStorage.getItem(key); return x == null ? initial : x === "1"; } catch { return initial; }
  });
  const set = useCallback((next: boolean) => {
    setV(next);
    try { localStorage.setItem(key, next ? "1" : "0"); } catch { /* modo privado */ }
  }, [key]);
  return [v, set];
}

/** La hoja como CSV que Excel abre directo (BOM + CRLF). Lo que se ve: con el filtro aplicado. */
function downloadSheetCsv(rows: SheetRow[]) {
  const cell = (v: unknown) => { const x = String(v ?? ""); return /[",\r\n]/.test(x) ? `"${x.replace(/"/g, '""')}"` : x; };
  const head = ["Date", "Description", "Added text", "Amount", "Section", "Category", "Subcategory", "Account", "Note"];
  const lines = rows.map((r) => [
    r.date, r.description, r.description_extra ?? "", (r.is_inflow ? "" : "-") + Number(r.amount ?? 0).toFixed(2),
    r.account_type ?? "", r.category ?? "", r.subcategory ?? "", r.bank_account ?? "", r.note ?? "",
  ]);
  const csv = "\uFEFF" + [head, ...lines].map((l) => l.map(cell).join(",")).join("\r\n") + "\r\n";
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = `bookkeeping-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

/** El texto de cada columna VISIBLE en el que busca Ctrl+F (lo que el bookkeeper lee en la celda). */
const SEARCH_PROPS = ["date", "description_extra", "amount", "category", "subcategory"] as const;
function searchText(r: SheetRow, prop: string): string {
  switch (prop) {
    case "date": return r.date ? `${r.date} ${new Date(r.date + "T00:00:00").toLocaleDateString("en-US", { month: "short", day: "2-digit", year: "numeric" })}` : "";
    case "description_extra": return [r.description, r.description_extra].filter(Boolean).join(" - ");
    case "amount": {
      const n = Number(r.amount ?? 0), sign = r.is_inflow ? "" : "-";
      return `${sign}${n.toFixed(2)} ${sign}$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    }
    default: return String((r as any)[prop] ?? "");
  }
}
/** Lo que COPIA una celda (Ctrl+C y el menú "Copiar"): lo que se ve, listo para pegar en Excel o Sheets.
 *  RevoGrid copiaría el valor crudo (`item[prop]`): de Descripción solo el texto agregado, del monto la
 *  magnitud sin signo, de la fecha el ISO. El monto va como NÚMERO (guion ASCII, sin "$" ni miles) para que
 *  la hoja de cálculo lo lea como número; la fecha, MM/DD/YYYY. Sin tabs ni saltos: romperían las columnas. */
function copyText(r: SheetRow, prop: string, caseStyle: CaseStyle, pending: string): string {
  let text: string;
  switch (prop) {
    case "date": {
      const [y, m, d] = String(r.date ?? "").split("-");
      text = y && m && d ? `${m}/${d.slice(0, 2)}/${y}` : "";
      break;
    }
    case "description_extra": text = [r.description, r.description_extra].filter(Boolean).join(" - "); break;
    case "amount": {
      const n = Number(r.amount ?? 0);
      text = (r.is_inflow || !n ? "" : "-") + n.toFixed(2);
      break;
    }
    case "category": text = r.category ? applyCase(r.category, caseStyle) : pending; break;
    case "subcategory": text = applyCase(r.subcategory, caseStyle); break;
    default: text = String((r as any)[prop] ?? "");
  }
  return text.replace(/[\t\r\n]+/g, " ");
}

/** La búsqueda encuentra la fila por sus columnas, su cuenta o SU NOTA (19-oct: la nota ya no es columna,
 *  pero se sigue buscando; la coincidencia se marca en la Descripción, donde está el 📝). */
const noteHas = (r: SheetRow, q: string) => (r.note ?? "").toLowerCase().includes(q);
const matchesSearch = (r: SheetRow, q: string) =>
  SEARCH_PROPS.some((p) => searchText(r, p).toLowerCase().includes(q)) ||
  (r.bank_account ?? "").toLowerCase().includes(q) || noteHas(r, q);

/** IR A UNA TRANSACCIÓN desde otra pantalla (Auditoría, 14-oct). `n` cambia en cada pedido, así el
 *  mismo id pedido dos veces vuelve a llevar ahí. Sin `id` y con `onlyPending` (el Resumen): la hoja
 *  abre con el filtro "Pendientes" y en la primera fila Pendiente. */
export type SheetFocus = { id: number | null; onlyPending?: boolean; n: number };

export default function Sheet({ onNavigate, focusTx, onFocusTxDone }: {
  onNavigate?: (v: View) => void;
  focusTx?: SheetFocus | null;   // la fila a la que hay que llevar al bookkeeper al abrir
  onFocusTxDone?: () => void;
} = {}) {
  const { t } = useTranslation();
  const [rows, setRows] = useState<SheetRow[] | null>(null);
  const [chart, setChart] = useState<ChartOfAccounts>({});
  // Ventana "Crear regla" (4-oct): `seed` = la fila de la que parte, o null para una regla en blanco.
  const [ruleDialog, setRuleDialog] = useState<{ seed: SheetRow | null } | null>(null);
  // CUENTAS con casillas (Jose, 4-oct): ver 2 o más a la vez. Vacío = todas. El Pivote usa el mismo.
  const [acctSel, setAcctSel] = useState<string[]>([]);
  const [acctOpen, setAcctOpen] = useState(false);
  // FECHAS (Jose, 19-oct): junto a las cuentas y con ellas; también filtra el Pivote. Se recuerda por libro
  // mientras no se recargue la página (`gridMemory.ts`), aunque la Hoja se desmonte al ir a otra pantalla.
  const [dateFilter, setDateFilterState] = useState<DateFilter>(() => rememberedDateFilter(currentCompanyId()));
  const setDateFilter = useCallback((f: DateFilter) => { setDateFilterState(f); rememberDateFilter(currentCompanyId(), f); }, []);
  const dateRange = dateRangeOf(dateFilter);
  // NOTA DEL LIBRO desde la Hoja (Jose, 19-oct; `NoteQuickAdd` es de Auditoría/IA): `merchant` = el comercio
  // de la fila (clic derecho) o null (menú Edición y botón 📌).
  const [quickNote, setQuickNote] = useState<{ merchant: string | null } | null>(null);
  // "IR A LA REGLA" desde una fila (clic derecho): la pestaña Reglas abre con esa regla a la vista.
  // `n` cambia en cada pedido, así dos clics seguidos a la misma regla vuelven a llevar ahí.
  const [goRule, setGoRule] = useState<{ id: number; n: number } | null>(null);
  // BUSCAR (Ctrl+F, como en Excel): qué coincidencia está activa ("N de M").
  const [hit, setHit] = useState(0);
  const [onlyFlagged, setOnlyFlagged] = useState(false);
  const [onlyPending, setOnlyPending] = useState(false);
  const [search, setSearch] = useState("");
  const searchRef = useRef<HTMLInputElement | null>(null);
  const lastSearchRef = useRef("");  // Enter con la misma búsqueda = siguiente; con una nueva = la primera
  const [saving, setSaving] = useState<string | null>(null);
  // MENÚ VER (Jose, 4-oct): la barra de botones, la barra de fórmulas y fijar Fecha + Descripción
  // a la izquierda se pueden ver / ocultar. Todo lo de los botones también está en los menús.
  const [toolbar, setToolbar] = usePref("contabu.toolbar", true);
  const [showFormula, setShowFormula] = usePref("contabu.formulaBar", true);
  const [pinDesc, setPinDesc] = usePref("contabu.pinDescription", false);
  // El NÚMERO de prioridad de las reglas (14-oct): escondido; quién gana se decide en la columna Choques.
  const [showPriority, setShowPriority] = usePref("contabu.rules.showPriority", false);
  // Lo que la hoja de Reglas expone a los menús (cuando esa pestaña está abierta).
  // (Llega en cada render de Reglas: se guarda en un ref y solo se re-pinta si cambió lo que se ve
  // en los menús — con un estado de objeto sería un bucle de renders.)
  const rulesCmdsRef = useRef<RulesCommands | null>(null);
  const [, setRulesSig] = useState("");
  const pendingRulesCmd = useRef<((c: RulesCommands) => void) | null>(null);
  const onRulesCommands = useCallback((c: RulesCommands | null) => {
    rulesCmdsRef.current = c;
    setRulesSig(c ? `${c.canUndo}|${c.lastUndo}|${c.selectedCount}|${c.canExport}|${c.canDelete}|${c.formulaOpen}` : "");
    if (c && pendingRulesCmd.current) { const run = pendingRulesCmd.current; pendingRulesCmd.current = null; run(c); }
  }, []);
  // DENSIDAD: cuántas transacciones ve el bookkeeper de un vistazo. Se recuerda por navegador.
  const [labels, setLabels] = useState<SheetLabel[]>([]);
  // Filas seleccionadas: el formato y las etiquetas se aplican sobre la selección, como en Excel.
  const [selected, setSelected] = useState<number[]>([]);
  // BARRA DE FÓRMULAS (2-oct, pedido de Jose): la celda enfocada — id de la fila, columna y número
  // de fila visible — para mostrar su contenido COMPLETO arriba de la hoja, como en Excel.
  const [focus, setFocus] = useState<{ id: number; prop: string; row: number } | null>(null);
  // EL PANEL LATERAL (13-oct, Firm): sigue a la fila enfocada. × o Esc lo cierran para ESA fila; al
  // elegir otra, se abre de nuevo. En Ver se puede apagar del todo (para editar a lo ancho).
  const [flags, setFlags] = useState<Record<number, FlagLevel>>({});
  const [flagsKey, setFlagsKey] = useState(0);
  const [panelOnSelect, setPanelOnSelect] = usePref("contabu.detailsPanel", true);
  const [panelClosedFor, setPanelClosedFor] = useState<number | null>(null);
  // Al llegar desde Auditoría, el panel se abre para ESA fila aunque en Ver esté apagado.
  const [panelForcedFor, setPanelForcedFor] = useState<number | null>(null);
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
  // Los puntitos se recalculan con cada recarga de la hoja (una edición puede resolver un hallazgo).
  const loadFlags = useCallback(() => {
    getTransactionFlags()
      .then((f) => { setFlags(Object.fromEntries(Object.entries(f).map(([k, v]) => [Number(k), v]))); setFlagsKey((n) => n + 1); })
      .catch(() => setFlags({}));
  }, []);
  useEffect(() => { if (rows) loadFlags(); }, [rows, loadFlags]);

  // ↻ ACTUALIZAR BANCO (Jose, 4-oct): re-sincroniza TODAS las conexiones activas del libro desde la
  // hoja, por la misma ruta que el botón "Actualizar" de Bancos, y muestra el mismo resumen. Sin banco
  // vinculado (o sin Plaid configurado) no aparece. Una conexión caída lleva a Bancos a reconectar.
  const [bankConns, setBankConns] = useState<BankConnection[]>([]);
  const [bankBusy, setBankBusy] = useState(false);
  const [bankNote, setBankNote] = useState<{ sync: BankSync | null; disconnected: number; failed: number } | null>(null);
  const [, setNowTick] = useState(0); // re-pinta "hace X" cada minuto
  const loadBank = useCallback(() => {
    getBankConnections().then((r) => setBankConns(r.config.enabled ? r.connections : [])).catch(() => setBankConns([]));
  }, []);
  useEffect(() => {
    loadBank();
    const id = window.setInterval(() => setNowTick((n) => n + 1), 60_000);
    return () => window.clearInterval(id);
  }, [loadBank]);
  const syncAllBanks = useCallback(async () => {
    setBankBusy(true); setBankNote(null);
    const results: BankSync[] = [];
    let disconnected = bankConns.filter((c) => c.status === "disconnected").length;
    let failed = 0;
    for (const c of bankConns.filter((c) => c.status !== "disconnected")) {
      try { results.push((await syncBankConnection(c.id)).sync); }
      catch (e) { if (e instanceof BankApiError && e.code === "bank_disconnected") disconnected++; else failed++; }
    }
    setBankNote({ sync: results.length ? sumSyncs(results) : null, disconnected, failed });
    setBankBusy(false);
    loadBank();
    load(); // la hoja se recarga sola con lo que entró
  }, [bankConns, loadBank, load]);
  // "Actualizado hace X": la conexión MÁS atrasada (si una nunca se sincronizó, eso manda).
  const bankStamps = bankConns.map((c) => c.last_synced_at);
  const oldestSync = bankStamps.includes(null) ? null
    : bankStamps.reduce<string | null>((m, s) => (m === null || (s as string) < m ? s : m), null);
  useEffect(() => {
    load();
    getCategories().then((c) => { setChart(c); setEditorChart(c); }).catch(() => {});
    getLabels().then(setLabels).catch(() => {});
  }, [load]);
  // Al VOLVER a la hoja desde Reglas o el Pivote, se recarga: un cambio en el reglamento re-alinea
  // movimientos (Jose, 4-oct: "modifiqué la subcategoría en Reglas y no veo el cambio en el libro").
  const prevTab = useRef(tab);
  useEffect(() => {
    if (tab === "sheet" && prevTab.current !== "sheet") {
      load();
      getCategories().then((c) => { setChart(c); setEditorChart(c); }).catch(() => {});
    }
    prevTab.current = tab;
  }, [tab, load]);

  // `t` de i18next cambia de identidad en cada render. Si el texto de la observación se calculara
  // dentro del useMemo de las filas, `source` sería un array NUEVO en cada render y RevoGrid
  // descartaría la sesión de edición abierta (bug encontrado 27-sep: el editor abría y no guardaba).
  // Por eso el idioma se resuelve aparte y las filas solo dependen de datos, no de `t`.
  const lang = i18n.language;


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

  // Lo que dejan ver las cuentas y las fechas: de aquí salen también los conteos de "Pendientes" y
  // "Por revisar", para que el número del botón sea el que se ve al pulsarlo.
  const scoped = useMemo(() => {
    let out = rows ?? [];
    if (acctSel.length) out = out.filter((r) => acctSel.includes(r.bank_account ?? ""));
    if (dateRange) out = out.filter((r) => inRange(r.date, dateRange));
    return out;
  }, [rows, acctSel, dateRange?.from, dateRange?.to]); // eslint-disable-line react-hooks/exhaustive-deps

  const visible = useMemo(() => {
    let out = scoped;
    if (onlyFlagged) out = out.filter((r) => flags[r.id]);
    if (onlyPending) out = out.filter((r) => !r.category);
    // Busca en TODAS las columnas visibles + la cuenta (Jose, 4-oct) + la nota (19-oct).
    const q = search.trim().toLowerCase();
    if (q) out = out.filter((r) => matchesSearch(r, q));
    // La grilla necesita los valores ya planos para pintarlos y editarlos.
    // `_rowClass` pinta la fila COMPLETA (borde a borde). Estilar dentro de cada `cellTemplate`
    // no alcanzaba: dejaba sin color la columna de observación y el espacio sobrante a la derecha.
    return out.map((r) => ({
      ...r,
      _flag: flags[r.id] ?? "",
      _rowClass: rowClassOf(r),
    }));
  }, [scoped, onlyFlagged, onlyPending, search, lang, flags]); // eslint-disable-line react-hooks/exhaustive-deps

  // Las CELDAS que coinciden, en orden de lectura (fila por fila, de izquierda a derecha): son las que
  // se resaltan y por las que se navega con Enter / Shift+Enter ("N de M").
  // Lo hallado SOLO en la nota se marca en la Descripción (donde está el 📝) con `note: true`: al ir ahí
  // se abre el panel, que es donde se lee la nota.
  const hits = useMemo(() => {
    const q = search.trim().toLowerCase();
    type Hit = { id: number; prop: string; y: number; note: boolean };
    if (!q) return [] as Hit[];
    const out: Hit[] = [];
    visible.forEach((r, y) => {
      for (const p of SEARCH_PROPS) {
        const own = searchText(r, p).toLowerCase().includes(q);
        const inNote = p === "description_extra" && !own && noteHas(r, q);
        if (own || inNote) out.push({ id: r.id, prop: p, y, note: inNote });
      }
    });
    return out;
  }, [visible, search]);
  useEffect(() => { setHit(0); }, [search]);

  const flagged = scoped.filter((r) => flags[r.id]).length;
  const pending = scoped.filter((r) => !r.category).length;

  /** Una edición: se manda a la base y se recarga. Si falla, se revierte recargando. */
  const { push: pushUndo, undo, canUndo, last: lastUndo } = useUndo((m) => setError(
    m === "changed_since"
      ? t("sheet.undoChanged", { defaultValue: "That row changed after this edit, so it wasn't undone." })
      : m));
  const [caseStyle, setCaseStyle] = useCaseStyle();
  // Para el Ctrl+C de la grilla (un listener que se pone una vez): el texto con el estilo y el idioma de AHORA.
  const cellCopyText = (r: SheetRow, prop: string) =>
    copyText(r, prop, caseStyle, t("sheet.pending", { defaultValue: "Pending" }));
  const cellCopyRef = useRef(cellCopyText);
  cellCopyRef.current = cellCopyText;
  /** Deshacer una corrección de categoría = el historial del servidor (vuelve EXACTA, con su ƒ). */
  const undoCategorization = useCallback((id: number, label: string) => pushUndo({
    label, run: async () => { await undoTransactionChange(id); load(); },
  }), [pushUndo, load]);

  /** Guarda UNA celda (edición, pegado, borrado o "Pegar" del menú) y deja cómo deshacerla. */
  const saveCell = useCallback(async (model: any, prop: string, raw: unknown, reload = true): Promise<boolean> => {
    if (!model?.id || !["category", "subcategory", "description_extra"].includes(prop)) return false;
    const value = String(raw ?? "").trim();
    // OJO: RevoGrid ya aplicó el valor nuevo a `model` ANTES de disparar `afteredit`, así que
    // comparar contra `model[prop]` siempre da igual y descartaría toda edición (bug 27-sep).
    // El valor ANTERIOR real está en el estado de React, que es la copia de lo que hay en la base.
    const row = rowsRef.current?.find((r) => r.id === model.id);
    const before = row?.[prop as "category" | "subcategory" | "description_extra"];
    if (value === (before ?? "")) return false;
    // Supr sobre un rango no "borra" categorías: quedarían en "" (no es Pendiente de verdad).
    if (prop === "category" && !value) { load(); return false; }
    const payload: Record<string, unknown> = { [prop]: value, rule_action: "only_this" };
    if (prop === "category") {
      const section = Object.entries(chartRef.current).find(([, cats]) => cats.includes(value))?.[0];
      if (!section && value) {
        // Pegar un texto que no es una cuenta del plan: no se inventan cuentas (candado del plan).
        setError(t("sheet.notInChart", { defaultValue: "“{{v}}” isn't an account in this book's chart.", v: value }));
        load();
        return false;
      }
      if (section) payload.account_type = section;
    }
    setSaving(t("sheet.saving", { defaultValue: "Saving…" }));
    try {
      // "only_this" = excepción puntual: editar una celda NO reescribe el reglamento.
      // Cambiar la regla es una acción explícita, no un efecto secundario de escribir en la hoja.
      await updateTransaction(model.id, payload as any);
      const what = (row?.description ?? "").slice(0, 28);
      if (prop === "category" || prop === "subcategory") {
        undoCategorization(model.id, t("sheet.undoCell", { defaultValue: "{{col}} of {{what}}", col: t(`sheet.${prop}`), what }));
      } else {
        const prev = before ?? "";
        pushUndo({ label: t("sheet.undoCell", { defaultValue: "{{col}} of {{what}}", col: t("sheet.description"), what }),
                   run: async () => { await updateTransaction(model.id, { [prop]: prev } as any); load(); } });
      }
      if (reload) load();
      setSaving(null);
      return true;
    } catch (err) {
      setError(String((err as Error)?.message ?? err));
      setSaving(null);
      load(); // revierte: la base manda
      return false;
    }
  }, [load, t, pushUndo, undoCategorization]);

  /** Una celda ({prop, val, model}) o un RANGO ({data, models}: Ctrl+V sobre varias, Supr). Antes
   *  el rango cambiaba la pantalla pero NO se guardaba. */
  const onEdit = useCallback(async (e: any) => {
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
  const visibleRef = useRef<SheetRow[]>([]);
  useEffect(() => { visibleRef.current = visible; }, [visible]);

  const onEditRef = useRef(onEdit);
  useEffect(() => { onEditRef.current = onEdit; }, [onEdit]);

  // DÓNDE QUEDÓ LA HOJA (Jose, 19-oct): el desplazamiento se anota aquí mientras se usa y se pone igual al
  // volver de Reglas o del Pivote (`gridMemory.ts`). La fila enfocada ya la recuerda `focus`.
  const sheetSpot = useRef<GridSpot>(emptySpot());
  const rulesSpot = useRef<GridSpot>(emptySpot());   // la de Reglas, que sí se desmonta: se le presta
  const restoringSheet = useRef(false);
  const [gridMounts, setGridMounts] = useState(0);
  const lastGridEl = useRef<HTMLElement | null>(null);

  const attachGrid = useCallback((el: HTMLElement | null) => {
    const prev = gridRef.current as any;
    if (prev?.__contabuScroll) { prev.__contabuScroll(); delete prev.__contabuScroll; }
    if (prev?.__contabuHandler) {
      prev.removeEventListener("afteredit", prev.__contabuHandler);
      delete prev.__contabuHandler;
    }
    if (prev?.__contabuRange) {
      prev.removeEventListener("afterfocus", prev.__contabuRange);
      prev.removeEventListener("selectionchanged", prev.__contabuRange);
      delete prev.__contabuRange;
    }
    if (prev?.__contabuFocus) {
      prev.removeEventListener("afterfocus", prev.__contabuFocus);
      delete prev.__contabuFocus;
    }
    if (prev?.__contabuColResize) {
      prev.removeEventListener("aftercolumnresize", prev.__contabuColResize);
      delete prev.__contabuColResize;
    }
    if (prev?.__contabuCopy) {
      prev.removeEventListener("clipboardrangecopy", prev.__contabuCopy);
      delete prev.__contabuCopy;
    }
    gridRef.current = el;
    if (!el) return;
    // El wrapper de React vuelve a llamar a este `ref` en cada render (null y luego el MISMO elemento): solo
    // un elemento que no habíamos visto es una grilla recién montada (si no, el contador entra en bucle).
    if (el !== lastGridEl.current) {
      lastGridEl.current = el;
      restoringSheet.current = true;   // recién montada, en 0: no anotar hasta ponerla donde estaba
      setGridMounts((n) => n + 1);
    }
    (el as any).__contabuScroll = trackScroll(el, sheetSpot, () => restoringSheet.current);
    const handler = (e: Event) => void onEditRef.current(e as CustomEvent);
    (el as any).__contabuHandler = handler;
    el.addEventListener("afteredit", handler);

    // El bookkeeper sigue pudiendo arrastrar el borde de Descripción: ese ancho a mano gana sobre el
    // reparto automático del sobrante (18-oct).
    const onColResize = (e: Event) => {
      const cols = Object.values((e as CustomEvent).detail ?? {}) as { prop?: string; size?: number }[];
      const desc = cols.find((c) => c.prop === "description_extra" && c.size);
      if (desc?.size) setManualDescSize(desc.size);
    };
    (el as any).__contabuColResize = onColResize;
    el.addEventListener("aftercolumnresize", onColResize);

    // CTRL+C (19-oct): RevoGrid copia el valor CRUDO de cada columna (`copyRangeArray` → `item[prop]`) y deja
    // cambiar `detail.data` antes de escribir el portapapeles (`onCopy` lo lee después de emitir). Aquí va,
    // celda por celda, lo que se ve (`copyText`). Filas y columnas salen de los almacenes del overlay que copia,
    // igual que en RevoGrid: es el de SU zona (columnas fijadas o no) y trae el orden y el filtro de la grilla.
    const onCopy = (e: Event) => {
      const d = (e as CustomEvent).detail;
      const overlay = e.composedPath()[0] as any;
      const shown = (store: any): any[] => {
        const src = store.get("source") ?? [];
        return (store.get("items") ?? []).map((i: number) => src[i]);
      };
      try {
        const r = d?.range;
        if (!r || !overlay?.dataStore || !overlay?.colData) return;
        const models = shown(overlay.dataStore);
        const props = shown(overlay.colData).slice(r.x, r.x1 + 1).map((c: any) => String(c?.prop));
        const data: string[][] = [];
        for (let y = r.y; y <= r.y1; y++) {
          const m = models[y];
          data.push(m ? props.map((p: string) => cellCopyRef.current(m, p)) : []);
        }
        d.data = data;
      } catch { /* algo cambió dentro de RevoGrid: se queda su copia cruda */ }
    };
    (el as any).__contabuCopy = onCopy;
    el.addEventListener("clipboardrangecopy", onCopy);

    // Selección: el rango que el bookkeeper marcó arrastrando (o la celda enfocada).
    // 19-oct: el índice es el de la fila EN LA GRILLA (filtrada y ordenada); antes se buscaba en todas las
    // filas del libro y, con un filtro o un orden puesto, la selección caía en OTRAS transacciones.
    const onRange = async (e: Event) => {
      const d = (e as CustomEvent).detail ?? {};
      const a = d.y ?? d.rowIndex, b = d.y1 ?? d.y ?? d.rowIndex;
      if (a == null) return;
      const [lo, hi] = [Math.min(a, b), Math.max(a, b)];
      let shown: any[] = [];
      try { shown = (await (el as any).getVisibleSource?.()) ?? []; } catch { /* aún no lista */ }
      if (!shown.length) shown = visibleRef.current;
      setSelected(shown.slice(lo, hi + 1).map((r) => r?.id).filter((id): id is number => id != null));
    };
    // La celda ENFOCADA (no el rango): de aquí sale la barra de fórmulas.
    const onFocus = (e: Event) => {
      const d = (e as CustomEvent).detail ?? {};
      const id = d.model?.id;
      const prop = d.column?.prop;
      if (id == null || !prop) return;
      setFocus({ id, prop: String(prop), row: (d.rowIndex ?? 0) + 1 });
    };
    (el as any).__contabuFocus = onFocus;
    el.addEventListener("afterfocus", onFocus);
    (el as any).__contabuRange = onRange;
    el.addEventListener("afterfocus", onRange);
    el.addEventListener("selectionchanged", onRange);
  }, []);


  /** Formato sobre la selección. `column: null` = la fila entera (es lo que un bookkeeper
   *  espera al resaltar: se marca el movimiento, no una casilla suelta). */
  const format = useCallback(async (patch: Omit<Parameters<typeof applyCellFormat>[0], "transaction_ids">) => {
    if (!selected.length) return;
    // Para DESHACER: el formato de fila que tenía cada una antes (Jose, 4-oct: todo deja su deshacer).
    const before = (rowsRef.current ?? []).filter((r) => selected.includes(r.id))
      .map((r) => ({ id: r.id, fmt: { ...(r.formats?._row ?? {}) } as CellFormat }));
    setSaving(t("sheet.saving", { defaultValue: "Saving…" }));
    try {
      await applyCellFormat({ transaction_ids: selected, column: null, ...patch });
      pushUndo({ label: t("sheet.undoFormat", { defaultValue: "format {{count}} rows", count: before.length }),
                 run: async () => {
                   for (const b of before) {
                     await applyCellFormat({ transaction_ids: [b.id], column: null, clear: true });
                     if (Object.keys(b.fmt).length) await applyCellFormat({ transaction_ids: [b.id], column: null, ...b.fmt });
                   }
                   load();
                 } });
      load();
    } catch (err) { setError(String((err as Error)?.message ?? err)); }
    finally { setSaving(null); }
  }, [selected, load, t, pushUndo]);

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
      undoCategorization(id, t("sheet.undoFollow", { defaultValue: "follow the rule" }));
      load();
    } catch (err) {
      setError((err as Error).message === "no-rule"
        ? t("sheet.noRule", { defaultValue: "No rule covers this transaction. Create one in the Rules tab." })
        : String((err as Error).message));
    } finally { setSaving(null); }
  }, [load, t, undoCategorization]);
  const backToRuleRef = useRef(backToRule);
  useEffect(() => { backToRuleRef.current = backToRule; }, [backToRule]);

  // ESCRITO A MANO = AMARILLO TENUE (Jose, 3-oct-2026). Una fila `manual` está desconectada de las
  // reglas (ninguna la toca), y eso tiene que verse sin pasar el mouse: se tiñen sus celdas de
  // categoría y subcategoría. Un resaltado de fila que ponga el bookkeeper gana sobre este tinte.
  const manualCell = (p: any) => (p?.model?.source === "manual" ? { class: { "cb-manual": true } } : {});

  // El sobrante de ancho sobre las columnas fijas (lo mide un ResizeObserver más abajo, sobre `hostRef`)
  // va entero a Descripción; lo que el bookkeeper fijó A MANO arrastrando su borde gana. RevoGrid 4.28
  // trae un plugin de `stretch`, pero verificado en su código (`column.stretch.plugin`) que solo estira
  // la ÚLTIMA columna, y Descripción es la segunda: por eso se mide aquí.
  const [flexExtra, setFlexExtra] = useState(0);
  const [manualDescSize, setManualDescSize] = useState<number | null>(null);
  const descSize = manualDescSize ?? (BASE_DESC_SIZE + flexExtra);

  // ABRIR EL PANEL EN UNA FILA aunque en Ver esté apagado (clic derecho → "Nota…", el 📝, una
  // coincidencia de la búsqueda que está solo en la nota): es donde se lee y se escribe la nota (19-oct).
  const openPanelFor = useCallback((id: number, prop: string, y: number) => {
    setFocus({ id, prop, row: y + 1 });
    setSelected([id]);
    setPanelClosedFor(null);
    setPanelForcedFor(id);
  }, []);
  const openPanelRef = useRef(openPanelFor);
  useEffect(() => { openPanelRef.current = openPanelFor; }, [openPanelFor]);

  const columns = useMemo(() => ([
    { prop: "date", name: t("sheet.date", { defaultValue: "Date" }), size: 116, sortable: true, readonly: true,
      // Fecha SIEMPRE en formato de EE.UU. (mes-día-año), sin importar el idioma de la UI: es dato
      // contable y la regla de oro manda que vaya en inglés. En `es` el navegador la daría al revés
      // (28 mar 2026) y confundiría al leer un extracto estadounidense.
      cellTemplate: (h: any, p: any) => {
        const date = h("span", { style: styleOf(p.model, "date") }, p.model.date
          ? new Date(p.model.date + "T00:00:00").toLocaleDateString("en-US", { month: "short", day: "2-digit", year: "numeric" })
          : "");
        // El PUNTITO (13-oct): qué filas tienen algo, de un vistazo. El detalle, en el panel lateral.
        const lvl = p.model._flag as FlagLevel | "";
        const dot = h("span", {
          title: lvl ? t(`panel.flagTitle.${lvl}`) : undefined,
          // En px COMO TEXTO: el `h` de RevoGrid descarta los números sueltos en `style`.
          style: { display: "inline-block", width: "8px", height: "8px", borderRadius: "999px", flex: "none",
                   marginInlineEnd: "6px", background: lvl ? FLAG_COLOR[lvl] : "transparent" },
        });
        return h("span", { style: { display: "flex", alignItems: "center" } }, dot, date);
      } },
    // La descripción del BANCO no se edita nunca (es la evidencia). Lo que se edita es
    // `description_extra`: el texto que el bookkeeper AGREGA — a quién fue el cheque, qué era esa
    // ACH críptica — y que ENTRA AL MATCHEO DE REGLAS. Se pinta distinto para que nunca haya duda
    // de qué dijo el banco y qué escribiste tú.
    { prop: "description_extra", name: t("sheet.description", { defaultValue: "Description" }),
      size: descSize, sortable: true, editor: TEXT_EDITOR,
      cellTemplate: (h: any, p: any) => {
        // El texto se corta con "…" y lo que va detrás (etiquetas, 📝) queda siempre a la vista.
        const text: any[] = [
          h("span", { style: styleOf(p.model, "description") }, p.model.description ?? ""),
        ];
        if (p.model.description_extra) {
          // " - " + el texto del bookkeeper, un poco TRANSLÚCIDO (Jose, 2-oct). Antes iba verde, en
          // negrita y tras una raya vertical. Sigue distinguiéndose del texto del banco (más claro y
          // tras el guion), que es el punto: nunca dudar de qué dijo el banco y qué escribiste tú.
          text.push(h("span", {
            title: t("sheet.yourText", { defaultValue: "Text you added — rules match on it too" }),
            style: { ...styleOf(p.model, "description"), opacity: 0.55, whiteSpace: "pre" },
          }, ` - ${p.model.description_extra}`));
        } else {
          text.push(h("span", {
            style: { marginInlineStart: "6px", color: "#c9c2b2", fontStyle: "italic", fontSize: "12px" },
          }, t("sheet.addText", { defaultValue: "+ add" })));
        }
        const kids: any[] = [h("span", { style: { flex: "0 1 auto", minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } }, ...text)];
        for (const l of (p.model.labels ?? []) as SheetLabel[]) {
          kids.push(h("span", {
            title: l.name,
            style: { marginInlineStart: "6px", background: l.color, color: "#fff", borderRadius: "4px",
                     padding: "0 6px", fontSize: "10.5px", fontWeight: 700, whiteSpace: "nowrap", flex: "none" },
          }, l.name));
        }
        // LA FILA TIENE NOTA (19-oct): una marca discreta, con la nota de tooltip; el clic abre el panel,
        // donde se lee entera y se edita. La columna Nota salió de la Hoja.
        if (p.model.note) {
          kids.push(h("span", {
            title: p.model.note, "aria-label": t("sheet.hasNote"),
            onClick: () => openPanelRef.current(p.model.id, "description_extra", p.rowIndex ?? 0),
            style: { marginInlineStart: "6px", fontSize: "11px", opacity: 0.7, cursor: "pointer", flex: "none" },
          }, "📝"));
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
      cellProperties: manualCell,
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
                   marginInlineEnd: "5px", fontSize: "12px" } }, "\u0192"));
        else if (manual) kids.push(h("button", {
          title: t("sheet.manualClick", {
            defaultValue: "Written by hand — click to follow the rule again" }),
          onClick: (e: MouseEvent) => { e.stopPropagation(); void backToRuleRef.current(p.model.id); },
          style: { border: 0, background: "transparent", cursor: "pointer", padding: 0,
                   color: "#b9b2a2", marginInlineEnd: "5px", fontSize: "11px", lineHeight: 1 },
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
              padding: "1px 8px", borderRadius: "5px", fontSize: "12.5px",
              border: "1px dashed rgba(220,38,38,.45)",
            },
          }, t("sheet.pending", { defaultValue: "Pending" }));
        }
        kids.push(h("span", { style: styleOf(p.model, "category") }, applyCase(p.model.category, caseStyle)));
        return h("span", { style: { display: "flex", alignItems: "center" } }, ...kids);
      } },
    { prop: "subcategory", name: t("sheet.subcategory", { defaultValue: "Subcategory" }), size: 165,
      cellProperties: manualCell, editor: TEXT_EDITOR,
      cellTemplate: (h: any, p: any) => h("span", { style: styleOf(p.model, "subcategory") }, applyCase(p.model.subcategory, caseStyle)) },
    // La NOTA de la fila ya no es columna (19-oct): se escribe en el panel lateral y aquí queda el 📝.
  ]), [t, caseStyle, descSize]);

  // BARRA DE FÓRMULAS: referencia (columna + fila) y el contenido COMPLETO de la celda enfocada.
  // Solo muestra; editar sigue siendo en la celda. En Descripción: "banco - texto agregado".
  const formula = useMemo(() => {
    if (!focus) return null;
    const r = visible.find((x) => x.id === focus.id);
    const col = (columns as any[]).find((c) => c.prop === focus.prop);
    if (!r || !col) return null;
    let text = "";
    switch (focus.prop) {
      case "date":
        text = r.date ? new Date(r.date + "T00:00:00").toLocaleDateString("en-US",
          { month: "short", day: "2-digit", year: "numeric" }) : "";
        break;
      case "description_extra":
        text = r.description_extra ? `${r.description ?? ""} - ${r.description_extra}` : (r.description ?? "");
        break;
      case "amount":
        text = (r.is_inflow ? "" : "−") + "$" + Number(r.amount ?? 0)
          .toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        break;
      case "category":
        text = r.category ?? t("sheet.pending", { defaultValue: "Pending" });
        break;
      default:
        text = String((r as any)[focus.prop] ?? "");
    }
    return { ref: `${col.name} · ${focus.row}`, text };
  }, [focus, visible, columns, t]);

  // EL PANEL LATERAL: la fila enfocada, salvo que se haya cerrado para ella (× o Esc).
  const panelTx = tab === "sheet" && focus && panelClosedFor !== focus.id
    && (panelOnSelect || panelForcedFor === focus.id) ? focus.id : null;
  // El cierre vale para la fila de AHORA: al pasar a otra, el panel vuelve a abrirse (también al volver).
  const focusId = focus?.id ?? null;
  useEffect(() => { setPanelClosedFor((c) => (c != null && focusId !== c ? null : c)); }, [focusId]);
  useEffect(() => {
    if (panelTx == null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      // Esc dentro de un editor (o del buscador) es de ESE campo: cancela la edición, no cierra el panel.
      const el = e.target as HTMLElement | null;
      if (el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName))) return;
      setPanelClosedFor(panelTx);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [panelTx]);

  // MENÚ DE CLIC DERECHO (Jose, 4-oct): ver, editar, copiar, pegar, deshacer, seguir la regla,
  // crear una regla desde la fila y cómo se ve el texto.
  const hostRef = useRef<HTMLDivElement | null>(null);
  useUndoShortcut(hostRef, () => void undo());
  useExternalClipboardGuard(hostRef);

  // Se recalcula cuando cambia el ancho DISPONIBLE: la ventana o el panel lateral de la transacción
  // que abre/cierra a su lado (los dos cambian el ancho de `hostRef`, no el de la ventana).
  useEffect(() => {
    const el = hostRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width ?? 0;
      setFlexExtra(Math.max(0, Math.round(w - FIXED_COLS_WIDTH - ROW_HEADER_AND_SCROLLBAR - BASE_DESC_SIZE)));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  /** La ventana de NOTA DEL LIBRO. Con una fila, el comercio va prellenado con la MISMA palabra clave que
   *  sugiere "Crear regla" (sin fechas, IDs ni números de rastreo): la nota caza por ese texto dentro de la
   *  descripción, y la descripción entera solo cazaría a esta fila. */
  const openBookNote = useCallback(async (row: SheetRow | null) => {
    if (!row) { setQuickNote({ merchant: null }); return; }
    let merchant = (row.description ?? "").slice(0, 80);
    try { const p = await previewRule({ transaction_id: row.id }); if (p.keyword) merchant = p.keyword.slice(0, 80); }
    catch { /* sin sugerencia: va la descripción del banco, editable */ }
    setQuickNote({ merchant });
  }, []);
  const [menu, setMenu] = useState<{ at: { x: number; y: number }; model: any; prop: string; row: number } | null>(null);
  const [viewer, setViewer] = useState<{ title: string; text: string } | null>(null);
  const onContextMenu = useCallback(async (e: React.MouseEvent) => {
    if (tab !== "sheet") return;
    e.preventDefault();
    const at = { x: e.clientX, y: e.clientY };
    const c = await cellFromEvent(e.nativeEvent);
    if (c) setMenu({ at, model: c.model, prop: c.prop, row: c.y });
  }, [tab]);
  const EDITABLE = ["category", "subcategory", "description_extra"];
  const sheetCellText = (m: any, prop: string) => prop === "description_extra"
    ? [m.description, m.description_extra].filter(Boolean).join(" - ")
    : String(m[prop] ?? "");
  const menuItems = (m: any, prop: string, row: number): MenuItem[] => {
    const colName = (columns as any[]).find((c) => c.prop === prop)?.name ?? prop;
    const can = EDITABLE.includes(prop);
    const items: MenuItem[] = [
      { label: t("sheet.menu.view", { defaultValue: "View full content" }),
        onClick: () => setViewer({ title: `${colName} · ${row + 1}`, text: sheetCellText(m, prop) }) },
      { label: t("sheet.menu.edit", { defaultValue: "Edit" }), hint: "Enter", disabled: !can,
        onClick: () => void (gridRef.current as any)?.setCellEdit?.(row, prop) },
      { label: t("sheet.menu.copy", { defaultValue: "Copy" }), hint: "Ctrl+C",
        onClick: () => void navigator.clipboard?.writeText(cellCopyText(m, prop)) },
      { label: t("sheet.menu.paste", { defaultValue: "Paste" }), hint: "Ctrl+V", disabled: !can,
        onClick: async () => {
          try { const txt = await navigator.clipboard.readText(); await saveCell(m, prop, txt.split(/\r?\n/)[0]); }
          catch { setError(t("sheet.menu.pasteBlocked", { defaultValue: "The browser didn't allow reading the clipboard. Use Ctrl+V." })); }
        } },
      "sep",
    ];
    if (m.source === "manual") items.push({ label: t("sheet.menu.followRule", { defaultValue: "Follow the rule again" }),
      onClick: () => void backToRule(m.id) });
    // IR A LA REGLA (Jose, 4-oct): la fórmula de esta celda, en la hoja de Reglas.
    if (m.source === "rule" && m.matched_rule_id != null) items.push({
      label: t("sheet.menu.goToRule", { defaultValue: "Go to the rule" }),
      onClick: () => { setGoRule({ id: m.matched_rule_id, n: Date.now() }); setTab("rules"); } });
    // El banco vinculado lo retiró: el bookkeeper decide (Movimientos, 4-oct).
    if (m.bank_removed_at) items.push(
      { label: t("sheet.menu.bankKeep", { defaultValue: "Keep it (clear the bank's mark)" }),
        onClick: async () => { try { await decideBankRemoved(m.id, "keep"); } catch (e) { setError(String((e as Error).message)); } load(); } },
      { label: t("sheet.menu.bankDelete", { defaultValue: "Delete it from the book" }),
        onClick: async () => {
          if (!window.confirm(t("sheet.menu.bankDeleteConfirm", { defaultValue: "Delete this transaction? The bank withdrew it. This can't be undone." }))) return;
          try { await decideBankRemoved(m.id, "delete"); } catch (e) { setError(String((e as Error).message)); }
          load();
        } },
    );
    items.push({ label: t("sheet.menu.newRule", { defaultValue: "Create a rule from this row" }),
      onClick: () => setRuleDialog({ seed: m as SheetRow }) });
    // LAS NOTAS (19-oct): la de ESTA fila se escribe en el panel; la del libro, en su ventana.
    items.push("sep",
      { label: t("sheet.menu.note"), onClick: () => openPanelFor(m.id, prop, row) },
      { label: t("sheet.menu.bookNote"), onClick: () => void openBookNote(m as SheetRow) });
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

  const TOOL: React.CSSProperties = {
    border: "1px solid #e3ded2", borderRadius: 5, background: "#fff", cursor: "pointer",
    fontSize: 12.5, padding: "2px 7px", lineHeight: 1.6, minWidth: 24,
  };

  const input: React.CSSProperties = {
    padding: "6px 10px", borderRadius: 8, border: "1px solid #d5d0c4",
    fontSize: 13.5, background: "#fff",
  };

  // Fijar Fecha + Descripción a la izquierda (menú Ver): no se van al desplazarse de lado.
  const hitKey = useMemo(() => new Set(hits.map((h) => `${h.id}|${h.prop}`)), [hits]);
  const curHit = hits[Math.min(hit, hits.length - 1)];
  const gridColumns = useMemo(() => (columns as any[]).map((c) => {
    let col = pinDesc && (c.prop === "date" || c.prop === "description_extra") ? { ...c, pin: "colPinStart" } : c;
    if (hitKey.size && (SEARCH_PROPS as readonly string[]).includes(c.prop)) {
      const base = c.cellProperties;
      col = { ...col, cellProperties: (p: any) => {
        const own = base ? base(p) : {};
        const key = `${p?.model?.id}|${c.prop}`;
        if (!hitKey.has(key)) return own;
        const cur = curHit && `${curHit.id}|${curHit.prop}` === key;
        return { ...own, class: { ...(own.class ?? {}), "cb-hit": true, "cb-hit-cur": !!cur } };
      } };
    }
    return col;
  }), [columns, pinDesc, hitKey, curHit]);

  /** Lleva la grilla a la coincidencia `i` (la desplaza y enfoca su celda). */
  const goHit = useCallback(async (i: number) => {
    if (!hits.length) return;
    const n = ((i % hits.length) + hits.length) % hits.length;
    setHit(n);
    const h = hits[n], grid = gridRef.current as any;
    if (!grid) return;
    const pinned = pinDesc ? ["date", "description_extra"] : [];
    const cols = (columns as any[]).map((c) => c.prop);
    const isPin = pinned.includes(h.prop);
    const x = isPin ? pinned.indexOf(h.prop) : cols.filter((p) => !pinned.includes(p)).indexOf(h.prop);
    try {
      await grid.scrollToRow?.(h.y);
      if (!isPin) await grid.scrollToColumnProp?.(h.prop);
      await grid.setCellsFocus?.({ x, y: h.y }, { x, y: h.y }, isPin ? "colPinStart" : "rgCol", "rgRow");
    } catch { /* la grilla aún no está lista */ }
    if (h.note) openPanelFor(h.id, h.prop, h.y);   // lo hallado está en la nota: se lee en el panel
  }, [hits, pinDesc, columns, openPanelFor]);

  // IR A LA TRANSACCIÓN (Jose, 14-oct; Buzón 8-oct): desde Auditoría se llega a la fila EXACTA.
  // Paso 1, con las filas ya cargadas: si no existe, se avisa; si existe, se quita SOLO lo que la
  // esconde (cuentas, búsqueda, "Pendientes", "Por revisar") y se deja apuntada para el paso 2.
  const [goTo, setGoTo] = useState<number | null>(null);
  useEffect(() => {
    if (!focusTx || !rows) return;
    onFocusTxDone?.();
    // Desde el Resumen: la 1ª Pendiente DENTRO de las fechas elegidas; si no hay ninguna, la 1ª de todas.
    const r = focusTx.id != null ? rows.find((x) => x.id === focusTx.id)
      : focusTx.onlyPending ? (rows.find((x) => !x.category && inRange(x.date, dateRange)) ?? rows.find((x) => !x.category))
      : undefined;
    if (focusTx.onlyPending) { setTab("sheet"); setOnlyPending(true); }
    if (!r) { if (focusTx.id != null) setError(t("sheet.focusGone")); return; }
    setTab("sheet");
    if (acctSel.length && !acctSel.includes(r.bank_account ?? "")) setAcctSel([]);
    if (!inRange(r.date, dateRange)) setDateFilter(null);
    if (onlyPending && r.category) setOnlyPending(false);
    if (onlyFlagged && !flags[r.id]) setOnlyFlagged(false);
    const q = search.trim().toLowerCase();
    if (q && !matchesSearch(r, q)) setSearch("");
    setGoTo(r.id);
  }, [focusTx, rows]); // eslint-disable-line react-hooks/exhaustive-deps

  // Paso 2, cuando la fila ya está entre las visibles y la grilla montada: desplazarse, seleccionarla
  // y abrir su panel. La posición sale de la grilla (respeta un orden por columna); si no, de `visible`.
  useEffect(() => {
    if (goTo == null || tab !== "sheet" || !visible.some((r) => r.id === goTo)) return;
    let alive = true, tries = 0;
    const run = async () => {
      const grid = gridRef.current as any;
      if (!grid?.scrollToRow) { if (alive && tries++ < 20) window.setTimeout(run, 50); return; }
      let y = visible.findIndex((r) => r.id === goTo);
      try {
        const shown: any[] = (await grid.getVisibleSource?.()) ?? [];
        const i = shown.findIndex((r) => r?.id === goTo);
        if (i >= 0) y = i;
      } catch { /* sin orden propio: vale el de `visible` */ }
      if (!alive) return;
      const pinned = pinDesc ? "colPinStart" : "rgCol";
      try {
        await grid.scrollToRow(y);
        await grid.setCellsFocus?.({ x: 0, y }, { x: 0, y }, pinned, "rgRow");
      } catch { /* la grilla aún no está lista: igual queda seleccionada abajo */ }
      setFocus({ id: goTo, prop: "date", row: y + 1 });
      setSelected([goTo]);
      setPanelClosedFor(null);
      setPanelForcedFor(goTo);
      setGoTo(null);
    };
    void run();
    return () => { alive = false; };
  }, [goTo, tab, visible, pinDesc]);

  // VOLVER A DONDE ESTABA: cada vez que la grilla se monta (al volver de Reglas o del Pivote), la celda
  // enfocada y el desplazamiento de antes. Si otra pantalla mandó a una fila exacta (`focusTx` → `goTo`),
  // gana ese pedido: no se restaura nada.
  useEffect(() => {
    if (!gridMounts) return;
    const spot = sheetSpot.current;
    if (focusTx || goTo != null || (spot.x === 0 && spot.y === 0 && focus == null)) {
      restoringSheet.current = false;
      return;
    }
    let alive = true;
    restoringSheet.current = true;
    const remembered = focus;
    const pinned = pinDesc ? ["date", "description_extra"] : [];
    const cols = (columns as any[]).map((c) => c.prop);
    void restoreSpot(() => gridRef.current, { ...spot, id: remembered?.id ?? null, prop: remembered?.prop ?? null },
      async (grid: any, y) => {
        const prop = remembered?.prop ?? "date";
        const isPin = pinned.includes(prop);
        const x = Math.max(0, isPin ? pinned.indexOf(prop) : cols.filter((c) => !pinned.includes(c)).indexOf(prop));
        await grid.setCellsFocus?.({ x, y }, { x, y }, isPin ? "colPinStart" : "rgCol", "rgRow");
      }, () => alive).finally(() => { if (alive) restoringSheet.current = false; });
    return () => { alive = false; };
  }, [gridMounts]); // eslint-disable-line react-hooks/exhaustive-deps

  // CTRL+F / CMD+F dentro de la hoja: enfoca NUESTRO buscador en vez del del navegador (como Excel).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey || e.key.toLowerCase() !== "f") return;
      if (tab !== "sheet") return;
      e.preventDefault();
      searchRef.current?.focus();
      searchRef.current?.select();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [tab]);

  // BARRA DE MENÚS (Jose, 4-oct): Archivo · Edición · Ver · Formato · Opciones, arriba de las pestañas.
  // Las acciones son las MISMAS de los botones y del clic derecho; en Reglas, las de esa hoja.
  const onRules = tab === "rules";
  const withRules = (run: (c: RulesCommands) => void) => {
    if (rulesCmdsRef.current && onRules) { run(rulesCmdsRef.current); return; }
    pendingRulesCmd.current = run;   // se abre Reglas y se ejecuta al montarse
    setTab("rules");
  };
  const rc = onRules ? rulesCmdsRef.current : null;
  const selRow = selected.length === 1 ? (rowsRef.current ?? []).find((r) => r.id === selected[0]) ?? null : null;
  const nSel = onRules ? (rc?.selectedCount ?? 0) : (tab === "sheet" ? selected.length : 0);
  const doFormat = (p: { bold?: boolean; italic?: boolean; bg?: string; clear?: boolean }) => {
    if (onRules) { rc?.restyle(p.clear ? "clear" : p); return; }
    void format(p);
  };
  const COLORS = [
    ["#f6c343", t("sheet.color.yellow", { defaultValue: "Yellow — check this" })],
    ["#5cc98f", t("sheet.color.green", { defaultValue: "Green — done / OK" })],
    ["#ef6f6f", t("sheet.color.red", { defaultValue: "Red — problem" })],
    ["#6aa9f0", t("sheet.color.blue", { defaultValue: "Blue — ask the client" })],
  ] as const;
  const undoLabel = (l: string | null) => l ? t("sheet.undoWhat", { defaultValue: "Undo: {{what}}", what: l }) : t("sheet.undo", { defaultValue: "Undo" });
  const lang2 = (i18n.language || "en").slice(0, 2);
  const menus: Menu[] = [
    { id: "file", label: t("sheet.mb.file"), items: [
      { label: t("sheet.mb.upload"), onClick: () => onNavigate?.("upload"), disabled: !onNavigate },
      { label: t("sheet.mb.linkBank"), onClick: () => onNavigate?.("upload"), disabled: !onNavigate },
      ...(bankConns.length ? [{ label: t("sheet.bank.update"), disabled: bankBusy, onClick: () => void syncAllBanks() }] : []),
      "sep",
      { label: t("sheet.mb.importRules"), onClick: () => withRules((c) => c.importRules()) },
      { label: t("sheet.mb.exportRules"), onClick: () => withRules((c) => c.exportRules()) },
      "sep",
      { label: t("sheet.mb.exportSheet"), onClick: () => downloadSheetCsv(visible), disabled: tab !== "sheet" || !visible.length },
      { label: t("sheet.mb.openPnl"), onClick: () => onNavigate?.("pnl"), disabled: !onNavigate },
    ] },
    { id: "edit", label: t("sheet.mb.edit"), items: [
      onRules
        ? { label: undoLabel(rc?.lastUndo ?? null), hint: "Ctrl+Z", disabled: !rc?.canUndo, onClick: () => rc?.undo() }
        : { label: undoLabel(lastUndo), hint: "Ctrl+Z", disabled: !canUndo || tab !== "sheet", onClick: () => void undo() },
      { label: t("sheet.menu.copy", { defaultValue: "Copy" }), hint: "Ctrl+C", disabled: tab !== "sheet" || !formula,
        onClick: () => void navigator.clipboard?.writeText(formula?.text ?? "") },
      "sep",
      onRules
        ? { label: t("rules.add", { defaultValue: "New rule" }), onClick: () => rc?.newRule() }
        : { label: selRow ? t("sheet.createRuleFromRow", { defaultValue: "Create rule from this row" })
                          : t("sheet.createRule", { defaultValue: "Create rule" }),
            onClick: () => { if (tab !== "sheet") setTab("sheet"); setRuleDialog({ seed: selRow }); } },
      { label: t("sheet.menu.followRule", { defaultValue: "Follow the rule again" }),
        disabled: tab !== "sheet" || selRow?.source !== "manual", onClick: () => selRow && void backToRule(selRow.id) },
      onRules
        ? { label: t("rules.deleteRule", { defaultValue: "Delete rule…" }), disabled: !rc?.canDelete, onClick: () => rc?.deleteRule() }
        : { label: t("sheet.menu.goToRule", { defaultValue: "Go to the rule" }),
            disabled: tab !== "sheet" || selRow?.source !== "rule" || selRow?.matched_rule_id == null,
            onClick: () => { if (selRow?.matched_rule_id != null) { setGoRule({ id: selRow.matched_rule_id, n: Date.now() }); setTab("rules"); } } },
      "sep",
      { label: t("sheet.mb.newBookNote"), onClick: () => void openBookNote(null) },
      "sep",
      { label: t("sheet.mb.find"), hint: "Ctrl+F", disabled: tab !== "sheet",
        onClick: () => setTimeout(() => { searchRef.current?.focus(); searchRef.current?.select(); }, 0) },
    ] },
    { id: "view", label: t("sheet.mb.view"), items: [
      { label: t("rules.formula.show", { defaultValue: "View as formula" }), checked: !!rc?.formulaOpen,
        onClick: () => withRules((c) => c.toggleFormula()) },
      { label: t("rules.showPriority"), checked: showPriority, onClick: () => setShowPriority(!showPriority) },
      "sep",
      { label: t("sheet.tabSheet", { defaultValue: "Bookkeeping" }), checked: tab === "sheet", onClick: () => setTab("sheet") },
      { label: t("sheet.tabRules", { defaultValue: "Rules" }), checked: tab === "rules", onClick: () => setTab("rules") },
      { label: t("sheet.tabPivot", { defaultValue: "Pivot" }), checked: tab === "pivot", onClick: () => setTab("pivot") },
      "sep",
      { label: t("sheet.onlyPending", { defaultValue: "Pending ({{n}})", n: pending }), checked: onlyPending,
        disabled: tab !== "sheet", onClick: () => setOnlyPending((v) => !v) },
      { label: t("sheet.onlyFlagged", { defaultValue: "Needs a look ({{n}})", n: flagged }), checked: onlyFlagged,
        disabled: tab !== "sheet", onClick: () => setOnlyFlagged((v) => !v) },
      "sep",
      { label: t("sheet.mb.toolbar"), checked: toolbar, onClick: () => setToolbar(!toolbar) },
      { label: t("sheet.mb.formulaBar"), checked: showFormula, onClick: () => setShowFormula(!showFormula) },
      { label: t("sheet.mb.pinDescription"), checked: pinDesc, disabled: tab !== "sheet", onClick: () => setPinDesc(!pinDesc) },
      { label: t("panel.showOnSelect"), checked: panelOnSelect, disabled: tab !== "sheet",
        onClick: () => { setPanelOnSelect(!panelOnSelect); setPanelClosedFor(null); } },
      { heading: t("sheet.mb.density") },
      ...(["compact", "normal", "roomy"] as const).map((d) => ({
        label: t(`sheet.density.${d}`, { defaultValue: d }), checked: density === d, onClick: () => setDensity(d) })),
      { heading: t("sheet.mb.textCase") },
      { label: t("sheet.case.asis", { defaultValue: "Text as written" }), checked: caseStyle === "asis", onClick: () => setCaseStyle("asis") },
      { label: t("sheet.case.upper", { defaultValue: "ALL CAPS" }), checked: caseStyle === "upper", onClick: () => setCaseStyle("upper") },
      { label: t("sheet.case.title", { defaultValue: "First Letter Capitalized" }), checked: caseStyle === "title", onClick: () => setCaseStyle("title") },
    ] },
    { id: "format", label: t("sheet.mb.format"), items: [
      { heading: nSel ? t("sheet.nSelected", { defaultValue: "{{count}} selected", count: nSel }) : t("sheet.mb.selectFirst") },
      { label: t("sheet.fmt.bold"), hint: "B", disabled: !nSel, onClick: () => doFormat({ bold: true }) },
      { label: t("sheet.fmt.italic"), hint: "I", disabled: !nSel, onClick: () => doFormat({ italic: true }) },
      "sep",
      ...COLORS.map(([c, label]) => ({ label, swatch: c, disabled: !nSel, onClick: () => doFormat({ bg: c }) })),
      "sep",
      { label: t("sheet.fmt.clear"), disabled: !nSel, onClick: () => doFormat({ clear: true }) },
    ] },
    { id: "options", label: t("sheet.mb.options"), items: [
      { label: t("sheet.mb.chart"), onClick: () => onNavigate?.("settings"), disabled: !onNavigate },
      { heading: t("sheet.mb.language") },
      { label: "English", checked: lang2 === "en", onClick: () => void i18n.changeLanguage("en") },
      { label: "Español", checked: lang2 === "es", onClick: () => void i18n.changeLanguage("es") },
      "sep",
      { label: t("sheet.mb.shortcuts"), onClick: () => setViewer({ title: t("sheet.mb.shortcuts"), text: t("sheet.mb.shortcutsText") }) },
    ] },
  ];

  if (error && !rows) return <p style={{ color: "#991b1b", padding: 16 }}>{error}</p>;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 104px)", minHeight: 420 }}>
      <style>{MANUAL_CSS}</style>
      {rowCss && <style>{rowCss}</style>}
      {/* LA LÍNEA DE ESTADO (13-oct, Firm: diseño de página compartido — `PageKit.tsx`): el "actualizado
          hace X" y "↻ Actualizar banco". 18-oct, ronda de diseño: fuera las cifras que llevaba al lado
          (transacciones, Pendientes, problemas) — el inicio ya las dice; todo el bloque se esconde si
          el libro no tiene banco vinculado. */}
      {bankConns.length > 0 && (
        <div style={{ marginBottom: 8 }}>
          <PageHeader
            meta={!oldestSync ? t("sheet.bank.never")
              : Date.now() - Date.parse(oldestSync + "Z") < 60_000 ? t("sheet.bank.justNow")
              : t("sheet.bank.updatedAgo", { when: timeAgo(oldestSync, lang2) })}
            primary={
              <button onClick={() => void syncAllBanks()} disabled={bankBusy} className={BTN_PRIMARY}>
                {bankBusy ? t("sheet.bank.updating") : t("sheet.bank.update")}
              </button>
            } />
        </div>
      )}
      <MenuBar menus={menus} ariaLabel={t("sheet.mb.label")} />
      {/* Un aviso con la hoja ya cargada (antes `error` solo se veía si la hoja no cargaba). */}
      {error && rows && (
        <div className="alert text-sm bg-amber-50 text-amber-800 border-amber-200 mb-2" style={{ display: "flex" }}>
          <span style={{ flex: 1 }}>{error}</span>
          <button onClick={() => setError(null)} aria-label={t("sheet.menu.close", { defaultValue: "Close" })}
            style={{ border: 0, background: "transparent", cursor: "pointer", color: "#8a958f", fontSize: 16 }}>×</button>
        </div>
      )}
      {bankNote && (
        <div style={{ position: "relative", marginBottom: 8 }}>
          {bankNote.sync && <SyncSummary s={bankNote.sync} />}
          {bankNote.disconnected > 0 && (
            <div className="alert text-sm bg-amber-50 text-amber-800 border-amber-200 mt-1">
              <span>
                {t("sheet.bank.disconnected", { count: bankNote.disconnected })}{" "}
                <button onClick={() => onNavigate?.("upload")} className="underline font-semibold">
                  {t("sheet.bank.goReconnect")}
                </button>
              </span>
            </div>
          )}
          {bankNote.failed > 0 && (
            <div className="alert alert-error text-sm mt-1">{t("sheet.bank.failed", { count: bankNote.failed })}</div>
          )}
          <button onClick={() => setBankNote(null)} aria-label={t("sheet.bank.close")} title={t("sheet.bank.close")}
            style={{ position: "absolute", top: 6, insetInlineEnd: 10, border: 0, background: "transparent",
                     cursor: "pointer", color: "#8a958f", fontSize: 16 }}>×</button>
        </div>
      )}
      {/* Barra de control: el filtro por cuenta que pidió Jose, arriba de la hoja. */}
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginBottom: 10 }}>
        {/* Hoja / Pivote: el pivote es la MISMA verdad, agrupada — por eso vive aquí al lado
            y comparte el filtro de cuenta, no en otra sección del menú. */}
        <div style={{ display: "flex", gap: 2, background: "#fff", border: "1px solid #d5d0c4",
                      borderRadius: 8, padding: 2 }}>
          {(["sheet", "rules", "pivot"] as const).map((k) => (  /* Reglas 2ª (Jose, 4-oct) */
            <button key={k} onClick={() => setTab(k)}
              style={{ border: 0, borderRadius: 6, cursor: "pointer", fontWeight: 600, fontSize: 13,
                       padding: "5px 13px",
                       background: tab === k ? "#1A8754" : "transparent",
                       color: tab === k ? "#fff" : "#5b6b62" }}>
              {k === "sheet" ? t("sheet.tabSheet", { defaultValue: "Bookkeeping" })
               : k === "pivot" ? t("sheet.tabPivot", { defaultValue: "Pivot" })
               : t("sheet.tabRules", { defaultValue: "Rules" })}
            </button>
          ))}
        </div>

        {/* CUENTAS con casillas: el botón dice cuáles están activas (todas, una, dos, o "N de M"). */}
        <div style={{ position: "relative", display: "flex", alignItems: "center" }}>
          <button type="button" onClick={() => setAcctOpen((o) => !o)} aria-expanded={acctOpen}
            title={acctSel.length ? acctSel.join(" · ") : undefined}
            style={{ ...input, cursor: "pointer", display: "flex", alignItems: "center", gap: 6,
                     borderColor: acctSel.length ? "#1A8754" : "#d5d0c4", color: acctSel.length ? "#1A8754" : undefined,
                     fontWeight: acctSel.length ? 600 : 400, paddingInlineEnd: acctSel.length ? 28 : undefined }}>
            {acctSel.length === 0 ? t("sheet.allAccounts", { defaultValue: "All accounts" })
              : acctSel.length <= 2 ? acctSel.join(" · ")
              : t("sheet.accountsSome", { defaultValue: "{{n}} of {{m}} accounts", n: acctSel.length, m: accounts.length })}
            {acctSel.length === 0 && <span style={{ fontSize: 10 }}>▾</span>}
          </button>
          {/* Filtro activo → se ve cuál y se quita con una × (19-oct, igual que el de fechas). */}
          {acctSel.length > 0 && (
            <button type="button" onClick={() => setAcctSel([])} aria-label={t("sheet.clearAccounts")} title={t("sheet.clearAccounts")}
              style={{ position: "absolute", insetInlineEnd: 6, border: 0, background: "transparent", cursor: "pointer",
                       color: "#1A8754", fontSize: 15, lineHeight: 1, padding: "0 3px" }}>×</button>
          )}
          {acctOpen && (
            <>
              <div onClick={() => setAcctOpen(false)} style={{ position: "fixed", inset: 0, zIndex: 40 }} />
              <div role="group" aria-label={t("sheet.accountsLabel", { defaultValue: "Accounts" })}
                style={{ position: "absolute", top: "100%", left: 0, marginTop: 4, zIndex: 41, minWidth: 220,
                         background: "#fff", border: "1px solid #d5d0c4", borderRadius: 8,
                         boxShadow: "0 8px 24px rgba(15,23,42,.12)", padding: 6 }}>
                <label style={{ display: "flex", gap: 8, alignItems: "center", padding: "5px 8px", fontSize: 13, cursor: "pointer", fontWeight: 600 }}>
                  <input type="checkbox" checked={acctSel.length === 0} onChange={() => setAcctSel([])} />
                  {t("sheet.allAccounts", { defaultValue: "All accounts" })}
                </label>
                <div style={{ borderTop: "1px solid #eee9de", margin: "4px 0" }} />
                {accounts.map((a) => (
                  <label key={a} style={{ display: "flex", gap: 8, alignItems: "center", padding: "5px 8px", fontSize: 13, cursor: "pointer" }}>
                    <input type="checkbox" checked={acctSel.includes(a)}
                      onChange={(e) => setAcctSel((cur) => {
                        const next = e.target.checked ? [...cur, a] : cur.filter((x) => x !== a);
                        return next.length === accounts.length ? [] : next;  // todas marcadas = "todas"
                      })} />
                    <span style={{ fontVariantNumeric: "tabular-nums" }}>{a}</span>
                  </label>
                ))}
                {accounts.length === 0 && (
                  <div style={{ padding: "5px 8px", fontSize: 12.5, color: "#8a958f" }}>
                    {t("sheet.noAccounts", { defaultValue: "No accounts in this book yet." })}
                  </div>
                )}
              </div>
            </>
          )}
        </div>

        {/* FECHAS (19-oct): atajos o un rango propio; se combina con todo lo demás y filtra el Pivote. */}
        <DateFilterButton value={dateFilter} onChange={setDateFilter} inputStyle={input} />

        {/* BUSCAR: Ctrl+F lo enfoca · Enter / Shift+Enter siguiente / anterior · Esc limpia. */}
        <div style={{ position: "relative", display: "flex", alignItems: "center" }}>
          <input ref={searchRef} value={search} onChange={(e) => setSearch(e.target.value)}
                 onKeyDown={(e) => {
                   if (e.key === "Escape") { e.preventDefault(); setSearch(""); return; }
                   if (e.key === "Enter") {
                     e.preventDefault();
                     const next = e.shiftKey ? hit - 1 : (search === lastSearchRef.current ? hit + 1 : 0);
                     lastSearchRef.current = search;
                     // Como el cuadro de buscar de Excel: la grilla va a la celda y el foco se queda aquí.
                     void goHit(next).then(() => setTimeout(() => searchRef.current?.focus(), 80));  // RevoGrid enfoca después
                   }
                 }}
                 title={t("sheet.searchHint", { defaultValue: "Ctrl+F to search · Enter: next · Shift+Enter: previous · Esc: clear" })}
                 style={{ ...input, minWidth: 230, paddingInlineEnd: search ? 70 : 10 }}
                 placeholder={t("sheet.search", { defaultValue: "Search…" })} />
          {search.trim() && (
            <span aria-live="polite" style={{ position: "absolute", insetInlineEnd: 10, fontSize: 11.5,
                         color: hits.length ? "#5b6b62" : "#991b1b", fontVariantNumeric: "tabular-nums", pointerEvents: "none" }}>
              {hits.length
                ? t("sheet.searchCount", { defaultValue: "{{n}} of {{m}}", n: Math.min(hit, hits.length - 1) + 1, m: hits.length })
                : t("sheet.searchNone", { defaultValue: "0 found" })}
            </span>
          )}
        </div>

        {/* CREAR REGLA — protagonista (Jose, 4-oct): siempre a la vista. Con UNA fila seleccionada
            parte de su descripción (el texto COMBINADO: si escribiste a quién fue el cheque, esa es
            la palabra clave que quieres, no "CHECK 1234"); sin selección, abre una regla en blanco. */}
        {toolbar && tab === "sheet" && (
          <button
            onClick={() => {
              const row = selected.length === 1 ? (rowsRef.current ?? []).find((r) => r.id === selected[0]) : null;
              setRuleDialog({ seed: row ?? null });
            }}
            title={selected.length === 1
              ? t("sheet.ruleFromRow", { defaultValue: "Create a rule from this description" })
              : t("sheet.ruleBlank", { defaultValue: "Create a new rule (select a row first to start from its description)" })}
            style={{ border: "1px solid #1A8754", background: "#1A8754", color: "#fff", borderRadius: 8,
                     padding: "6px 14px", fontWeight: 700, fontSize: 13, cursor: "pointer",
                     whiteSpace: "nowrap", display: "inline-flex", alignItems: "center", gap: 6,
                     boxShadow: "0 1px 2px rgba(26,135,84,.35)" }}>
            <span style={{ fontStyle: "italic", fontWeight: 800 }}>ƒ+</span>
            {selected.length === 1
              ? t("sheet.createRuleFromRow", { defaultValue: "Create rule from this row" })
              : t("sheet.createRule", { defaultValue: "Create rule" })}
          </button>
        )}

        {/* 📌 NOTA DEL LIBRO (19-oct): lo que el bookkeeper sabe del libro, sin salir de la Hoja. Con texto y
            junto a "Crear regla" (Jose: "el botón está muy escondido" cuando era solo el 📌). */}
        {toolbar && tab === "sheet" && (
          <button onClick={() => void openBookNote(null)} title={t("sheet.bookNoteHint")}
            style={{ border: "1px solid #d5d0c4", background: "#fff", borderRadius: 8, padding: "5px 12px",
                     fontWeight: 600, fontSize: 13, cursor: "pointer", whiteSpace: "nowrap" }}>
            📌 {t("sheet.bookNote")}
          </button>
        )}

        {toolbar && tab === "sheet" && (pending > 0 || onlyPending) && (
          <button onClick={() => setOnlyPending((v) => !v)}
            style={{ ...input, cursor: "pointer", fontWeight: 600,
                     border: "1px solid " + (onlyPending ? "#dc2626" : "#d5d0c4"),
                     background: onlyPending ? "rgba(220,38,38,.10)" : "#fff",
                     color: onlyPending ? "#991b1b" : "#5b6b62" }}>
            {t("sheet.onlyPending", { defaultValue: "Pending ({{n}})", n: pending })}
          </button>
        )}

        {toolbar && tab === "sheet" && (flagged > 0 || onlyFlagged) && (
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
        {toolbar && tab === "sheet" && selected.length > 0 && (
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

        {toolbar && tab === "sheet" && (
          <button onClick={() => void undo()} disabled={!canUndo}
            title={lastUndo ? t("sheet.undoWhat", { defaultValue: "Undo: {{what}}", what: lastUndo }) + " (Ctrl+Z)" : "Ctrl+Z"}
            style={{ border: "1px solid #d5d0c4", background: "#fff", borderRadius: 8, padding: "5px 12px",
                     fontWeight: 600, fontSize: 13, cursor: canUndo ? "pointer" : "default",
                     opacity: canUndo ? 1 : 0.45, whiteSpace: "nowrap" }}>
            ↶ {t("sheet.undo", { defaultValue: "Undo" })}
          </button>
        )}

        {/* Densidad: tamaño de letra y alto de fila. Un bookkeeper revisando 300 movimientos
            quiere ver el máximo posible; uno mostrándole los números al cliente, lo contrario. */}
        {toolbar && <div style={{ display: "flex", gap: 2, background: "#fff", border: "1px solid #d5d0c4",
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
        </div>}

        <span style={{ marginLeft: "auto", fontSize: 12.5, color: "#5b6b62" }}>
          {saving ?? t("sheet.count", { defaultValue: "{{count}} transactions", count: visible.length })}
        </span>
      </div>

      {/* La barra vive DENTRO de la columna flex: le quita alto a la grilla (flex: 1), no a la
          página — así `calc(100vh - 132px)` no cambia y la página sigue sin scroll. */}
      {showFormula && tab === "sheet" && rows !== null && (
        <FormulaBar cellRef={formula?.ref ?? null} text={formula?.text ?? null}
          emptyLabel={t("sheet.formulaEmpty", { defaultValue: "Select a cell to see its full content" })}
          ariaLabel={t("sheet.formulaLabel", { defaultValue: "Cell content" })} />
      )}

      {/* La grilla y, a su derecha, el PANEL de la transacción (13-oct). El panel le quita ancho a la
          grilla (no la tapa): la fila seleccionada siempre queda a la vista. */}
      <div style={{ flex: 1, minHeight: 0, display: "flex", gap: 8 }}>
      <div ref={hostRef} onContextMenu={(e) => void onContextMenu(e)}
        style={{ flex: 1, minHeight: 0, minWidth: 0,
                    border: tab === "sheet" ? "1px solid #e3ded2" : "0",
                    borderRadius: 10, overflow: "hidden" }}>
        {tab === "rules"
          ? <RulesSheet seedKeyword={ruleSeed} onSeedUsed={() => setRuleSeed(null)} memory={rulesSpot}
              toolbar={toolbar} formulaBar={showFormula} onCommands={onRulesCommands}
              focusRule={goRule} onFocusRuleDone={() => setGoRule(null)} showPriority={showPriority} />
          : tab === "pivot"
          ? <div style={{ height: "100%", overflowY: "auto" }}><Pivot accounts={acctSel} range={dateRange} /></div>
          : rows === null
          ? <p style={{ padding: 16, color: "#5b6b62" }}>{t("common.loading", { defaultValue: "Loading…" })}</p>
          : <RevoGrid
              ref={attachGrid as any}
              style={{ height: "100%", fontSize: `${DENSITY.font}px` }}
              theme="compact"
              columns={gridColumns as any}
              source={visible as any}
              resize
              range
              canFocus
              rowSize={DENSITY.row}
              rowHeaders={true}   /* números de fila: para poder decir "la fila 47" al reportar */
              rowClass="_rowClass"
              editors={SHEET_EDITORS as any}
            />}
      </div>
      {panelTx != null && (
        <TransactionPanel txId={panelTx} refreshKey={flagsKey}
          onClose={() => setPanelClosedFor(panelTx)}
          onGoRule={(id) => { setGoRule({ id, n: Date.now() }); setTab("rules"); }}
          onOpenAudit={() => onNavigate?.("audit")}
          // Recarga las FILAS (y con ellas los puntitos): la nota se escribe en el panel y su 📝 y la
          // búsqueda viven en la Hoja (19-oct).
          onChanged={load} />
      )}
      </div>
      {menu && <CellMenu at={menu.at} items={menuItems(menu.model, menu.prop, menu.row)} onClose={() => setMenu(null)} />}
      {ruleDialog && (
        <RuleDialog seed={ruleDialog.seed} chart={chart}
          onClose={() => setRuleDialog(null)}
          onChartChanged={(c) => setChart(c)}
          onOpenRules={(k) => { setRuleDialog(null); setRuleSeed(k); setTab("rules"); }}
          onCreated={({ id, keyword, applied }) => {
            setRuleDialog(null);
            pushUndo({ label: t("rules.undoCreate", { defaultValue: "create rule {{k}}", k: keyword }),
                       run: async () => { await deleteRule(id, true); load(); } });
            setSaving(t("rules.dialog.created", { k: keyword, count: applied }));
            setTimeout(() => setSaving(null), 4000);
            load();
          }} />
      )}
      {/* Al guardar, la Hoja sigue igual detrás (la ventana es un portal encima): mismo lugar, misma fila.
          Se recargan los puntitos y el panel, que muestran las notas del libro de cada fila. */}
      {quickNote && (
        <NoteQuickAdd merchant={quickNote.merchant} onClose={() => setQuickNote(null)} onSaved={() => loadFlags()}
          onOpenNotes={onNavigate ? () => onNavigate("notes") : undefined} />
      )}
      {viewer && <CellViewer title={viewer.title} text={viewer.text} onClose={() => setViewer(null)}
                    closeLabel={t("sheet.menu.close", { defaultValue: "Close" })} />}

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
