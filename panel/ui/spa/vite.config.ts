import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// FASE C.1: SPA React/Vite + TanStack Router (manual, no file-based).
// Dev: vite dev server con proxy a Django (:8000) para /api y /ws.
// Build: vite build → dist/ servido por whitenoise (panel/staticfiles-like).
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: false },
      "/ws": { target: "ws://127.0.0.1:8000", ws: true, changeOrigin: false },
      "/static": { target: "http://127.0.0.1:8000", changeOrigin: false },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    target: "es2022",
  },
});