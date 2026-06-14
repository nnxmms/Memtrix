import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The SPA is served from the FastAPI backend at the site root in production.
// During local development, proxy /api and /healthz to the backend on :8800.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8800",
      "/healthz": "http://localhost:8800",
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
