import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The built app goes where FastAPI serves it from. During `npm run dev`,
// API and image requests go to a running backend (python -m app, port 8796 by default).
const backend = process.env.PROGRAMARR_BACKEND ?? "http://127.0.0.1:8796";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: "../app/web/dist",
    emptyOutDir: true,
    chunkSizeWarningLimit: 900,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": backend,
      "/img": backend,
      "/health": backend,
    },
  },
});
