import { defineConfig } from "tsup";

export default defineConfig({
  entry: ["src/index.ts"],
  format: ["esm"],
  target: "node20",
  outDir: "dist",
  clean: true,
  sourcemap: true,
  splitting: false,
  // The shared package ships TypeScript source, which Node cannot load at
  // runtime, so it is compiled into the bundle rather than imported.
  noExternal: ["@commitmail/shared"],
  // A native module; it must be loaded from node_modules, not bundled.
  external: ["@node-rs/argon2"],
});
