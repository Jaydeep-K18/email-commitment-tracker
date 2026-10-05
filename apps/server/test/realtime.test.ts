import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";

import { defaultSettings, type ActivityEvent, type Notification, type ServerMessage } from "@commitmail/shared";
import { pino } from "pino";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { WebSocket } from "ws";

import { createSession } from "../src/auth/sessions";
import type { Db } from "../src/db/types";
import { recordEvent } from "../src/events/record";
import { PostgresEventFeed } from "../src/realtime/feed";
import { CLOSE_SESSION_ENDED, Hub } from "../src/realtime/hub";
import { Notifier, notificationFor } from "../src/realtime/notifier";
import { buildApp, createTestDb, ORIGIN, resetDb, signedIn, type TestApp } from "./helpers";

const log = pino({ level: "silent" });
let db: Db;
let t: TestApp;

beforeAll(async () => {
  ({ db } = await createTestDb());
});
afterAll(async () => db.close());
beforeEach(async () => {
  await resetDb(db);
  t = buildApp(db);
});

const waitFor = async (predicate: () => boolean, ms = 3_000) => {
  const deadline = Date.now() + ms;
  while (!predicate()) {
    if (Date.now() > deadline) throw new Error("timed out waiting");
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
};

async function insertJob(status: string, attempts = 5) {
  const { rows } = await db.query<{ id: number }>(
    `INSERT INTO jobs (type, idempotency_key, status, attempts, max_attempts, last_error)
     VALUES ('process_email', 'process_email:' || floor(random() * 1e9)::text, $1, $2, 5, 'Ollama is down') RETURNING id`,
    [status, attempts],
  );
  return rows[0]!.id;
}

describe("jobs from the dashboard", () => {
  it("retries a failed job with a fresh budget and says so in the audit trail", async () => {
    const session = await signedIn(t.app);
    const id = await insertJob("failed");

    const res = await session.post(`/api/jobs/${id}/retry`);
    expect(res.status).toBe(200);
    expect(res.body).toMatchObject({ status: "queued", attempts: 5, maxAttempts: 8 });
    const audit = await db.query("SELECT type, source FROM events WHERE entity_id = $1", [String(id)]);
    expect(audit.rows).toEqual([{ type: "job.retried", source: "server" }]);
  });

  it("will not 'retry' a job that is still running", async () => {
    const session = await signedIn(t.app);
    const id = await insertJob("running", 1);
    const res = await session.post(`/api/jobs/${id}/retry`);
    expect(res.status).toBe(409);
  });

  it("retries every failed job at once", async () => {
    const session = await signedIn(t.app);
    await insertJob("failed");
    await insertJob("failed");
    await insertJob("succeeded");
    expect((await session.post("/api/jobs/retry-failed")).body).toEqual({ retried: 2 });
  });

  it("'sync now' queues a fetch and a publish, and pressing it twice does not double them", async () => {
    const session = await signedIn(t.app);
    const first = await session.post("/api/sync");
    const second = await session.post("/api/sync");
    expect(first.body.fetch.created).toBe(true);
    expect(second.body.fetch).toEqual({ jobId: first.body.fetch.jobId, created: false });
    const { rows } = await db.query("SELECT type FROM jobs ORDER BY id");
    expect(rows.map((r) => r.type)).toEqual(["fetch_mailbox", "publish_calendar"]);
  });

  it("shows a job with its full retry history", async () => {
    const session = await signedIn(t.app);
    const id = await insertJob("failed", 2);
    await db.query(
      `INSERT INTO job_attempts (job_id, attempt, status, error, duration_ms) VALUES
       ($1, 1, 'failed', 'timeout', 1200), ($1, 2, 'failed', 'timeout', 1300)`,
      [id],
    );
    const res = await session.get(`/api/jobs/${id}`);
    expect(res.body.description).toBe("analyze email #{email_id}");
    expect(res.body.history.map((a: { attempt: number }) => a.attempt)).toEqual([1, 2]);
  });
});

describe("notification rules", () => {
  const prefs = defaultSettings().notifications;
  const event = (type: string, payload: Record<string, unknown> = {}, extra: Partial<ActivityEvent> = {}): ActivityEvent => ({
    id: 1, type: type as ActivityEvent["type"], entityType: "job", entityId: "7", correlationId: null,
    severity: "info", message: "m", payload, source: "worker", createdAt: "2026-01-01T00:00:00Z", ...extra,
  });

  it("alerts on mail from a CRITICAL or IMPORTANT sender, and nobody else", () => {
    const vip = event("email.received", { vip_tier: "CRITICAL", subject: "Contract" }, { message: "Email from Priya: Contract", entityId: "42" });
    expect(notificationFor(vip, prefs)).toMatchObject({ kind: "important_email", title: "Important email from Priya", link: "/inbox/42" });
    expect(notificationFor(event("email.received", { vip_tier: "SKIP" }), prefs)).toBeNull();
  });

  it("tells you when a job fails, and when a retry rescues it", () => {
    expect(notificationFor(event("job.failed"), prefs)?.kind).toBe("job_failed");
    expect(notificationFor(event("job.completed", { attempts: 1 }), prefs)).toBeNull();
    expect(notificationFor(event("job.completed", { attempts: 3 }), prefs)?.kind).toBe("job_retry_succeeded");
  });

  it("respects the preferences", () => {
    expect(notificationFor(event("job.failed"), { ...prefs, jobFailed: false })).toBeNull();
  });
});

describe("the notifier", () => {
  it("does not announce history on its first start", async () => {
    await recordEvent(db, { type: "job.failed", message: "old failure", entityType: "job", entityId: 1 });
    const announced: Notification[] = [];
    const notifier = new Notifier(db, log, (n) => announced.push(n), 60_000);
    await notifier.start();
    notifier.stop();
    expect(announced).toEqual([]);
  });

  it("derives each notification once, however many times it is woken", async () => {
    const announced: Notification[] = [];
    const notifier = new Notifier(db, log, (n) => announced.push(n), 60_000);
    await notifier.start();
    await recordEvent(db, { type: "job.failed", message: "Failed: analyze email #3", entityType: "job", entityId: 9 });

    await Promise.all([notifier.wake(), notifier.wake(), notifier.wake()]);
    await notifier.wake();
    notifier.stop();

    expect(announced.map((n) => n.title)).toEqual(["A background job failed"]);
    expect((await db.query("SELECT count(*)::int AS n FROM notifications")).rows[0]!.n).toBe(1);
  });

  it("catches up on events from while it was stopped", async () => {
    const first = new Notifier(db, log, () => undefined, 60_000);
    await first.start();
    first.stop();
    await recordEvent(db, { type: "job.failed", message: "while down", entityType: "job", entityId: 4 });

    const announced: Notification[] = [];
    const second = new Notifier(db, log, (n) => announced.push(n), 60_000);
    await second.start();
    second.stop();
    expect(announced).toHaveLength(1);
  });
});

describe("the live event feed", () => {
  it("delivers new events as Postgres announces them", async () => {
    const received: ActivityEvent[] = [];
    const feed = new PostgresEventFeed(db, log, (events) => void received.push(...events), 60_000);
    await feed.start();

    await recordEvent(db, { type: "email.received", message: "Email from Priya: hello" });
    await waitFor(() => received.length === 1);
    await feed.stop();

    expect(received[0]).toMatchObject({ type: "email.received", source: "server" });
  });
});

describe("the WebSocket hub", () => {
  let server: Server;
  let hub: Hub;
  let url: string;

  beforeEach(async () => {
    server = createServer(t.app);
    hub = new Hub(db, [ORIGIN], 24, log);
    hub.attach(server);
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    url = `ws://127.0.0.1:${(server.address() as AddressInfo).port}/ws`;
  });
  afterEach(async () => {
    await hub.close();
    await new Promise((resolve) => server.close(resolve));
  });

  const open = (headers: Record<string, string>) =>
    new Promise<{ ws: WebSocket; status?: number; messages: ServerMessage[] }>((resolve) => {
      const messages: ServerMessage[] = [];
      const ws = new WebSocket(url, { headers });
      ws.on("message", (data) => messages.push(JSON.parse(String(data))));
      ws.once("open", () => resolve({ ws, messages }));
      ws.once("unexpected-response", (_req, res) => resolve({ ws, status: res.statusCode, messages }));
      ws.once("error", () => undefined);
    });

  async function sessionCookie() {
    const { token, session } = await createSession(db, 24, {});
    return { cookie: `cm_session=${token}`, sessionId: session.id };
  }

  it("refuses a connection without a session", async () => {
    expect((await open({ Origin: ORIGIN })).status).toBe(401);
  });

  it("refuses a connection from another site, even with a valid cookie", async () => {
    const { cookie } = await sessionCookie();
    expect((await open({ Origin: "https://evil.example", Cookie: cookie })).status).toBe(403);
  });

  it("streams broadcasts to a signed-in browser", async () => {
    const { cookie } = await sessionCookie();
    const client = await open({ Origin: ORIGIN, Cookie: cookie });
    await waitFor(() => client.messages.length === 1);   // hello

    hub.broadcast({ type: "notification", notification: { id: 1, kind: "system", title: "Hi", body: null, severity: "info", link: null, readAt: null, createdAt: "2026-01-01T00:00:00Z" } });
    await waitFor(() => client.messages.length === 2);
    expect(client.messages[1]).toMatchObject({ type: "notification" });
    client.ws.close();
  });

  it("closes the socket the moment its session signs out", async () => {
    const { cookie, sessionId } = await sessionCookie();
    const client = await open({ Origin: ORIGIN, Cookie: cookie });
    const closed = new Promise<number>((resolve) => client.ws.once("close", (code) => resolve(code)));
    hub.closeSession(sessionId);
    expect(await closed).toBe(CLOSE_SESSION_ENDED);
  });
});
