import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["test/**/*.test.ts"],
    // PGlite boots a full Postgres in WebAssembly; give a cold start room.
    testTimeout: 20_000,
    hookTimeout: 60_000,
    // Each file gets its own in-process database; files run in parallel.
    pool: "forks",
  },
});
