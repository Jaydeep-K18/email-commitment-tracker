/**
 * Test harness: the real app, on a real Postgres, with no infrastructure.
 *
 * PGlite is Postgres compiled to WebAssembly, running in-process. It is loaded
 * with packages/shared/contracts/schema.sql — the exact DDL Alembic runs in
 * production — so these tests exercise the true schema: the full-text column,
 * the NOTIFY trigger, the CHECK and UNIQUE constraints. One database per test
 * file, emptied between tests.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { PGlite, types } from "@electric-sql/pglite";
import type { Express } from "express";
import { pino } from "pino";
import request from "supertest";

import { createApp } from "../src/app";
import { hashPassword } from "../src/auth/password";
import { tenantDb } from "../src/db/tenant";
import type { Db, Queryable } from "../src/db/types";
import type { Deps } from "../src/deps";
import { envSchema, findRepoRoot, type Env } from "../src/env";
import { JobQueue } from "../src/jobs/queue";
import type { WorkerClient } from "../src/worker/client";

const SCHEMA = readFileSync(join(findRepoRoot(), "packages", "shared", "contracts", "schema.sql"), "utf8");

export function pgliteDb(pg: PGlite): Db {
  const wrap = (client: Pick<PGlite, "query">): Queryable => ({
    async query<T>(sql: string, params: unknown[] = []) {
      const result = await client.query<T>(sql, params as any[]);
      return { rows: result.rows, rowCount: result.affectedRows ?? result.rows.length };
    },
  });
  const root = wrap(pg);
  return {
    query: root.query,
    transaction: (fn) => pg.transaction((tx) => fn(wrap(tx))),
    async listen(channel, onMessage) {
      const unsubscribe = await pg.listen(channel, onMessage);
      return async () => unsubscribe();
    },
    close: () => pg.close(),
  };
}

export async function createTestDb(): Promise<{ pg: PGlite; db: Db }> {
  const pg = new PGlite({
    parsers: {
      [types.INT8]: (value: string) => Number(value),
      [types.NUMERIC]: (value: string) => Number(value),
      [types.TIMESTAMP]: (value: string) => value,
    },
  });
  await pg.exec(SCHEMA);
  return { pg, db: pgliteDb(pg) };
}

const TABLES = [
  "notifications", "job_attempts", "jobs", "events", "calendar_flags", "sync_log",
  "email_tags", "commitments", "raw_emails", "tags", "saved_views", "vip_contacts",
  "sent_messages", "settings", "users", "sessions", "metric_snapshots", "service_heartbeats",
  "system_state",
];

export async function resetDb(db: Db): Promise<void> {
  await db.query(`TRUNCATE ${TABLES.join(", ")} RESTART IDENTITY CASCADE`);
}

export const ORIGIN = "http://127.0.0.1:4000";

export function testEnv(overrides: Record<string, string> = {}): Env {
  return envSchema.parse({
    NODE_ENV: "test",
    DATABASE_URL: "postgres://test@localhost/test",
    APP_ORIGINS: ORIGIN,
    INTERNAL_API_TOKEN: "test-internal",
    LOG_LEVEL: "silent",
    ...overrides,
  });
}

/** A worker that answers setup calls from a script and records them. */
export class FakeWorker implements WorkerClient {
  calls: Array<{ method: string; path: string; body?: unknown }> = [];
  responses = new Map<string, unknown>();

  async internal<T>(method: string, path: string, body?: unknown): Promise<T> {
    this.calls.push({ method, path, body });
    return (this.responses.get(`${method} ${path}`) ?? {}) as T;
  }

  async forward(req: any, res: any, target: string): Promise<void> {
    this.calls.push({ method: req.method, path: target, body: req.body });
    res.json({ forwardedTo: target });
  }

  async ping() {
    return { ok: true, latencyMs: 1 };
  }
}

export interface TestApp {
  app: Express;
  deps: Deps;
  db: Db;
  worker: FakeWorker;
}

/**
 * The app as production builds it: `db` (PGlite, a superuser) is the system
 * connection, and requests get the tenant wrapper, so row-level security is
 * really enforced in these tests. Assertions that query `db` directly see
 * every user's rows.
 */
export function buildApp(db: Db, envOverrides: Record<string, string> = {}): TestApp {
  const log = pino({ level: "silent" });
  const env = testEnv(envOverrides);
  const worker = new FakeWorker();
  const scoped = tenantDb(db);
  const deps: Deps = {
    env,
    db: scoped,
    systemDb: db,
    log,
    redis: null,
    jobs: new JobQueue(scoped, null, env.REDIS_KEY_PREFIX, env.JOB_MAX_ATTEMPTS, log),
    worker,
    hub: null,
    liveEvents: "postgres",
  };
  return { app: createApp(deps), deps, db, worker };
}

export const OWNER = { email: "owner@example.com", displayName: "Jaydeep", password: "correct horse battery" };

/** An agent signed in as the owner, sending the CSRF token and Origin like the app does. */
export async function signedIn(app: Express) {
  const agent = request.agent(app);
  const setup = await agent.post("/api/auth/setup").set("Origin", ORIGIN).send(OWNER);
  if (setup.status !== 201) throw new Error(`setup failed: ${setup.status} ${JSON.stringify(setup.body)}`);
  return sessionFor(agent, setup.body.csrfToken);
}

/** Another account (not an admin), created directly and signed in. */
export async function signedInAs(app: Express, db: Queryable, email: string, password = "another long password") {
  await db.query("INSERT INTO users (email, password_hash) VALUES ($1, $2)", [email, await hashPassword(password)]);
  const agent = request.agent(app);
  const login = await agent.post("/api/auth/login").set("Origin", ORIGIN).send({ email, password });
  if (login.status !== 200) throw new Error(`login failed: ${login.status} ${JSON.stringify(login.body)}`);
  return sessionFor(agent, login.body.csrfToken);
}

function sessionFor(agent: ReturnType<typeof request.agent>, csrf: string) {
  return {
    agent,
    csrf,
    get: (url: string) => agent.get(url),
    post: (url: string, body?: unknown) => agent.post(url).set("Origin", ORIGIN).set("X-CSRF-Token", csrf).send(body as object),
    patch: (url: string, body?: unknown) => agent.patch(url).set("Origin", ORIGIN).set("X-CSRF-Token", csrf).send(body as object),
    put: (url: string, body?: unknown) => agent.put(url).set("Origin", ORIGIN).set("X-CSRF-Token", csrf).send(body as object),
    del: (url: string) => agent.delete(url).set("Origin", ORIGIN).set("X-CSRF-Token", csrf),
  };
}

// --- Fixtures ------------------------------------------------------------------

let sequence = 0;

/** Rows seeded straight into the database belong to the first account unless a test says otherwise. */
async function owned(db: Queryable, row: Record<string, unknown>): Promise<Record<string, unknown>> {
  if ("user_id" in row) return row;
  const { rows } = await db.query<{ id: number | null }>("SELECT min(id) AS id FROM users");
  if (rows[0]?.id == null) throw new Error("seed data needs an account: sign in first");
  return { user_id: rows[0].id, ...row };
}

export async function insertEmail(db: Queryable, fields: Record<string, unknown> = {}): Promise<number> {
  sequence += 1;
  const row = await owned(db, {
    message_id: `m-${sequence}@example.com`,
    sender_email: "priya@example.com",
    sender_name: "Priya Nair",
    subject: `Subject ${sequence}`,
    body_text: "Please send the quarterly report by Friday.",
    received_at: "2026-09-01 09:00:00",
    vip_tier: "CRITICAL",
    category: "important",
    processed: true,
    ...fields,
  });
  const columns = Object.keys(row);
  const { rows } = await db.query<{ id: number }>(
    `INSERT INTO raw_emails (${columns.join(", ")})
     VALUES (${columns.map((_, i) => `$${i + 1}`).join(", ")}) RETURNING id`,
    Object.values(row),
  );
  return rows[0]!.id;
}

export async function insertCommitment(db: Queryable, emailId: number, fields: Record<string, unknown> = {}): Promise<number> {
  const row = await owned(db, {
    email_id: emailId,
    type: "deadline_on_you",
    subject: "Send the quarterly report",
    deadline: "2026-09-05 17:00:00",
    evidence_quote: "Please send the quarterly report by Friday.",
    confidence: 0.9,
    vip_tier: "CRITICAL",
    status: "pending",
    ...fields,
  });
  const columns = Object.keys(row);
  const { rows } = await db.query<{ id: number }>(
    `INSERT INTO commitments (${columns.join(", ")})
     VALUES (${columns.map((_, i) => `$${i + 1}`).join(", ")}) RETURNING id`,
    Object.values(row),
  );
  return rows[0]!.id;
}

export async function eventTypes(db: Queryable): Promise<string[]> {
  const { rows } = await db.query<{ type: string }>("SELECT type FROM events ORDER BY id");
  return rows.map((r) => r.type);
}
