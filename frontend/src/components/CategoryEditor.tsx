import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Trans, useTranslation } from "react-i18next";
import { arrowDelta, gridOf, moveFocus, startedByTyping } from "./gridKit";
import { Editor, type EditorType } from "@revolist/react-datagrid";
import { addCustomCategory, type ChartOfAccounts } from "../api";

/**
 * EDITOR DE CATEGORÍA (FASE 10) — cierra el hueco del plan de cuentas.
 *
 * `categories.py` mantiene un plan CERRADO por sector para que la IA no invente cuentas, pero ese
 * candado solo existía del lado de la IA: la celda era texto libre. Como el P&L agrupa por el TEXTO
 * de la categoría (`reports.py`), un "Materiales" en vez de "Materials" parte el reporte en dos
 * renglones y hace que el auditor reporte inconsistencias que no existen.
 *
 * Aquí el bookkeeper ELIGE de su plan efectivo (sector + las personalizadas de la empresa),
 * agrupado por sección del P&L para que se vea a qué pertenece cada cuenta. Se puede escribir para
 * filtrar, pero no se puede inventar.
 */

// El chart vive fuera del componente porque RevoGrid construye el editor por su cuenta
// (no es un hijo de React): no hay forma de pasarle props desde el árbol.
let CHART: ChartOfAccounts = {};
export function setEditorChart(chart: ChartOfAccounts) { CHART = chart; }

const SECTION_LABEL: Record<string, string> = {
  REVENUE: "Revenue",
  COGS: "COGS · direct costs",
  OPERATING_EXPENSE: "Operating expenses",
  OTHER_INCOME: "Other income",
  OTHER_EXPENSE: "Other expenses",
  NON_PL: "Not in the P&L",
};
const SECTION_ORDER = ["REVENUE", "COGS", "OPERATING_EXPENSE", "OTHER_INCOME", "OTHER_EXPENSE", "NON_PL"];

// SECCIONES GUIADAS (Jose, 4-oct): "Account" terminó en Ingresos con un pago de nómina porque las 6
// secciones salían siempre en el mismo orden y sin decir qué va en cada una. Ahora se ordenan según
// el dinero ENTRE o SALGA, y las que casi nunca corresponden van al final, marcadas.
export type MoneyDirection = "in" | "out" | null;
const LIKELY: Record<"in" | "out", string[]> = {
  out: ["COGS", "OPERATING_EXPENSE", "OTHER_EXPENSE", "NON_PL"],
  in: ["REVENUE", "OTHER_INCOME", "NON_PL"],
};
/** Las secciones en el orden en que conviene ofrecerlas, y cuáles son poco comunes para ese dinero. */
export function sectionsFor(direction: MoneyDirection): { sec: string; unlikely: boolean }[] {
  if (!direction) return SECTION_ORDER.map((sec) => ({ sec, unlikely: false }));
  const likely = LIKELY[direction];
  return [...likely.map((sec) => ({ sec, unlikely: false })),
          ...SECTION_ORDER.filter((x) => !likely.includes(x)).map((sec) => ({ sec, unlikely: true }))];
}
/** La dirección del dinero de una fila de la Hoja (`is_inflow`) o de una regla (`direction`). */
export function directionOf(model: any): MoneyDirection {
  if (model?.is_inflow === true) return "in";
  if (model?.is_inflow === false) return "out";
  if (model?.direction === "in" || model?.direction === "out") return model.direction;
  return null;
}

function CategoryPicker(props: EditorType & { value?: unknown }) {
  const { save, close, val, x, y } = props;
  const model = (props as any).model, prop = (props as any).prop;
  const value = model && prop ? model[prop] : props.value;
  const direction = directionOf(model);
  // ESCRIBIR SIN ENTER (Jose, 4-oct): si el editor se abrió con una tecla, esa tecla ya es el
  // comienzo del filtro (RevoGrid la pasa en `val`). Antes se perdía y había que volver a escribirla.
  const typed = startedByTyping(val, value);
  const { t } = useTranslation();
  // Nombre de la sección en el idioma de la UI (el dato sigue en inglés). COGS lleva su aclaración.
  const sectionName = (sec: string) => sec === "COGS"
    ? t("sheet.sectionCogsDirect", { defaultValue: SECTION_LABEL.COGS })
    : t(`sheet.section.${sec}`, { defaultValue: SECTION_LABEL[sec] ?? sec });
  const [q, setQ] = useState(typed ? String(val) : "");
  // Las teclas que llegan antes de que el panel tome el foco, RevoGrid las suma a `val`: seguirlas.
  const qTouched = useRef(false);
  useEffect(() => { if (typed && !qTouched.current && typeof val === "string") setQ(val); }, [val, typed]);
  const [busy, setBusy] = useState(false);
  const [i, setI] = useState(0);
  const boxRef = useRef<HTMLDivElement>(null);
  const hostRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number; up: boolean } | null>(null);
  const current = String(value ?? "");

  // La lista se dibuja por PORTAL en <body> con posición FIJA: dentro de la celda quedaba
  // recortada por el contenedor de la grilla. Se mide el hueco del editor (`revogr-edit`) y,
  // si abajo no cabe, se abre hacia arriba.
  useEffect(() => {
    const host = hostRef.current?.closest("revogr-edit") ?? hostRef.current;
    const r = host?.getBoundingClientRect();
    if (!r) return;
    const H = 320;
    const up = r.bottom + H > window.innerHeight && r.top > H;
    setPos({ top: up ? r.top - H - 2 : r.bottom + 2, left: Math.min(r.left, window.innerWidth - 290), up });
  }, []);

  // El FOCO se toma explícitamente, no con `autoFocus`. Razón (bug 27-sep): `autoFocus` solo
  // dispara al montar, pero el panel vive en un PORTAL y se reposiciona en un segundo render;
  // además RevoGrid vuelve a enfocar su propia celda después de abrir el editor. Resultado: el
  // desplegable abría pero lo que escribías no llegaba a ningún lado.
  // Se reintenta en el siguiente frame para ganarle a ese robo de foco.
  useEffect(() => {
    if (!pos) return;
    const take = () => inputRef.current?.focus({ preventScroll: true });
    take();
    const id = requestAnimationFrame(take);
    const t = setTimeout(take, 60);
    return () => { cancelAnimationFrame(id); clearTimeout(t); };
  }, [pos]);

  // A PRUEBA DE ROBO DE FOCO. Pelear por el foco contra RevoGrid no alcanzó (Jose lo reportó dos
  // veces: el desplegable abre, dice "type to filter" y no se puede escribir). En vez de insistir,
  // mientras el panel está abierto se escucha el teclado en el DOCUMENTO: da igual quién tenga el
  // foco, lo que teclees entra al filtro. Solo caracteres imprimibles y borrar; las flechas, Enter
  // y Escape siguen su camino normal.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      // SIEMPRE se intercepta, incluso si el input ya tiene el foco. Dejar pasar la tecla "porque
      // el input la maneja" era el bug: el evento seguía subiendo hasta RevoGrid, que lo tomaba
      // como una tecla sobre la grilla y CERRABA el editor. Ahora la grilla nunca la ve.
      if (e.key === "Backspace") {
        e.preventDefault(); e.stopPropagation();
        qTouched.current = true; setQ((v) => v.slice(0, -1));
      } else if (e.key.length === 1) {
        e.preventDefault(); e.stopPropagation();
        qTouched.current = true; setQ((v) => v + e.key);
      } else {
        return;  // flechas, Enter y Escape siguen su curso (los maneja `onKey` del input)
      }
      inputRef.current?.focus({ preventScroll: true });
    };
    document.addEventListener("keydown", onKey, true);  // captura: antes que RevoGrid
    return () => document.removeEventListener("keydown", onKey, true);
  }, []);

  // Lista plana [sección, categoría] en el orden del estado de resultados.
  const options = useMemo(() => {
    const out: { section: string; name: string }[] = [];
    for (const { sec } of sectionsFor(direction)) {
      for (const name of CHART[sec] ?? []) out.push({ section: sec, name });
    }
    for (const [sec, cats] of Object.entries(CHART)) {
      if (!SECTION_ORDER.includes(sec)) for (const name of cats) out.push({ section: sec, name });
    }
    return out;
  }, []);

  const shown = useMemo(() => {
    const s = q.trim().toLowerCase();
    return s ? options.filter((o) => o.name.toLowerCase().includes(s)) : options;
  }, [options, q]);

  useEffect(() => { setI(0); }, [q]);
  useEffect(() => {
    boxRef.current?.querySelector<HTMLElement>('[data-active="true"]')
      ?.scrollIntoView({ block: "nearest" });
  }, [i]);

  const pick = (name: string) => save(name);

  // AGREGAR UNA CATEGORÍA (Jose, 4-oct): antes solo se ofrecía si lo escrito NO coincidía con
  // ninguna cuenta; con un nombre parecido a otro ("Materials" vs "Materials Testing") no había
  // cómo. Ahora hay un "+ Nueva categoría" fijo al pie de la lista.
  const [creating, setCreating] = useState(false);
  const name = q.trim();
  const exists = !!name && options.some((o) => o.name.toLowerCase() === name.toLowerCase());
  const createIn = async (sec: string) => {
    if (!name || exists) return;
    setBusy(true);
    try {
      await addCustomCategory(sec, name);
      CHART = { ...CHART, [sec]: [...(CHART[sec] ?? []), name] };  // disponible al instante
      save(name);
    } catch {
      setBusy(false);
    }
  };
  const sectionButtons = () => sectionsFor(direction).map(({ sec, unlikely }) => (
    <button
      key={sec}
      disabled={busy}
      onMouseDown={(e) => { e.preventDefault(); void createIn(sec); }}
      title={t(`sheet.sectionHelp.${sec}`)}
      style={{
        display: "block", width: "100%", textAlign: "start", border: 0,
        background: "transparent", cursor: busy ? "wait" : "pointer",
        padding: "4px 6px", fontSize: 13, color: unlikely ? "#9a9384" : "#1A8754", fontWeight: 600,
      }}
    >
      + {sectionName(sec)}
      {unlikely && <span style={{ fontWeight: 400, fontSize: 11 }}> · {t(direction === "out" ? "sheet.unlikelyOut" : "sheet.unlikelyIn")}</span>}
      <span style={{ display: "block", fontWeight: 400, fontSize: 11, color: "#7a8a80", lineHeight: 1.3 }}>
        {t(`sheet.sectionHelp.${sec}`)}
      </span>
    </button>
  ));
  /** Elige y mueve el foco (Tab y, si se abrió escribiendo, ← / →), como al confirmar en Excel. */
  const pickAndMove = (name: string, dx: number, dy: number) => {
    const grid = gridOf(hostRef.current);
    save(name, true);
    if (x != null && y != null) void moveFocus(grid, x + dx, y + dy);
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setI((n) => Math.min(n + 1, shown.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setI((n) => Math.max(n - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); if (shown[i]) pick(shown[i].name); }
    else if (e.key === "Escape") { e.preventDefault(); close(); }
    else if (e.key === "Tab") { e.preventDefault(); e.stopPropagation(); if (shown[i]) pickAndMove(shown[i].name, e.shiftKey ? -1 : 1, 0); }
    else if (typed && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
      // ↑/↓ recorren la lista; ← / → confirman la opción resaltada y se mueven (modo Introducir).
      const d = arrowDelta(e.key)!;
      if (shown[i]) { e.preventDefault(); e.stopPropagation(); pickAndMove(shown[i].name, d[0], d[1]); }
    }
  };

  let lastSection = "";

  // Al SOLTAR el mouse fuera de la grilla, RevoGrid le quita el foco y cierra el editor
  // (`mouseupHandle` en revo-grid: "use mouseup/touchend e.preventDefault()" para evitarlo). Las
  // opciones de la lista se salvaban porque eligen en el mousedown; el pie de "Nueva categoría" no.
  const keepOpen = (e: React.SyntheticEvent) => e.preventDefault();
  const panel = (
    <div onMouseUp={keepOpen} onTouchEnd={keepOpen} style={{
      position: "fixed", zIndex: 2000,
      top: pos?.top ?? -9999, left: pos?.left ?? -9999,
      width: 280, background: "#fff",
      border: "1px solid #1A8754", borderRadius: 8, boxShadow: "0 10px 30px rgba(15,23,42,.20)",
      overflow: "hidden",
    }}>
      <input
        ref={inputRef}
        value={q}
        onChange={(e) => { qTouched.current = true; setQ(e.target.value); }}
        onKeyDown={onKey}
        placeholder={current || t("sheet.editorPlaceholder", { defaultValue: "Type to filter or create…" })}
        style={{
          width: "100%", border: 0, borderBottom: "1px solid #e3ded2", padding: "8px 10px",
          font: "inherit", fontSize: 13.5, outline: "none",
        }}
      />
      <div ref={boxRef} style={{ maxHeight: creating ? 96 : 230, overflowY: "auto" }}>
        {/* CREAR LA CUENTA SIN SALIR DE LA HOJA. El plan sigue CERRADO —todo valor es una cuenta
            real— pero ya no hay que irse a Ajustes y volver: el bookkeeper escribe el nombre, elige
            a qué sección del P&L pertenece, y queda creada y seleccionada. Sin eso, el candado se
            sentía como "no puedo escribir". */}
        {q.trim() && shown.length === 0 && (
          <div style={{ padding: "10px 12px" }}>
            <div style={{ fontSize: 12.5, color: "#5b6b62", marginBottom: 8 }}>
              <Trans i18nKey="sheet.editorNoMatch" values={{ q: q.trim() }} components={{ b: <b /> }}
                defaults="No account matches “<b>{{q}}</b>”. Create it under:" />
            </div>
            {sectionButtons()}
          </div>
        )}
        {!q.trim() && shown.length === 0 && (
          <div style={{ padding: "10px 12px", fontSize: 12.5, color: "#5b6b62" }}>
            {t("sheet.editorEmpty", { defaultValue: "Type to search your chart of accounts." })}
          </div>
        )}
        {shown.map((o, n) => {
          const header = o.section !== lastSection ? (lastSection = o.section) : null;
          return (
            <div key={`${o.section}:${o.name}`}>
              {header && (
                <div style={{
                  padding: "6px 10px 3px", fontSize: 10.5, fontWeight: 700, letterSpacing: ".06em",
                  textTransform: "uppercase", color: "#5b6b62", background: "#FBF7EE",
                }}>
                  {sectionName(o.section)}
                </div>
              )}
              <div
                data-active={n === i}
                onMouseEnter={() => setI(n)}
                onMouseDown={(e) => { e.preventDefault(); pick(o.name); }}
                style={{
                  padding: "6px 12px", fontSize: 13.5, cursor: "pointer",
                  background: n === i ? "rgba(26,135,84,.12)" : "transparent",
                  fontWeight: o.name === current ? 700 : 400,
                }}
              >
                {o.name}{o.name === current ? " ✓" : ""}
              </div>
            </div>
          );
        })}
      </div>
      {/* Pie fijo: AGREGAR una categoría nueva, aunque lo escrito se parezca a cuentas existentes. */}
      {!(name && shown.length === 0) && (
        <div style={{ borderTop: "1px solid #e3ded2", background: "#FBF7EE", padding: creating ? "8px 10px" : 0,
                      maxHeight: creating ? 210 : undefined, overflowY: creating ? "auto" : undefined }}>
          {!creating ? (
            <button
              onMouseDown={(e) => { e.preventDefault(); setCreating(true); inputRef.current?.focus({ preventScroll: true }); }}
              style={{ display: "block", width: "100%", textAlign: "start", border: 0, background: "transparent",
                       padding: "8px 12px", fontSize: 13, fontWeight: 700, color: "#1A8754", cursor: "pointer" }}>
              {name && !exists
                ? t("sheet.editorAddNamed", { defaultValue: "+ Add “{{q}}” as a new category", q: name })
                : t("sheet.editorAdd", { defaultValue: "+ New category" })}
            </button>
          ) : !name ? (
            <div style={{ fontSize: 12.5, color: "#5b6b62" }}>
              {t("sheet.editorAddType", { defaultValue: "Type the new category's name above (in English: it's accounting data), then pick its section." })}
            </div>
          ) : exists ? (
            <div style={{ fontSize: 12.5, color: "#5b6b62" }}>
              {t("sheet.editorAddExists", { defaultValue: "“{{q}}” already exists — pick it from the list.", q: name })}
            </div>
          ) : (
            <>
              <div style={{ fontSize: 12.5, color: "#5b6b62", marginBottom: 4 }}>
                <Trans i18nKey="sheet.editorAddUnder" values={{ q: name }} components={{ b: <b /> }}
                  defaults="Create “<b>{{q}}</b>” under:" />
              </div>
              {sectionButtons()}
            </>
          )}
          {creating && (
            <button onMouseDown={(e) => { e.preventDefault(); setCreating(false); }}
              style={{ marginTop: 4, border: 0, background: "transparent", color: "#5b6b62", fontSize: 12,
                       cursor: "pointer", padding: "2px 0" }}>
              {t("sheet.editorAddCancel", { defaultValue: "Cancel" })}
            </button>
          )}
        </div>
      )}
    </div>
  );

  return (
    <div ref={hostRef} style={{ width: 0, height: 0 }}>
      {createPortal(panel, document.body)}
    </div>
  );
}

/** A qué sección del P&L pertenece una categoría del plan. Lo usan la hoja y la hoja de reglas
 *  para que la cuenta y la sección nunca queden divergentes. */
export function sectionOfCategory(name: string): string | null {
  for (const [sec, cats] of Object.entries(CHART)) {
    if (cats.includes(name)) return sec;
  }
  return null;
}

export const CATEGORY_EDITOR = "categoryPicker";
export const categoryEditors = { [CATEGORY_EDITOR]: Editor(CategoryPicker) };
