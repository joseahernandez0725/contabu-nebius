import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Editor, type EditorType } from "@revolist/react-datagrid";

/**
 * KIT DE LA HOJA (FASE 10, Movimientos · 4-oct-2026) — lo que comparten la Hoja y la hoja de Reglas
 * para trabajar como en Excel (pedido de Jose):
 *
 * - `TEXT_EDITOR`: editor de texto que ARRANCA con la tecla que pulsaste (sin Enter antes) y, si
 *   empezaste escribiendo, las FLECHAS confirman y mueven — como el "modo Introducir" de Excel. Si
 *   entraste con Enter, F2 o doble clic ("modo Modificar"), las flechas mueven el cursor del texto.
 *   Si la columna trae `suggestion(model)`, la sugerencia se ve en GRIS después de lo escrito
 *   (autocompletar, como Excel): Tab o Enter la aceptan, → la pone sin salir y Supr la descarta.
 * - `useUndo`: la pila de DESHACER (botón y Ctrl+Z). Cada edición guardada deja cómo revertirse.
 * - `CellMenu` + `cellFromEvent`: el menú de CLIC DERECHO sobre una celda.
 * - `useCaseStyle` / `applyCase`: cómo se VEN categoría y subcategoría (como está · MAYÚSCULAS ·
 *   Primera Letra). Es solo presentación: el dato no cambia (la categoría tiene que coincidir
 *   letra por letra con el plan de cuentas).
 */

// ── Mover el foco de la grilla ────────────────────────────────────────────────────────────────

export function gridOf(el: Element | null): any {
  return el?.closest("revo-grid") ?? null;
}

/** Mueve el foco a (x, y) dentro de los límites de la grilla. Espera a que la grilla termine de
 *  cerrar el editor: al guardar, RevoGrid vuelve a enfocar la celda editada y pisaría el movimiento. */
export async function moveFocus(grid: any, x: number, y: number) {
  if (!grid) return;
  await new Promise((r) => setTimeout(r, 40));
  const cols = (await grid.getColumns?.()) ?? [];
  const rows = (await grid.getVisibleSource?.()) ?? [];
  const cx = Math.max(0, Math.min(x, cols.length - 1));
  const cy = Math.max(0, Math.min(y, rows.length - 1));
  await grid.setCellsFocus({ x: cx, y: cy }, { x: cx, y: cy });
}

const ARROWS: Record<string, [number, number]> = {
  ArrowUp: [0, -1], ArrowDown: [0, 1], ArrowLeft: [-1, 0], ArrowRight: [1, 0],
};
export function arrowDelta(key: string): [number, number] | null {
  return ARROWS[key] ?? null;
}

/** ¿El editor se abrió ESCRIBIENDO (RevoGrid pasa la tecla en `val`) o con Enter/F2/doble clic? */
export function startedByTyping(val: unknown, current: unknown): boolean {
  return typeof val === "string" && val !== String(current ?? "");
}

// ── Autocompletar en gris ─────────────────────────────────────────────────────────────────────

/** Lo que FALTA de la sugerencia para lo escrito ("PAY" → "ROLL SERVICE"), o "" si no aplica. */
export function ghostRest(text: string, suggestion: string | null | undefined): string {
  const s = suggestion ?? "";
  if (!s || text.length >= s.length) return "";
  return s.toLowerCase().startsWith(text.toLowerCase()) ? s.slice(text.length) : "";
}

/** Campo de texto con AUTOCOMPLETAR (Jose, 4-oct): la sugerencia aparece en gris después de lo que
 *  escribes y se va acortando mientras coincida. → al final la pone sin salir; Supr la descarta.
 *  Lo demás (Tab, Enter) lo decide quien lo usa: `onKeyDown` recibe la sugerencia COMPLETA si está
 *  a la vista (para aceptarla) o null. */
export function GhostInput({ value, onChange, suggestion, onKeyDown, inputRef, style, placeholder, autoFocus,
                            onFocus, onBlur }: {
  value: string;
  onFocus?: () => void;
  onBlur?: () => void;
  onChange: (v: string) => void;
  suggestion?: string | null;
  onKeyDown?: (e: React.KeyboardEvent<HTMLInputElement>, accept: string | null) => void;
  inputRef?: React.Ref<HTMLInputElement>;
  style?: React.CSSProperties;
  placeholder?: string;
  autoFocus?: boolean;
}) {
  const [dismissed, setDismissed] = useState(false);
  const rest = dismissed ? "" : ghostRest(value, suggestion);
  const key = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.nativeEvent.isComposing) return;
    const el = e.currentTarget;
    const atEnd = el.selectionStart === el.value.length && el.selectionEnd === el.value.length;
    if (rest && atEnd && e.key === "ArrowRight") {
      e.preventDefault(); e.stopPropagation();
      onChange(suggestion ?? "");
      return;
    }
    if (rest && atEnd && e.key === "Delete") {
      e.preventDefault(); e.stopPropagation();
      setDismissed(true);
      return;
    }
    onKeyDown?.(e, rest ? (suggestion ?? null) : null);
  };
  const ghost: React.CSSProperties = {
    ...style, position: "absolute", inset: 0, pointerEvents: "none", whiteSpace: "pre", overflow: "hidden",
    display: "flex", alignItems: "center", color: "transparent", background: "transparent",
    borderColor: "transparent", outline: "none", boxSizing: "border-box",
  };
  return (
    <div style={{ position: "relative", width: "100%", height: style?.height ?? undefined }}>
      <input ref={inputRef} value={value} autoFocus={autoFocus}
        placeholder={rest || value ? undefined : placeholder}
        onChange={(e) => { setDismissed(false); onChange(e.target.value); }}
        onKeyDown={key} onFocus={onFocus} onBlur={onBlur}
        style={{ ...style, boxSizing: "border-box", width: "100%" }} />
      {rest && (
        <div aria-hidden style={ghost}>
          <span style={{ visibility: "hidden" }}>{value}</span>
          <span style={{ color: "#a8a191" }}>{rest}</span>
        </div>
      )}
    </div>
  );
}

// ── Editor de texto estilo Excel ──────────────────────────────────────────────────────────────

type TextEditorProps = EditorType & { value?: unknown };

function TextCellEditor(props: TextEditorProps) {
  const { save, close, val, column, model, prop, x, y } = props as TextEditorProps & {
    model?: any; prop?: string; x?: number; y?: number;
  };
  const current = model && prop ? model[prop] : props.value;
  const typed = startedByTyping(val, current);
  const suggestion = String((column as any)?.suggestion?.(model) ?? "");
  const initial = typed ? String(val) : String(current ?? "");
  const [text, setText] = useState(initial);
  const ref = useRef<HTMLInputElement>(null);
  const textRef = useRef(text);
  textRef.current = text;
  // Las teclas que llegan ANTES de que este campo tenga el foco, RevoGrid las va sumando a `val`
  // (si se ignoran, "TEST" queda "TES"). Mientras no se haya escrito en el campo, se sigue a `val`.
  const touched = useRef(false);
  useEffect(() => {
    if (typed && !touched.current && typeof val === "string") setText(val);
  }, [val, typed]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const take = () => {
      el.focus({ preventScroll: true });
      // Escribiendo: el cursor al final. Entrando a modificar: todo seleccionado, como en Excel.
      if (typed) el.setSelectionRange(el.value.length, el.value.length); else el.select();
    };
    take();
    const id = requestAnimationFrame(take);
    return () => cancelAnimationFrame(id);
  }, [typed]);

  // `accept` = la sugerencia completa si está a la vista en gris: Enter y Tab la aceptan (Excel).
  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>, accept: string | null) => {
    const value = accept ?? textRef.current;
    if (e.key === "Enter") {
      e.preventDefault(); e.stopPropagation();
      save(value);                              // guarda y baja (lo hace la grilla)
    } else if (e.key === "Escape") {
      e.preventDefault(); e.stopPropagation();
      close();
    } else if (e.key === "Tab") {
      e.preventDefault();
      save(value, true);                        // la grilla mueve a la derecha con su Tab
    } else if (typed) {
      const d = arrowDelta(e.key);
      if (d && x != null && y != null) {        // modo Introducir: la flecha confirma y mueve
        e.preventDefault(); e.stopPropagation();
        const grid = gridOf(ref.current);
        save(value, true);
        void moveFocus(grid, x + d[0], y + d[1]);
      }
    }
  };

  return (
    <GhostInput
      inputRef={ref}
      value={text}
      suggestion={suggestion}
      onChange={(v) => { touched.current = true; setText(v); }}
      onKeyDown={onKeyDown}
      style={{ width: "100%", height: "100%", border: 0, outline: "2px solid #1A8754",
               padding: "0 8px", font: "inherit", background: "#fff" }}
    />
  );
}

export const TEXT_EDITOR = "cbText";
export const textEditors = { [TEXT_EDITOR]: Editor(TextCellEditor) };

// ── Deshacer ──────────────────────────────────────────────────────────────────────────────────

export type UndoEntry = { label: string; run: () => Promise<void> };

/** Pila de DESHACER de una grilla. `push` al guardar; `undo` revierte la última (botón y Ctrl+Z).
 *  Ctrl+Z dentro de un texto que se está escribiendo sigue siendo el deshacer del navegador. */
export function useUndo(onError: (msg: string) => void, max = 50) {
  const [stack, setStack] = useState<UndoEntry[]>([]);
  const [busy, setBusy] = useState(false);
  const stackRef = useRef(stack);
  stackRef.current = stack;

  const push = useCallback((entry: UndoEntry) => {
    setStack((s) => [...s.slice(-(max - 1)), entry]);
  }, [max]);

  const undo = useCallback(async () => {
    const entry = stackRef.current[stackRef.current.length - 1];
    if (!entry || busy) return;
    setBusy(true);
    setStack((s) => s.slice(0, -1));
    try { await entry.run(); }
    catch (e) { onError(String((e as Error)?.message ?? e)); }
    finally { setBusy(false); }
  }, [busy, onError]);

  return { push, undo, canUndo: stack.length > 0 && !busy, last: stack[stack.length - 1]?.label ?? null };
}

/** Ctrl+Z / ⌘+Z sobre la grilla (no dentro de un campo de texto). */
export function useUndoShortcut(hostRef: React.RefObject<HTMLElement | null>, undo: () => void) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.shiftKey || e.key.toLowerCase() !== "z") return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      if (!hostRef.current?.contains(document.activeElement) && !hostRef.current?.matches(":hover")) return;
      e.preventDefault();
      undo();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [hostRef, undo]);
}

// ── Mayúsculas (solo presentación) ────────────────────────────────────────────────────────────

export type CaseStyle = "asis" | "upper" | "title";
const CASE_KEY = "contabu.caseStyle";
const CASE_EVENT = "contabu:casestyle";

function readCase(): CaseStyle {
  try {
    const v = localStorage.getItem(CASE_KEY);
    return v === "upper" || v === "title" ? v : "asis";
  } catch { return "asis"; }
}

/** Preferencia del bookkeeper (se recuerda en este navegador y se sincroniza entre las grillas). */
export function useCaseStyle(): [CaseStyle, (c: CaseStyle) => void] {
  const [style, setStyle] = useState<CaseStyle>(readCase);
  useEffect(() => {
    const on = () => setStyle(readCase());
    window.addEventListener(CASE_EVENT, on);
    return () => window.removeEventListener(CASE_EVENT, on);
  }, []);
  const set = useCallback((c: CaseStyle) => {
    try { localStorage.setItem(CASE_KEY, c); } catch { /* sin almacenamiento: solo esta sesión */ }
    setStyle(c);
    window.dispatchEvent(new Event(CASE_EVENT));
  }, []);
  return [style, set];
}

export function applyCase(text: string | null | undefined, style: CaseStyle): string {
  const s = text ?? "";
  if (style === "upper") return s.toUpperCase();
  if (style === "title") return s.toLowerCase().replace(/(^|[\s/&(-])(\p{L})/gu, (_, a, b) => a + b.toUpperCase());
  return s;
}

// ── Menú de clic derecho ──────────────────────────────────────────────────────────────────────

export type MenuItem =
  | { label: string; onClick: () => void; disabled?: boolean; checked?: boolean; hint?: string; swatch?: string }
  | { heading: string }
  | "sep";

/** La celda bajo el clic derecho: la enfoca y devuelve su fila (modelo) y su columna (prop). */
export async function cellFromEvent(e: MouseEvent): Promise<{ model: any; prop: string; x: number; y: number } | null> {
  const cell = (e.target as HTMLElement | null)?.closest?.("[data-rgcol][data-rgrow]") as HTMLElement | null;
  const grid = gridOf(cell);
  if (!cell || !grid) return null;
  const x = Number(cell.getAttribute("data-rgcol")), y = Number(cell.getAttribute("data-rgrow"));
  if (Number.isNaN(x) || Number.isNaN(y)) return null;
  await grid.setCellsFocus({ x, y }, { x, y });
  const f = await grid.getFocused?.();
  const prop = f?.column?.prop;
  if (!f?.model || prop == null) return null;
  return { model: f.model, prop: String(prop), x, y };
}

export function CellMenu({ at, items, onClose, keepRef }: {
  at: { x: number; y: number }; items: MenuItem[]; onClose: () => void;
  keepRef?: React.RefObject<HTMLElement | null>;  // clics aquí no cierran (la barra de menús)
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const away = (e: MouseEvent) => {
      if (ref.current?.contains(e.target as Node) || keepRef?.current?.contains(e.target as Node)) return;
      onClose();
    };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("mousedown", away, true);
    document.addEventListener("keydown", esc, true);
    window.addEventListener("scroll", onClose, true);
    return () => {
      document.removeEventListener("mousedown", away, true);
      document.removeEventListener("keydown", esc, true);
      window.removeEventListener("scroll", onClose, true);
    };
  }, [onClose, keepRef]);
  const W = 250;
  const left = Math.max(8, Math.min(at.x, window.innerWidth - W - 8));
  const top = Math.max(8, Math.min(at.y, window.innerHeight - 40 - items.length * 30));
  return createPortal(
    // preventDefault en mouseup: si no, RevoGrid toma el clic como "afuera" y suelta el foco de la grilla.
    <div ref={ref} role="menu" onMouseUp={(e) => e.preventDefault()} style={{
      position: "fixed", zIndex: 3000, top, left, width: W, background: "#fff",
      border: "1px solid #d5d0c4", borderRadius: 8, boxShadow: "0 10px 30px rgba(15,23,42,.18)",
      padding: 4, fontSize: 13, maxHeight: `calc(100vh - ${top + 8}px)`, overflowY: "auto",
    }}>
      {items.map((it, i) => it === "sep"
        ? <div key={i} style={{ height: 1, background: "#efeae0", margin: "4px 2px" }} />
        : "heading" in it
        ? <div key={i} style={{ padding: "5px 10px 2px", fontSize: 10.5, fontWeight: 700, letterSpacing: ".06em",
                                textTransform: "uppercase", color: "#9a9384" }}>{it.heading}</div>
        : (
          <button key={i} role="menuitem" disabled={it.disabled}
            onClick={() => { onClose(); it.onClick(); }}
            style={{
              display: "flex", width: "100%", alignItems: "center", gap: 8, border: 0,
              background: "transparent", textAlign: "start", padding: "6px 10px", borderRadius: 6,
              cursor: it.disabled ? "default" : "pointer", color: it.disabled ? "#b9b2a2" : "#1f2a24",
            }}
            onMouseEnter={(e) => { if (!it.disabled) e.currentTarget.style.background = "rgba(26,135,84,.08)"; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}>
            <span style={{ width: 14, color: "#1A8754", fontWeight: 700 }}>{it.checked ? "✓" : ""}</span>
            {it.swatch && <span style={{ width: 14, height: 14, borderRadius: 3, background: it.swatch, flex: "none",
                                         border: "1px solid rgba(15,23,42,.28)" }} />}
            <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{it.label}</span>
            {it.hint && <span style={{ color: "#9a9384", fontSize: 11.5 }}>{it.hint}</span>}
          </button>
        ))}
    </div>,
    document.body,
  );
}

// ── Barra de menús (Archivo · Edición · Ver · Formato · Opciones) ─────────────────────────────

export type Menu = { id: string; label: string; items: MenuItem[] };

/** La barra de menús de arriba, como en Excel o Google Sheets (Jose, 4-oct). Clic abre un menú; con
 *  uno abierto, pasar el mouse por los otros los abre. Los menús los arma quien la usa. */
export function MenuBar({ menus, ariaLabel, right }: { menus: Menu[]; ariaLabel: string; right?: React.ReactNode }) {
  const [open, setOpen] = useState<string | null>(null);
  const [at, setAt] = useState<{ x: number; y: number }>({ x: 0, y: 0 });
  const barRef = useRef<HTMLDivElement>(null);
  const show = (id: string, el: HTMLElement) => {
    const r = el.getBoundingClientRect();
    setAt({ x: r.left, y: r.bottom + 2 });
    setOpen(id);
  };
  const current = menus.find((m) => m.id === open);
  return (
    <div ref={barRef} role="menubar" aria-label={ariaLabel}
      style={{ display: "flex", alignItems: "center", gap: 2, marginBottom: 4, minHeight: 26 }}>
      {menus.map((m) => (
        <button key={m.id} role="menuitem" aria-haspopup="menu" aria-expanded={open === m.id}
          onClick={(e) => (open === m.id ? setOpen(null) : show(m.id, e.currentTarget))}
          onMouseEnter={(e) => { if (open && open !== m.id) show(m.id, e.currentTarget); }}
          style={{ border: 0, borderRadius: 6, padding: "3px 10px", fontSize: 13, fontWeight: 600,
                   cursor: "pointer", color: "#334139",
                   background: open === m.id ? "rgba(26,135,84,.12)" : "transparent" }}>
          {m.label}
        </button>
      ))}
      {right && <span style={{ marginInlineStart: "auto", display: "flex", alignItems: "center", gap: 8 }}>{right}</span>}
      {current && <CellMenu at={at} items={current.items} onClose={() => setOpen(null)} keepRef={barRef} />}
    </div>
  );
}

/** Ventanita para VER el contenido completo de una celda (se puede seleccionar y copiar). */
export function CellViewer({ title, text, onClose, closeLabel }: {
  title: string; text: string; onClose: () => void; closeLabel: string;
}) {
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", esc, true);
    return () => document.removeEventListener("keydown", esc, true);
  }, [onClose]);
  return createPortal(
    <div onMouseDown={onClose} style={{ position: "fixed", inset: 0, zIndex: 3000, background: "rgba(15,23,42,.25)",
      display: "flex", alignItems: "center", justifyContent: "center", padding: 16 }}>
      <div onMouseDown={(e) => e.stopPropagation()} role="dialog" aria-label={title}
        style={{ background: "#fff", borderRadius: 10, padding: 16, width: "min(560px, 100%)",
                 boxShadow: "0 20px 50px rgba(15,23,42,.25)" }}>
        <div style={{ fontWeight: 700, fontSize: 13, color: "#5b6b62", marginBottom: 8 }}>{title}</div>
        <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 14, color: "#0f172a",
                      userSelect: "text", maxHeight: "50vh", overflowY: "auto" }}>{text || "—"}</div>
        <div style={{ textAlign: "end", marginTop: 12 }}>
          <button onClick={onClose} style={{ border: "1px solid #d5d0c4", background: "#fff", borderRadius: 8,
            padding: "5px 14px", fontWeight: 600, cursor: "pointer" }}>{closeLabel}</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

// ── Barra de fórmulas ─────────────────────────────────────────────────────────────────────────

/** La barra de arriba, como en Excel: referencia (columna · fila) y el contenido COMPLETO de la celda
 *  enfocada. Un texto más largo que la barra se recorre de lado (sin "…", que era justo el problema).
 *  Con `onCommit` (celda editable) la barra SE EDITA, como en Excel: Enter guarda, Escape descarta y
 *  al salir del campo guarda si cambió. `hint` = texto gris (p. ej. una sugerencia). */
export function FormulaBar({ cellRef, text, hint, suggestion, emptyLabel, ariaLabel, onCommit }: {
  cellRef: string | null; text: string | null; hint?: string | null; emptyLabel: string; ariaLabel: string;
  suggestion?: string | null;   // autocompletar en gris (Tab / Enter la aceptan), como en la celda
  onCommit?: (value: string) => void;
}) {
  const has = cellRef != null;
  const [draft, setDraftState] = useState(text ?? "");
  const draftRef = useRef(draft);
  const setDraft = (v: string) => { draftRef.current = v; setDraftState(v); };
  const editing = useRef(false);
  // Al cambiar de celda (o al recargar su valor) la barra muestra lo nuevo.
  useEffect(() => { if (!editing.current) setDraft(text ?? ""); }, [text, cellRef]);
  const commit = () => {
    editing.current = false;
    if (onCommit && draftRef.current !== (text ?? "")) onCommit(draftRef.current);
  };
  const box: React.CSSProperties = {
    display: "flex", alignItems: "center", gap: 8, height: 30, marginBottom: 6, background: "#fff",
    border: "1px solid " + (has && onCommit ? "#c9d9cf" : "#e3ded2"), borderRadius: 8,
    padding: "0 10px", fontSize: 13, minWidth: 0, flex: "none",
  };
  return (
    <div role="status" aria-label={ariaLabel} style={box}>
      <span style={{ flex: "none", fontWeight: 700, color: "#5b6b62", fontSize: 12,
                     paddingInlineEnd: 8, borderInlineEnd: "1px solid #e3ded2",
                     minWidth: 110, whiteSpace: "nowrap" }}>
        {cellRef ?? "—"}
      </span>
      {has && onCommit ? (
        <div style={{ flex: 1, minWidth: 0 }}>
          <GhostInput value={draft} placeholder={hint ?? ""} suggestion={suggestion}
            onFocus={() => { editing.current = true; }}
            onBlur={commit}
            onChange={(v) => { editing.current = true; setDraft(v); }}
            onKeyDown={(e, accept) => {
              const el = e.target as HTMLInputElement;
              if (e.key === "Tab" && accept) { e.preventDefault(); setDraft(accept); }
              else if (e.key === "Enter") {
                e.preventDefault();
                if (accept) setDraft(accept);  // Enter acepta la sugerencia en gris y guarda
                el.blur();
              } else if (e.key === "Escape") { e.preventDefault(); editing.current = false; setDraft(text ?? "");
                                               el.blur(); }
            }}
            style={{ border: 0, outline: "none", font: "inherit", color: "#0f172a",
                     background: "transparent", padding: 0, height: 26 }} />
        </div>
      ) : (
        <span title={text || hint || undefined}
          style={{ flex: 1, minWidth: 0, overflowX: "auto", overflowY: "hidden", whiteSpace: "nowrap",
                   color: has && text ? "#0f172a" : "#b9b2a2",
                   fontStyle: has && text ? "normal" : "italic" }}>
          {!has ? emptyLabel : (text || hint || "\u00a0")}
        </span>
      )}
    </div>
  );
}
