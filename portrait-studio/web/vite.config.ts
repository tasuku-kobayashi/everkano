import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Development: `pnpm dev` (:5173) proxies /api to the FastAPI backend (:8000) so no CORS is needed.
// Production: `pnpm build` writes dist/, which the FastAPI app serves from `/` on the same port.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": { target: process.env.VITE_API_URL ?? "http://127.0.0.1:8000", changeOrigin: false },
    },
  },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 900 },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
});
