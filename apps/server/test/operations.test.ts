import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";

import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";

import type { Db } from "../src/db/types";
import { buildApp, createTestDb, eventTypes, insertCommitment, insertEmail, resetDb, signedIn, type TestApp } from "./helpers";

let db: Db;
let t: TestApp;
let session: Awaited<ReturnType<typeof signedIn>>;

beforeAll(async () => {
  ({ db } = await createTestDb());
});
afterAll(async () => db.close());
beforeEach(async () => {
  await resetDb(db);
  t = buildApp(db, { OLLAMA_HOST: "http://127.0.0.1:9" });   // nothing listens there
  session = await signedIn(t.app);
});

describe("settings", () => {
  it("starts from sensible defaults", async () => {
    const res = await session.get("/api/settings");
    expect(res.body.email.fetchIntervalMinutes).toBe(15);
    expect(res.body.calendar.workingHours).toEqual({ start: "09:00", end: "18:00", days: [1, 2, 3, 4, 5] });
    expect(res.body.appearance.theme).toBe("system");
  });

  it("saves a whole section and records the change", async () => {
    const res = await session.put("/api/settings/appearance", { theme: "dark", density: "compact", reducedMotion: true });
    expect(res.body.appearance).toEqual({ theme: "dark", density: "compact", reducedMotion: true });
    expect(await eventTypes(db)).toContain("settings.updated");
  });

  it("rejects values outside what the worker can honour", async () => {
    expect((await session.put("/api/settings/email", { fetchIntervalMinutes: 1 })).status).toBe(400);
    expect((await session.put("/api/settings/calendar", { timeZone: "Mars/Olympus" })).status).toBe(400);
    expect((await session.put("/api/settings/calendar", { workingHours: { start: "18:00", end: "09:00" } })).status).toBe(400);
    expect((await session.put("/api/settings/server_state", {})).status).toBe(400);
  });
});

describe("contacts", () => {
  it("normalises the rule, counts matching mail, and re-tiers stored email", async () => {
    await insertEmail(db, { sender_email: "boss@acme.com" });
    const res = await session.post("/api/contacts", { matchType: "domain", matchValue: "@ACME.com", tier: "CRITICAL" });

    expect(res.status).toBe(201);
    expect(res.body).toMatchObject({ matchValue: "acme.com", tier: "CRITICAL", matchingEmails: 1 });
    const jobs = await db.query("SELECT type FROM jobs");
    expect(jobs.rows.map((r) => r.type)).toEqual(["apply_vip_rules"]);
  });

  it("refuses a malformed address and a duplicate rule", async () => {
    expect((await session.post("/api/contacts", { matchType: "exact_email", matchValue: "not-an-email", tier: "CRITICAL" })).status).toBe(400);
    await session.post("/api/contacts", { matchType: "exact_email", matchValue: "a@b.com", tier: "CRITICAL" });
    expect((await session.post("/api/contacts", { matchType: "exact_email", matchValue: "A@B.com", tier: "MONITOR" })).status).toBe(409);
  });
});

describe("analytics", () => {
  it("fills every day of the range, so charts have no gaps", async () => {
    const res = await session.get("/api/analytics").query({ range: "7d", tz: "UTC" });
    expect(res.body.volume).toHaveLength(7);
    expect(res.body.volume.every((d: { total: number }) => d.total === 0)).toBe(true);
    expect(res.body.conversion.rate).toBeNull();   // nothing analysed: no rate, not 0%
  });

  it("buckets by the user's own day, not UTC's", async () => {
    // 20:00 UTC two days ago is 05:00 the next morning in Tokyo (UTC+9). Both
    // days sit inside a 7-day range in either zone, whatever the time is now.
    const day = (offset: number) => new Date(Date.now() + offset * 86_400_000).toISOString().slice(0, 10);
    await insertEmail(db, { received_at: `${day(-2)} 20:00:00` });

    const dayOf = async (tz: string) => {
      const res = await session.get("/api/analytics").query({ range: "7d", tz });
      return res.body.volume.find((d: { total: number }) => d.total > 0)?.date;
    };
    expect(await dayOf("UTC")).toBe(day(-2));
    expect(await dayOf("Asia/Tokyo")).toBe(day(-1));
  });

  it("measures conversion, categories, senders and reply time", async () => {
    const now = new Date();
    const stamp = (minutesAgo: number) => new Date(now.getTime() - minutesAgo * 60_000).toISOString().replace("T", " ").slice(0, 19);
    const analysed = await insertEmail(db, { message_id: "a@x", category: "action_required", received_at: stamp(120) });
    await insertCommitment(db, analysed, { calendar_synced: true });
    await insertEmail(db, { category: "update", received_at: stamp(60) });
    await insertEmail(db, { category: "low_priority", vip_tier: "SKIP", received_at: stamp(30) });
    await db.query("INSERT INTO sent_messages (user_id, message_id, in_reply_to, sent_at) VALUES ((SELECT min(id) FROM users), 'r@me', 'a@x', $1)", [stamp(60)]);

    const res = await session.get("/api/analytics").query({ range: "7d", tz: "UTC" });
    expect(res.body.byCategory).toMatchObject({ action_required: 1, update: 1, low_priority: 1 });
    expect(res.body.actionVsInformational).toEqual({ action: 1, informational: 2 });
    expect(res.body.conversion).toEqual({ analyzed: 2, withCommitments: 1, onCalendar: 1, rate: 50 });
    expect(res.body.responseTime).toEqual({ replied: 1, medianMinutes: 60, p90Minutes: 60 });
    expect(res.body.topSenders[0]).toMatchObject({ email: "priya@example.com", count: 3 });
  });

  it("rejects an unknown time zone", async () => {
    expect((await session.get("/api/analytics").query({ tz: "Nowhere/Land" })).status).toBe(400);
  });
});

describe("privacy", () => {
  it("exports the user's data but never the password hash or sessions", async () => {
    await insertEmail(db);
    const res = await session.get("/api/privacy/export");
    expect(res.headers["content-disposition"]).toMatch(/attachment; filename="commitmail-export-/);
    expect(Object.keys(res.body.data)).not.toContain("users");
    expect(Object.keys(res.body.data)).not.toContain("sessions");
    expect(JSON.stringify(res.body)).not.toContain("$argon2id$");
    expect(res.body.data.raw_emails).toHaveLength(1);
  });

  it("will not purge without the typed confirmation", async () => {
    expect((await session.post("/api/privacy/purge", { scope: "everything" })).status).toBe(400);
    expect((await session.post("/api/privacy/purge", { scope: "everything", confirm: "delete" })).status).toBe(400);
  });

  it("purging everything keeps the account and queues removal of Google events", async () => {
    const emailId = await insertEmail(db);
    await insertCommitment(db, emailId, { gcal_event_id: "ect123" });

    const res = await session.post("/api/privacy/purge", { scope: "everything", confirm: "DELETE" });
    expect(res.body.googleRemovalsQueued).toBe(1);
    expect((await db.query("SELECT count(*)::int AS n FROM raw_emails")).rows[0]!.n).toBe(0);
    expect((await db.query("SELECT count(*)::int AS n FROM users")).rows[0]!.n).toBe(1);
    const jobs = await db.query("SELECT type, payload FROM jobs ORDER BY id");
    expect(jobs.rows[0]).toEqual({ type: "remove_google_event", payload: { commitment_id: expect.any(Number), event_id: "ect123" } });
    expect((await session.get("/api/emails")).status).toBe(200);   // still signed in
  });
});

describe("system health", () => {
  it("reports each component honestly", async () => {
    const res = await session.get("/api/system/health");
    const c = res.body.components;
    expect(c.database.state).toBe("ok");
    expect(c.redis.state).toBe("unconfigured");
    expect(c.kafka.state).toBe("unconfigured");
    expect(c.worker.state).toBe("down");          // never sent a heartbeat
    expect(c.ollama.state).toBe("down");
    expect(res.body.status).toBe("degraded");
  });

  it("sees a worker that heartbeat recently", async () => {
    await db.query("INSERT INTO service_heartbeats (service, details) VALUES ('worker', '{\"dispatcher\": \"database\"}')");
    const res = await session.get("/api/system/health");
    expect(res.body.components.worker.state).toBe("ok");
  });

  it("computes live metrics from the database when Flink is not running", async () => {
    const res = await session.get("/api/system/metrics").query({ minutes: 10 });
    expect(res.body.source).toBe("fallback");
    expect(res.body.windows).toHaveLength(10);
    expect(res.body.windows[0].metrics).toMatchObject({ events: expect.any(Number), errorSpike: false });
  });

  it("counts the minute from the events table, as Flink counts it from the topic", async () => {
    await db.query("DELETE FROM events");
    const rows: Array<[string, string]> = [
      ["email.analyzed", "{}"],
      ["job.completed", '{"duration_ms": 100}'],
      ["job.completed", '{"duration_ms": 300}'],
      ["job.completed", '{"duration_ms": "n/a"}'],
      ["job.retrying", "{}"],
      ["job.failed", "{}"],
      ["job.failed", "{}"],
    ];
    for (const [type, payload] of rows) {
      await db.query(
        `INSERT INTO events (type, severity, message, payload, source, created_at)
         VALUES ($1, 'info', 'x', $2::jsonb, 'worker', date_trunc('minute', now() at time zone 'utc') + interval '1 second')`,
        [type, payload],
      );
    }
    const { windows } = (await session.get("/api/system/metrics").query({ minutes: 5 })).body;
    expect(windows.at(-1).metrics).toEqual({
      events: 7,
      emailsProcessed: 1,
      throughputPerMinute: 1,
      avgLatencyMs: 200,
      p95LatencyMs: 290,
      successRate: 50,
      failures: 3,
      errorSpike: true,      // three failures in an otherwise quiet hour
      volumeAnomaly: false,
    });
  });

  it("prefers Flink's windows while they are fresh", async () => {
    const now = new Date();
    const fmt = (d: Date) => d.toISOString().replace("T", " ").slice(0, 19);
    await db.query(
      `INSERT INTO metric_snapshots (source, "window", window_start, window_end, metrics)
       VALUES ('flink', '1m', $1, $2, '{"events": 7}')`,
      [fmt(new Date(now.getTime() - 60_000)), fmt(now)],
    );
    const res = await session.get("/api/system/metrics");
    expect(res.body.source).toBe("flink");
    expect(res.body.windows[0].metrics.events).toBe(7);
  });
});

describe("Flink in system health", () => {
  let flink: Server;
  let jobsRunning = 1;

  beforeAll(async () => {
    flink = createServer((_req, res) => {
      res.setHeader("content-type", "application/json");
      res.end(JSON.stringify({ "jobs-running": jobsRunning, taskmanagers: 1 }));
    });
    await new Promise<void>((resolve) => flink.listen(0, "127.0.0.1", resolve));
  });
  afterAll(() => new Promise<void>((resolve) => flink.close(() => resolve())));

  async function flinkHealth(url = `http://127.0.0.1:${(flink.address() as AddressInfo).port}`) {
    await resetDb(db);
    const flinkSession = await signedIn(buildApp(db, { FLINK_URL: url }).app);
    return async () => (await flinkSession.get("/api/system/health")).body.components.flink;
  }

  async function snapshotEnding(minutesAgo: number) {
    await db.query(
      `INSERT INTO metric_snapshots (source, "window", window_start, window_end, metrics)
       VALUES ('flink', '1m', (now() at time zone 'utc') - make_interval(mins => $1::int + 1),
               (now() at time zone 'utc') - make_interval(mins => $1::int), '{}')`,
      [minutesAgo],
    );
  }

  it("is healthy while the job keeps writing windows", async () => {
    jobsRunning = 1;
    const health = await flinkHealth();
    await snapshotEnding(1);
    expect((await health()).state).toBe("ok");
  });

  it("notices a job that runs but has stopped writing", async () => {
    jobsRunning = 1;
    const health = await flinkHealth();
    await snapshotEnding(10);
    expect(await health()).toMatchObject({ state: "degraded", detail: "job running, but no metrics written for 10 min" });
  });

  it("says so when the cluster has no job", async () => {
    jobsRunning = 0;
    expect(await (await flinkHealth())()).toMatchObject({ state: "degraded", detail: "cluster up, but no job running" });
  });

  it("is down when the cluster does not answer", async () => {
    expect((await (await flinkHealth("http://127.0.0.1:9"))()).state).toBe("down");
  });
});

describe("setup", () => {
  it("validates before it bothers the worker", async () => {
    const res = await session.post("/api/setup/mailbox", { address: "not an email", password: "x" });
    expect(res.status).toBe(400);
    expect(t.worker.calls).toEqual([]);
  });

  it("forwards a valid mailbox, and checks mail straight away once it works", async () => {
    t.worker.responses.set("POST /setup/mailbox", { ok: true, message: "Connected" });
    const res = await session.post("/api/setup/mailbox", { address: "Me@Example.com", password: "app-pass" });
    expect(res.body).toEqual({ ok: true, message: "Connected" });
    expect(t.worker.calls[0]).toEqual({ method: "POST", path: "/setup/mailbox", body: { address: "me@example.com", password: "app-pass" } });
    expect((await db.query("SELECT type FROM jobs")).rows.map((r) => r.type)).toEqual(["fetch_mailbox"]);
  });

  it("forwards the Gmail panel and the calendar feed without a session", async () => {
    const panel = await session.agent.post("/ext/analyze").set("Origin", "chrome-extension://abc").send({ x: 1 });
    expect(panel.body).toEqual({ forwardedTo: "/api/analyze" });
    const feed = await session.agent.get("/calendar.ics");
    expect(feed.body).toEqual({ forwardedTo: "/calendar.ics" });
  });
});
