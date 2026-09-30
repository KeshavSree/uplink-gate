import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In development the UI is served by Vite and the API by the gate service.
const api = process.env.GATE_API_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target: api, ws: true, changeOrigin: true },
    },
  },
  build: { outDir: "dist", chunkSizeWarningLimit: 1500 },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
