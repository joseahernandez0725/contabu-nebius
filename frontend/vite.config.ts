import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Fuerza una sola copia de React (evita "Invalid hook call" por React duplicado).
  resolve: {
    dedupe: ["react", "react-dom"],
  },
  optimizeDeps: {
    // RevoGrid es un web component (Stencil) que carga sus piezas con imports DINÁMICOS. Cuando
    // Vite lo pre-empaqueta, cualquier cambio en el grafo de dependencias invalida ese paquete y
    // el navegador se queda pidiendo el hash viejo: `504 (Outdated Optimize Dep)` → la grilla no
    // monta y la hoja se queda en "Loading…" sin ningún error de código.
    //
    // Pasó cuatro veces el 27-sep (al añadir el editor de categorías, el pivote, la hoja de reglas
    // y el borrador). Excluirlo lo arregla de raíz: ya viene en ESM, no necesita pre-empaquetado.
    exclude: ["@revolist/revogrid", "@revolist/react-datagrid"],
  },
})

