/**
 * Everything a route can touch, passed in rather than imported.
 *
 * The app is built from this one object, so a test can hand it PGlite instead
 * of Postgres, no Redis, and a fake worker — and exercise every route exactly
 * as production runs it, minus the infrastructure.
 */
import type { Redis } from "ioredis";
import type { Logger } from "pino";

import type { Db } from "./db/types";
import type { Env } from "./env";
import type { JobQueue } from "./jobs/queue";
import type { Hub } from "./realtime/hub";
import type { WorkerClient } from "./worker/client";

export interface Deps {
  env: Env;
  /** Acts for the signed-in user: every query is confined to their rows (db/tenant.ts). */
  db: Db;
  /** Sees every user's rows. Only for sign-in and background work, never a user's request. */
  systemDb: Db;
  log: Logger;
  redis: Redis | null;
  jobs: JobQueue;
  worker: WorkerClient;
  /** Connected browsers; set once the HTTP server exists. */
  hub: Hub | null;
  /** Where live events come from, decided at startup (Kafka can fall back). */
  liveEvents: "kafka" | "postgres";
}
