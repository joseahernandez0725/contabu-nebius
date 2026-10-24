// El despliegue de DEMO del concurso (FASE 13, área Firm). Se enciende al COMPILAR con
// `VITE_DEMO_MODE=true` (el sitio demo de Firebase Hosting); en producción y en desarrollo está apagado.
// Lo usan: las dos puertas del login, la pantalla del código, el aviso de la visita y el recorrido.
// El backend tiene su propia bandera (`DEMO_MODE` en `backend/demo.py`): son dos despliegues aparte.
import { signInWithPopup } from "firebase/auth";
import { auth, googleProvider } from "./firebase";

export const DEMO_MODE = import.meta.env.VITE_DEMO_MODE === "true";

// El resumen del inicio (solo demo, 18-oct) ofrece el recorrido: este evento lo despliega si estaba
// minimizado. `DemoTour.tsx` lo escucha; App.tsx no cambia.
export const OPEN_TOUR_EVENT = "contabu:demo-tour-open";
export function openDemoTour() {
  window.dispatchEvent(new Event(OPEN_TOUR_EVENT));
}

// EL RECORRIDO GUIADO (23-oct): la pantalla de inicio + 7 paradas (`DemoGuide.tsx`). Lo abren el botón "Guide"
// de la barra de arriba y el del resumen del inicio. La lista de antes (`DemoTour.tsx`) queda solo en el demo en
// vivo, como "Cosas para probar".
export const OPEN_GUIDE_EVENT = "contabu:demo-guide-open";
export function openDemoGuide() {
  window.dispatchEvent(new Event(OPEN_GUIDE_EVENT));
}

// ── LAS DOS PUERTAS (23-oct) ─────────────────────────────────────────────────────────────────────
// `/api/me` dice por cuál entró el usuario (`demo.door`): "tour" (visita guiada, anónima, solo lectura) ·
// "live" (Google + código: su oficina) · "needs_code" (Google sin código) · "tour_missing".
export type DemoDoor = "tour" | "live" | "needs_code" | "tour_missing";

// ¿Se está mirando la visita guiada? Lo fija App.tsx al pintar (antes que cualquier página). Es una variable
// de módulo, no un estado, a propósito: así `<LiveOnly>` y `api.ts` lo leen sin que las páginas de otras
// áreas tengan que recibir nada nuevo.
let touring = false;
export function setTouring(v: boolean) { touring = v; }
export function inTour(): boolean { return DEMO_MODE && touring; }

// Google sin código que miraba la visita y quiere entrar: App.tsx escucha y vuelve a la pantalla del código.
export const ENTER_CODE_EVENT = "contabu:demo-enter-code";

/** "Entra con Google" desde la visita: el anónimo inicia sesión con Google (la app vuelve a cargar sola con
 *  la cuenta nueva y le pide el código); quien ya entró con Google sin código va a la pantalla del código. */
export async function goLive(): Promise<void> {
  if (auth.currentUser && !auth.currentUser.isAnonymous) {
    window.dispatchEvent(new Event(ENTER_CODE_EVENT));
    return;
  }
  try {
    await signInWithPopup(auth, googleProvider);
  } catch {
    /* cerró la ventana de Google: sigue en la visita */
  }
}
