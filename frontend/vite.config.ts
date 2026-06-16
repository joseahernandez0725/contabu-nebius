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
})

