import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { parseEnv } from "node:util";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";

/**
 * The repository's .env, shared with the server and the worker (data/.env
 * wins, as it does for them). Read as files rather than through process.env:
 * a preview runner sets PORT to this dev server's port, not the API's.
 */
function sharedEnv(): Record<string, string> {
  const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
  const values: Record<string, string> = {};
  for (const file of [join(root, ".env"), join(root, "data", ".env")]) {
    if (existsSync(file)) Object.assign(values, parseEnv(readFileSync(file, "utf8")));
  }
  return values;
}

const shared = sharedEnv();
const API = process.env.API_URL ?? shared.API_URL ?? `http://127.0.0.1:${shared.PORT || 4000}`;
const WEB_PORT = Number(process.env.PORT) || Number(process.env.WEB_PORT ?? shared.WEB_PORT) || 5173;

/**
 * Demo mode answers the API from a service worker. Its script comes straight
 * from the installed msw package — served in dev, emitted in a demo build —
 * so it always matches the library and never ships in a normal build.
 */
function demoServiceWorker(): Plugin {
  const source = () => readFileSync(createRequire(import.meta.url).resolve("msw/mockServiceWorker.js"), "utf8");
  return {
    name: "commitmail-demo-service-worker",
    configureServer(server) {
      server.middlewares.use("/mockServiceWorker.js", (_req, res) => {
        res.setHeader("Content-Type", "text/javascript");
        res.end(source());
      });
    },
    generateBundle() {
      this.emitFile({ type: "asset", fileName: "mockServiceWorker.js", source: source() });
    },
  };
}

export default defineConfig(({ mode }) => ({
  plugins: [react(), tailwindcss(), mode === "demo" && demoServiceWorker()],
  server: {
    port: WEB_PORT,
    strictPort: true,
    // Same-origin in development too: the browser talks only to Vite, which
    // forwards to Express. Cookies, CSRF and the WebSocket then behave exactly
    // as they will when Express serves the built app. The demo needs no server.
    proxy:
      mode === "demo"
        ? undefined
        : {
            "/api": { target: API },
            "/ext": { target: API },
            "/calendar.ics": { target: API },
            "/ws": { target: API.replace(/^http/, "ws"), ws: true },
          },
  },
  build: {
    outDir: mode === "demo" ? "dist-demo" : "dist",
    sourcemap: true,
    chunkSizeWarningLimit: 900,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    testTimeout: 15_000,
  },
}));
