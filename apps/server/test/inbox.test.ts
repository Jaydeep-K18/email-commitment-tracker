import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";

import type { Db } from "../src/db/types";
import {
  buildApp,
  createTestDb,
  eventTypes,
  insertCommitment,
  insertEmail,
  resetDb,
  signedIn,
  type TestApp,
} from "./helpers";

let db: Db;
let t: TestApp;
let session: Awaited<ReturnType<typeof signedIn>>;

beforeAll(async () => {
  ({ db } = await createTestDb());
});
afterAll(async () => db.close());
beforeEach(async () => {
  await resetDb(db);
  t = buildApp(db);
  session = await signedIn(t.app);
});

describe("listing", () => {
  it("shows the inbox newest first, without archived or deleted mail", async () => {
    const older = await insertEmail(db, { received_at: "2026-09-01 08:00:00" });
    const newer = await insertEmail(db, { received_at: "2026-09-02 08:00:00" });
    await insertEmail(db, { archived_at: "2026-09-03 00:00:00" });
    await insertEmail(db, { deleted_at: "2026-09-03 00:00:00" });

    const res = await session.get("/api/emails");
    expect(res.status).toBe(200);
    expect(res.body.items.map((e: { id: number }) => e.id)).toEqual([newer, older]);
    expect(res.body.total).toBe(2);
  });

  it("returns instants as UTC", async () => {
    await insertEmail(db, { received_at: "2026-09-01 08:30:00" });
    const res = await session.get("/api/emails");
    expect(res.body.items[0].receivedAt).toBe("2026-09-01T08:30:00Z");
  });

  it("finds email by meaning-bearing words through full-text search, best match first", async () => {
    await insertEmail(db, { subject: "Lunch on Friday?", body_text: "Want to grab food" });
    const strong = await insertEmail(db, { subject: "Quarterly report", body_text: "The quarterly report numbers" });
    const weak = await insertEmail(db, { subject: "Misc", body_text: "also the report is attached" });

    const res = await session.get("/api/emails").query({ q: "report", sort: "relevance" });
    expect(res.body.items.map((e: { id: number }) => e.id)).toEqual([strong, weak]);
  });

  it("treats search input as text, not SQL", async () => {
    await insertEmail(db);
    const res = await session.get("/api/emails").query({ q: "'; DROP TABLE raw_emails; --", sender: "%_\\" });
    expect(res.status).toBe(200);
    expect((await db.query("SELECT count(*)::int AS n FROM raw_emails")).rows[0]!.n).toBe(1);
  });

  it("filters by category, unread, starred, tier and sender", async () => {
    const match = await insertEmail(db, {
      category: "action_required", is_read: false, is_starred: true, vip_tier: "CRITICAL", sender_email: "boss@acme.com",
    });
    await insertEmail(db, { category: "action_required", is_read: true, is_starred: true });
    await insertEmail(db, { category: "update", is_read: false, is_starred: true });

    const res = await session.get("/api/emails").query({
      category: "action_required", unread: "true", starred: "true", tier: "CRITICAL", sender: "acme",
    });
    expect(res.body.items.map((e: { id: number }) => e.id)).toEqual([match]);
  });

  it("counts each category for the tab badges, ignoring the category filter itself", async () => {
    await insertEmail(db, { category: "action_required", is_read: false });
    await insertEmail(db, { category: "update", is_read: true });
    await insertEmail(db, { category: "update", is_read: true });

    const res = await session.get("/api/emails").query({ category: "update" });
    expect(res.body.total).toBe(2);
    expect(res.body.counts.byCategory).toMatchObject({ action_required: 1, update: 2, meeting: 0 });
    expect(res.body.counts.unread).toBe(1);
  });

  it("sorts by priority: action first, then meetings, then by sender tier", async () => {
    const update = await insertEmail(db, { category: "update" });
    const action = await insertEmail(db, { category: "action_required" });
    const meeting = await insertEmail(db, { category: "meeting" });

    const res = await session.get("/api/emails").query({ sort: "priority" });
    expect(res.body.items.map((e: { id: number }) => e.id)).toEqual([action, meeting, update]);
  });

  it("pages", async () => {
    for (let i = 0; i < 5; i++) await insertEmail(db, { received_at: `2026-09-0${i + 1} 08:00:00` });
    const res = await session.get("/api/emails").query({ page: 2, pageSize: 2 });
    expect(res.body.items).toHaveLength(2);
    expect(res.body.total).toBe(5);
  });

  it("rejects an invalid filter instead of ignoring it", async () => {
    const res = await session.get("/api/emails").query({ category: "urgent-ish" });
    expect(res.status).toBe(400);
  });
});

describe("one email", () => {
  it("includes its commitments, its timeline and how fast you replied", async () => {
    const id = await insertEmail(db, { message_id: "orig@x.com", received_at: "2026-09-01 09:00:00" });
    await insertCommitment(db, id);
    await db.query(
      "INSERT INTO events (user_id, type, message, correlation_id) VALUES ((SELECT min(id) FROM users), 'email.received', 'Email from Priya', $1)",
      [`email:${id}`],
    );
    await db.query(
      "INSERT INTO sent_messages (user_id, message_id, in_reply_to, sent_at) VALUES ((SELECT min(id) FROM users), 'reply@me', 'orig@x.com', '2026-09-01 10:30:00')",
    );

    const res = await session.get(`/api/emails/${id}`);
    expect(res.body.commitments).toHaveLength(1);
    expect(res.body.commitments[0].deadline).toBe("2026-09-05T17:00:00");   // wall clock, no Z
    expect(res.body.commitments[0].decision.shouldSync).toBe(true);
    expect(res.body.timeline.map((e: { type: string }) => e.type)).toEqual(["email.received"]);
    expect(res.body.responseMinutes).toBe(90);
  });

  it("404s for an email that does not exist", async () => {
    expect((await session.get("/api/emails/999")).status).toBe(404);
  });
});

describe("acting on email", () => {
  it("archives without touching the real mailbox, and records it", async () => {
    const id = await insertEmail(db);
    const res = await session.patch(`/api/emails/${id}`, { archived: true });

    expect(res.body.archivedAt).toMatch(/Z$/);
    expect(await eventTypes(db)).toContain("email.updated");
    expect((await session.get("/api/emails")).body.total).toBe(0);
    expect((await session.get("/api/emails").query({ folder: "archived" })).body.total).toBe(1);
  });

  it("opening an email marks it read without cluttering the audit trail", async () => {
    const id = await insertEmail(db, { is_read: false });
    await session.patch(`/api/emails/${id}`, { isRead: true });
    expect(await eventTypes(db)).not.toContain("email.updated");
  });

  it("a category you choose is yours, and handing it back asks the worker to re-sort", async () => {
    const id = await insertEmail(db, { category: "update", category_source: "rule" });

    const chosen = await session.patch(`/api/emails/${id}`, { category: "important" });
    expect(chosen.body.categorySource).toBe("user");

    await session.patch(`/api/emails/${id}`, { category: null });
    const { rows } = await db.query("SELECT type, payload FROM jobs");
    expect(rows).toEqual([{ type: "classify_emails", payload: { email_ids: [id] } }]);
  });

  it("refuses an empty or unknown change", async () => {
    const id = await insertEmail(db);
    expect((await session.patch(`/api/emails/${id}`, {})).status).toBe(400);
    expect((await session.patch(`/api/emails/${id}`, { subject: "hacked" })).status).toBe(400);
  });

  it("applies bulk actions in one transaction and one audit entry", async () => {
    const ids = [await insertEmail(db), await insertEmail(db), await insertEmail(db)];
    const res = await session.post("/api/emails/bulk", { action: "archive", ids });

    expect(res.body.updated).toBe(3);
    const audit = await db.query("SELECT message FROM events WHERE type = 'email.bulk_updated'");
    expect(audit.rows.map((r) => r.message)).toEqual(["Archived: 3 emails"]);
  });

  it("tags in bulk, once per email however often it is asked", async () => {
    const ids = [await insertEmail(db), await insertEmail(db)];
    const tag = await session.post("/api/tags", { name: "Clients", color: "teal" });

    await session.post("/api/emails/bulk", { action: "addTag", ids, tagId: tag.body.id });
    const again = await session.post("/api/emails/bulk", { action: "addTag", ids, tagId: tag.body.id });
    expect(again.body.updated).toBe(0);

    const tagged = await session.get("/api/emails").query({ tag: tag.body.id });
    expect(tagged.body.total).toBe(2);
    expect(tagged.body.items[0].tags).toEqual([{ id: tag.body.id, name: "Clients", color: "teal" }]);
  });

  it("caps a bulk request at 500 emails", async () => {
    const ids = Array.from({ length: 501 }, (_, i) => i + 1);
    expect((await session.post("/api/emails/bulk", { action: "archive", ids })).status).toBe(400);
  });
});

describe("tags and saved views", () => {
  it("refuses a duplicate tag name with a clear message", async () => {
    await session.post("/api/tags", { name: "Clients" });
    const duplicate = await session.post("/api/tags", { name: "Clients" });
    expect(duplicate.status).toBe(409);
  });

  it("saves a filter and sort to come back to", async () => {
    const view = await session.post("/api/views", {
      name: "Urgent from VIPs",
      filters: { folder: "inbox", category: ["action_required"], tier: ["CRITICAL"] },
      sort: { sort: "priority", dir: "desc" },
      isPinned: true,
    });
    expect(view.status).toBe(201);
    const listed = await session.get("/api/views");
    expect(listed.body[0]).toMatchObject({ name: "Urgent from VIPs", isPinned: true });
  });
});
