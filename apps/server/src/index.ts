/**
 * Start the server: validate config, connect, serve HTTP and WebSockets, and
 * start streaming events to browsers.
 *
 *   npm run dev -w @commitmail/server     (reloads on change)
 *   npm start   -w @commitmail/server     (after npm run build)
 */
import { createServer } from "node:http";

import { Redis } from "ioredis";
import { pino } from "pino";

import { createApp } from "./app";
import { deleteExpiredSessions } from "./auth/sessions";
import { createPgDb } from "./db/pg";
import type { Deps } from "./deps";
import { loadEnv } from "./env";
import { JobQueue } from "./jobs/queue";
import { PostgresEventFeed, type EventSource } from "./realtime/feed";
import { Hub } from "./realtime/hub";
import { Notifier } from "./realtime/notifier";
import { createWorkerClient } from "./worker/client";

async function main(): Promise<void> {
  const env = loadEnv();
  const log = pino({
    level: env.LOG_LEVEL,
    transport: env.NODE_ENV === "development" ? { target: "pino/file", options: { destination: 1 } } : undefined,
  });

  const db = createPgDb(env.DATABASE_URL, log);
  try {
    await db.query("SELECT 1");
  } catch (error) {
    const where = env.DATABASE_URL.replace(/\/\/[^@]*@/, "//");   // never print the password
    throw new Error(
      `Cannot reach PostgreSQL at ${where} (${(error as Error).message}).\n` +
        "Start it with: docker compose up -d   — then apply the schema: alembic upgrade head",
    );
  }

  let redis: Redis | null = null;
  if (env.REDIS_URL) {
    redis = new Redis(env.REDIS_URL, { lazyConnect: true, maxRetriesPerRequest: 2, enableOfflineQueue: false });
    try {
      await redis.connect();
    } catch (error) {
      // Jobs still work through the database; rate limits fall back to memory.
      log.warn({ err: error }, "Redis unreachable at startup; continuing without it");
      redis.disconnect();
      redis = null;
    }
  }

  const deps: Deps = {
    env,
    db,
    log,
    redis,
    jobs: new JobQueue(db, redis, env.REDIS_KEY_PREFIX, env.JOB_MAX_ATTEMPTS, log),
    worker: createWorkerClient(env.WORKER_URL, env.INTERNAL_API_TOKEN),
    hub: null,
  };

  const server = createServer(createApp(deps));
  const hub = new Hub(db, env.APP_ORIGINS, env.SESSION_TTL_HOURS, log);
  hub.attach(server);
  deps.hub = hub;

  const notifier = new Notifier(db, log, (notification) => hub.broadcast({ type: "notification", notification }));
  const feed: EventSource = new PostgresEventFeed(db, log, async (events) => {
    for (const event of events) hub.broadcast({ type: "event", event });
    void notifier.wake();
  });
  await notifier.start();
  await feed.start();

  const sweep = setInterval(() => void deleteExpiredSessions(db).catch(() => undefined), 60 * 60_000);
  sweep.unref();

  server.listen(env.PORT, env.HOST, () => {
    log.info(`CommitMail server on http://${env.HOST}:${env.PORT} (jobs via ${deps.jobs.dispatcher})`);
  });

  const shutdown = async (signal: string) => {
    log.info({ signal }, "shutting down");
    server.close();
    await feed.stop();
    notifier.stop();
    await hub.close();
    redis?.disconnect();
    await db.close();
    process.exit(0);
  };
  process.on("SIGINT", () => void shutdown("SIGINT"));
  process.on("SIGTERM", () => void shutdown("SIGTERM"));
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
});
