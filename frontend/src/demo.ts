// El despliegue de DEMO del concurso (FASE 13, área Firm). Se enciende al COMPILAR con
// `VITE_DEMO_MODE=true` (el sitio demo de Firebase Hosting); en producción y en desarrollo está apagado.
// Lo usan: el botón "Probar el demo" del login (login anónimo de Firebase) y el panel del recorrido.
// El backend tiene su propia bandera (`DEMO_MODE` en `backend/demo.py`): son dos despliegues aparte.
export const DEMO_MODE = import.meta.env.VITE_DEMO_MODE === "true";

// El resumen del inicio (solo demo, 18-oct) ofrece el recorrido: este evento lo despliega si estaba
// minimizado. `DemoTour.tsx` lo escucha; App.tsx no cambia.
export const OPEN_TOUR_EVENT = "contabu:demo-tour-open";
export function openDemoTour() {
  window.dispatchEvent(new Event(OPEN_TOUR_EVENT));
}
