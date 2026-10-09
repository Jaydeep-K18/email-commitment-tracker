/**
 * Two projects, two ways of running the same React app:
 *
 * - app:  the production build served by the real Express server, on a real
 *         Postgres with row-level security (its own database, see db.ts).
 *         No worker, Redis or Kafka: the flows tested here do not need them.
 * - demo: the demo build, as published on GitHub Pages, answered entirely by
 *         its in-browser service worker.
 *
 * Both builds must exist first; `npm run e2e` makes them (see package.json).
 */
import { defineConfig, devices } from "@playwright/test";

import { databaseUrl } from "./db";

const APP = "http://127.0.0.1:4400";
const DEMO = "http://127.0.0.1:4401";

export default defineConfig({
  testDir: "./tests",
  // The app project shares one database, emptied before each test.
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: { trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [
    { name: "app", testDir: "./tests/app", use: { ...devices["Desktop Chrome"], baseURL: APP } },
    // Trips the sign-in rate limit for 127.0.0.1, so it runs after everything else.
    { name: "app-last", testDir: "./tests/app-last", dependencies: ["app", "demo"], use: { baseURL: APP } },
    { name: "demo", testDir: "./tests/demo", use: { ...devices["Desktop Chrome"], baseURL: DEMO } },
  ],
  webServer: [
    {
      command: "npx tsx ../apps/server/src/index.ts",
      url: `${APP}/api/health`,
      timeout: 60_000,
      reuseExistingServer: false,
      stdout: "pipe",
      env: {
        NODE_ENV: "production",
        HOST: "127.0.0.1",
        PORT: "4400",
        LOG_LEVEL: "warn",
        DATABASE_URL: databaseUrl(),
        DATABASE_SYSTEM_URL: "",
        WEB_DIST: "apps/web/dist",
        APP_ORIGINS: APP,
        COOKIE_SECURE: "false",
        // Off, so the server cannot reach the developer's own Redis, Kafka,
        // Flink or worker from .env.
        REDIS_URL: "",
        KAFKA_BROKERS: "",
        FLINK_URL: "",
        INTERNAL_API_TOKEN: "",
        WORKER_URL: "http://127.0.0.1:9",
      },
    },
    {
      command: "npx vite preview --mode demo --port 4401 --strictPort --host 127.0.0.1",
      cwd: "../apps/web",
      url: DEMO,
      timeout: 60_000,
      reuseExistingServer: false,
    },
  ],
});
