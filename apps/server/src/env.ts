/**
 * Configuration, validated once at startup.
 *
 * Read from the same `.env` files the Python worker reads (repo root, then
 * data/.env), so the two processes cannot be pointed at different databases by
 * accident. Anything malformed stops the server before it listens, with a
 * message naming the variable — rather than surfacing later as a confusing
 * runtime failure.
 */
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import dotenv from "dotenv";
import { z } from "zod";

/** The repository root: the nearest ancestor holding alembic.ini. */
export function findRepoRoot(start = dirname(fileURLToPath(import.meta.url))): string {
  let dir = resolve(start);
  for (;;) {
    if (existsSync(join(dir, "alembic.ini"))) return dir;
    const parent = dirname(dir);
    if (parent === dir) return process.cwd();
    dir = parent;
  }
}

const booleanText = z
  .enum(["true", "false", "1", "0", "yes", "no", "on", "off"])
  .transform((value) => ["true", "1", "yes", "on"].includes(value));

const list = z
  .string()
  .transform((value) => value.split(",").map((item) => item.trim()).filter(Boolean));

export const envSchema = z.object({
  NODE_ENV: z.enum(["development", "test", "production"]).default("development"),
  HOST: z.string().default("127.0.0.1"),
  PORT: z.coerce.number().int().min(1).max(65535).default(4000),
  LOG_LEVEL: z.enum(["fatal", "error", "warn", "info", "debug", "trace", "silent"]).default("info"),

  DATABASE_URL: z
    .string()
    .regex(/^postgres(ql)?(\+psycopg)?:\/\//, "must be a postgres:// URL")
    .transform((url) => url.replace(/^postgresql\+psycopg:\/\//, "postgres://")),
  REDIS_URL: z.string().url().optional().or(z.literal("").transform(() => undefined)),
  REDIS_KEY_PREFIX: z.string().default("commitmail"),

  /**
   * Origins allowed to make state-changing requests and open WebSockets.
   * Defaults to this server and the Vite dev server, on whatever ports they use.
   */
  APP_ORIGINS: list.optional(),
  /** The Vite dev server's port; only used to work out the default origins. */
  WEB_PORT: z.coerce.number().int().min(1).max(65535).default(5173),
  /** Send cookies only over HTTPS. Turn on behind TLS; off for plain localhost. */
  COOKIE_SECURE: booleanText.default("false"),
  SESSION_TTL_HOURS: z.coerce.number().int().min(1).max(24 * 90).default(24 * 7),
  /** Trust X-Forwarded-For — only when behind a reverse proxy you control. */
  TRUST_PROXY: booleanText.default("false"),

  /** The Python worker's HTTP endpoints (feed, Gmail panel, internal API). */
  WORKER_URL: z.string().url().default("http://127.0.0.1:8765"),
  INTERNAL_API_TOKEN: z.string().optional(),
  OLLAMA_HOST: z.string().url().default("http://localhost:11434"),
  OLLAMA_MODEL: z.string().default("llama3.2:latest"),

  JOB_MAX_ATTEMPTS: z.coerce.number().int().min(1).max(50).default(5),

  KAFKA_BROKERS: list.optional(),
  FLINK_URL: z.string().url().optional().or(z.literal("").transform(() => undefined)),

  /** Built React app to serve. Unset in development, where Vite serves it. */
  WEB_DIST: z.string().optional(),
}).transform((env) => ({
  ...env,
  APP_ORIGINS:
    env.APP_ORIGINS ??
    [env.PORT, env.WEB_PORT].flatMap((port) => [`http://127.0.0.1:${port}`, `http://localhost:${port}`]),
}));

export type Env = z.infer<typeof envSchema>;

export function loadEnv(source: NodeJS.ProcessEnv = process.env): Env {
  const root = findRepoRoot();
  // Earlier files win, matching how the Python worker resolves the same files.
  dotenv.config({ path: join(root, "data", ".env"), quiet: true });
  dotenv.config({ path: join(root, ".env"), quiet: true });

  const parsed = envSchema.safeParse(source);
  if (!parsed.success) {
    const problems = parsed.error.issues
      .map((issue) => `  ${issue.path.join(".")}: ${issue.message}`)
      .join("\n");
    throw new Error(`Invalid configuration:\n${problems}`);
  }
  return parsed.data;
}
