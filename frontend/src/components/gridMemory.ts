// LA HOJA RECUERDA DÓNDE LA DEJASTE (Jose, 19-oct; Movimientos). Al pasar de Bookkeeping a Reglas o al
// Pivote, la grilla se desmonta; al volver, RevoGrid nace arriba a la izquierda. Aquí se guarda, por
// pestaña y mientras viva la Hoja (o sea, la sesión en ESE libro: `App` la monta con `key={company.id}`),
// el desplazamiento y la celda enfocada, y al volver se ponen igual. Nada va al navegador.
//
// ✅ API verificada en el código instalado (@revolist/revogrid 4.28.0):
// - el evento `viewportscroll` trae `{ dimension: "rgRow" | "rgCol", coordinate }`, con `coordinate` en
//   PÍXELES (lo emite `GridScrollingService` con lo mismo que guarda `setViewPortCoordinate`);
// - `scrollToCoordinate({ x, y })` recibe esos mismos píxeles (`scrollToRow(i)` convierte el índice a
//   píxeles y lo llama: `revo-grid.js`, `scrollToRow` → `getViewPortPos` → `scrollToCoordinate({ y })`).

import type { DateFilter } from "./DateFilter";

export type GridSpot = {
  x: number;              // desplazamiento horizontal, px
  y: number;              // desplazamiento vertical, px
  id: number | null;      // la fila enfocada (id de la transacción o de la regla)
  prop: string | null;    // y su columna
};

export const emptySpot = (): GridSpot => ({ x: 0, y: 0, id: null, prop: null });

type Grid = HTMLElement & {
  scrollToCoordinate?: (c: { x?: number; y?: number }) => Promise<void>;
  getVisibleSource?: () => Promise<any[]>;
};

/** Anota el desplazamiento de `el` en `spot`. Mientras `paused()` (la grilla recién montada, todavía en 0,
 *  antes de restablecer), no escribe: si no, el 0 inicial borraría lo guardado. Devuelve cómo soltarlo. */
export function trackScroll(el: HTMLElement, spot: { current: GridSpot }, paused: () => boolean): () => void {
  const onScroll = (ev: Event) => {
    if (paused()) return;
    const d = (ev as CustomEvent).detail ?? {};
    if (typeof d.coordinate !== "number") return;
    if (d.dimension === "rgRow") spot.current = { ...spot.current, y: d.coordinate };
    else if (d.dimension === "rgCol") spot.current = { ...spot.current, x: d.coordinate };
  };
  el.addEventListener("viewportscroll", onScroll);
  return () => el.removeEventListener("viewportscroll", onScroll);
}

/** Pone la grilla donde estaba: primero la celda enfocada (`focusCell`, que puede desplazar la vista) y
 *  después el desplazamiento guardado, que es el que manda. Espera a que la grilla esté lista. */
export async function restoreSpot(
  get: () => Grid | null,
  spot: GridSpot,
  focusCell: (grid: Grid, y: number) => Promise<void>,
  alive: () => boolean,
): Promise<void> {
  let grid = get();
  for (let tries = 0; alive() && !grid?.scrollToCoordinate && tries < 40; tries++) {
    await new Promise((r) => window.setTimeout(r, 50));
    grid = get();
  }
  if (!alive() || !grid?.scrollToCoordinate) return;
  // Que pinte las filas. Con `setTimeout`, no `requestAnimationFrame`: este no corre en una pestaña que no se
  // está dibujando, y la restauración se quedaba esperando (visto en el panel del navegador, 19-oct).
  await new Promise((r) => window.setTimeout(r, 60));
  if (!alive()) return;
  try {
    if (spot.id != null) {
      const shown: any[] = (await grid.getVisibleSource?.()) ?? [];
      const y = shown.findIndex((r) => r?.id === spot.id);
      if (y >= 0) await focusCell(grid, y);
    }
    await grid.scrollToCoordinate({ x: spot.x, y: spot.y });
  } catch { /* la grilla se fue (otra pestaña): no pasa nada */ }
}

// EL FILTRO DE FECHAS DE LA HOJA, POR LIBRO (Jose, 19-oct). La Hoja se desmonta al ir a otra pantalla
// (Auditoría, Notas…); el filtro no debe perderse por eso. Vive aquí, en memoria, uno por libro, hasta
// que se recarga la página. Nada va al navegador.
const dateFilters = new Map<number, DateFilter>();
export const rememberedDateFilter = (book: number | null): DateFilter => (book == null ? null : dateFilters.get(book) ?? null);
export function rememberDateFilter(book: number | null, f: DateFilter) {
  if (book == null) return;
  if (f) dateFilters.set(book, f); else dateFilters.delete(book);
}
