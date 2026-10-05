import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const API = process.env.API_URL ?? "http://127.0.0.1:4000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    strictPort: true,
    // Same-origin in development too: the browser talks only to Vite, which
    // forwards to Express. Cookies, CSRF and the WebSocket then behave exactly
    // as they will when Express serves the built app.
    proxy: {
      "/api": { target: API },
      "/ext": { target: API },
      "/calendar.ics": { target: API },
      "/ws": { target: API.replace(/^http/, "ws"), ws: true },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
    chunkSizeWarningLimit: 900,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    testTimeout: 15_000,
  },
});
