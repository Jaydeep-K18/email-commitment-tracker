/**
 * One account can never see or change another's data.
 *
 * Alice (the first account, an admin) has mail, commitments, tags, a saved
 * view, a contact rule, a notification, a job, a calendar flag and settings.
 * Bob, signed in on the same server, tries every route at them — listing,
 * fetching by id, changing, bulk-changing, tagging, exporting, purging — and
 * gets only his own (empty) data back, while Alice's rows stay untouched.
 *
 * Row-level security does this in the database (migration 0005), beneath the
 * route code: these tests go through the real app with the real policies.
 */
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";

import { tenantDb } from "../src/db/tenant";
import type { Db } from "../src/db/types";
import { buildApp, createTestDb, insertCommitment, insertEmail, resetDb, signedIn, signedInAs, type TestApp } from "./helpers";

let db: Db;
let t: TestApp;
let alice: Awaited<ReturnType<typeof signedIn>>;
let bob: Awaited<ReturnType<typeof signedIn>>;
const ids: Record<string, number> = {};

const day = (offset: number) => new Date(Date.now() + offset * 86_400_000).toISOString().slice(0, 10);

beforeAll(async () => {
  ({ db } = await createTestDb());
});
afterAll(async () => db.close());

beforeEach(async () => {
  await resetDb(db);
  t = buildApp(db);
  alice = await signedIn(t.app);   // first account: id 1, so the seed helpers give her these rows

  ids.email = await insertEmail(db, { subject: "Alice's contract" });
  ids.commitment = await insertCommitment(db, ids.email, { subject: "Sign Alice's contract", deadline: `${day(2)} 10:00:00` });
  ids.tag = (await alice.post("/api/tags", { name: "alice-only", color: "slate" })).body.id;
  ids.view = (await alice.post("/api/views", { name: "Alice's view", filters: {}, sort: {}, isPinned: false })).body.id;
  ids.contact = (await alice.post("/api/contacts", { matchValue: "boss@acme.com", matchType: "exact_email", tier: "CRITICAL" })).body.id;
  await alice.put("/api/settings/appearance", { theme: "dark", density: "compact", reducedMotion: true });
  ids.job = (await alice.post("/api/sync")).body.fetch.jobId;
  ids.flag = (await db.query<{ id: number }>(
    `INSERT INTO calendar_flags (user_id, kind, commitment_id, details, dedupe_key)
     VALUES (1, 'conflict', $1, '{}', 'k') RETURNING id`, [ids.commitment],
  )).rows[0]!.id;
  ids.notification = (await db.query<{ id: number }>(
    "INSERT INTO notifications (user_id, kind, title) VALUES (1, 'system', 'For Alice') RETURNING id",
  )).rows[0]!.id;

  bob = await signedInAs(t.app, db, "bob@example.com");
});

async function aliceStillHas() {
  const row = async (sql: string, id: number) => (await db.query(sql, [id])).rows[0];
  expect(await row("SELECT subject, is_starred, deleted_at FROM raw_emails WHERE id = $1", ids.email!)).toMatchObject({
    subject: "Alice's contract", is_starred: false, deleted_at: null,
  });
  expect(await row("SELECT status FROM commitments WHERE id = $1", ids.commitment!)).toMatchObject({ status: "pending" });
  expect(await row("SELECT name FROM tags WHERE id = $1", ids.tag!)).toMatchObject({ name: "alice-only" });
  expect(await row("SELECT status FROM calendar_flags WHERE id = $1", ids.flag!)).toMatchObject({ status: "open" });
  expect(await row("SELECT read_at FROM notifications WHERE id = $1", ids.notification!)).toMatchObject({ read_at: null });
}

describe("another account sees none of it", () => {
  it.each([
    ["/api/emails", (b: any) => b.items],
    ["/api/commitments", (b: any) => b.items],
    [`/api/calendar?from=${day(0)}&to=${day(30)}`, (b: any) => b.items],
    ["/api/calendar/flags", (b: any) => b.items],
    ["/api/tags", (b: any) => b],
    ["/api/views", (b: any) => b],
    ["/api/contacts", (b: any) => b],
    ["/api/notifications", (b: any) => b.items],
    ["/api/jobs", (b: any) => b.items],
    ["/api/relationships", (b: any) => b.people],
  ])("%s", async (path, pick) => {
    const theirs = await alice.get(path);
    const mine = await bob.get(path);
    expect(theirs.status).toBe(200);
    expect(pick(theirs.body).length).toBeGreaterThan(0);   // the test would prove nothing otherwise
    expect(mine.status).toBe(200);
    expect(pick(mine.body)).toEqual([]);
  });

  it("not by id either", async () => {
    for (const path of [`/api/emails/${ids.email}`, `/api/commitments/${ids.commitment}`, `/api/jobs/${ids.job}`]) {
      expect((await bob.get(path)).status, path).toBe(404);
    }
  });

  it("not in the activity timeline, the analytics or the export", async () => {
    expect((await bob.get("/api/activity")).body.items.every((e: { message: string }) => !e.message.includes("Alice"))).toBe(true);
    const analytics = (await bob.get("/api/analytics")).body;
    expect(analytics.totals?.emails ?? 0).toBe(0);
    const exported = (await bob.get("/api/privacy/export")).body.data;
    for (const table of ["raw_emails", "commitments", "tags", "saved_views", "vip_contacts"]) {
      expect(exported[table], table).toEqual([]);
    }
  });

  it("has its own settings", async () => {
    expect((await bob.get("/api/settings")).body.appearance.theme).toBe("system");
    await bob.put("/api/settings/appearance", { theme: "light", density: "comfortable", reducedMotion: false });
    expect((await alice.get("/api/settings")).body.appearance.theme).toBe("dark");
  });
});

describe("another account cannot change any of it", () => {
  it("one at a time", async () => {
    const attempts = [
      bob.patch(`/api/emails/${ids.email}`, { isStarred: true }),
      bob.patch(`/api/commitments/${ids.commitment}`, { status: "dismissed" }),
      bob.patch(`/api/tags/${ids.tag}`, { name: "stolen" }),
      bob.del(`/api/tags/${ids.tag}`),
      bob.del(`/api/views/${ids.view}`),
      bob.del(`/api/contacts/${ids.contact}`),
      bob.post(`/api/jobs/${ids.job}/retry`),
      bob.post(`/api/calendar/flags/${ids.flag}/dismiss`),
      bob.post(`/api/notifications/${ids.notification}/read`),
    ];
    const statuses = (await Promise.all(attempts)).map((res) => res.status);
    expect(statuses).toEqual(attempts.map(() => 404));
    await aliceStillHas();
  });

  it("in bulk", async () => {
    expect((await bob.post("/api/emails/bulk", { ids: [ids.email], action: "delete" })).body).toMatchObject({ updated: 0 });
    expect((await bob.post("/api/commitments/bulk", { ids: [ids.commitment], action: "dismiss" })).body).toMatchObject({ updated: 0 });
    await bob.post("/api/notifications/read-all");
    await aliceStillHas();
  });

  it("not even by attaching someone else's tag to their own email", async () => {
    const own = (await db.query<{ id: number }>(
      "INSERT INTO raw_emails (user_id, message_id) VALUES (2, 'bob@x') RETURNING id",
    )).rows[0]!.id;
    const res = await bob.post("/api/emails/bulk", { ids: [own], action: "addTag", tagId: ids.tag });
    expect(res.status).toBe(404);
    expect((await db.query("SELECT count(*)::int AS n FROM email_tags")).rows[0]!.n).toBe(0);
  });

  it("and purging deletes only their own", async () => {
    await bob.post("/api/privacy/purge", { scope: "everything", confirm: "DELETE" });
    await aliceStillHas();
  });
});

describe("the deployment's own views", () => {
  it("are for admins", async () => {
    expect((await bob.get("/api/system/metrics")).status).toBe(403);
    expect((await bob.get("/api/system/events")).status).toBe(403);
    expect((await alice.get("/api/system/metrics")).status).toBe(200);
  });

  it("show everyone whether things work, but only admins how", async () => {
    const forBob = (await bob.get("/api/system/health")).body.components.database;
    const forAlice = (await alice.get("/api/system/health")).body.components.database;
    expect(Object.keys(forBob)).toEqual(["state"]);
    expect(forAlice.detail).toMatch(/PostgreSQL/);
  });
});

describe("the database itself", () => {
  it("shows a query with no user set nothing at all", async () => {
    const { rows } = await tenantDb(db).query("SELECT count(*)::int AS n FROM raw_emails");
    expect(rows[0]!.n).toBe(0);
  });

  it("refuses a row written for someone else", async () => {
    const scoped = tenantDb(db);
    const { runAs } = await import("../src/db/tenant");
    await expect(
      runAs(2, () => scoped.query("INSERT INTO tags (user_id, name) VALUES (1, 'planted')")),
    ).rejects.toThrow(/row-level security/);
  });
});
