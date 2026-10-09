/** v2 phase 8: settling the worker's calendar flags, and the days ahead. */
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";

import type { Db } from "../src/db/types";
import { buildApp, createTestDb, eventTypes, insertCommitment, insertEmail, resetDb, signedIn } from "./helpers";

let db: Db;
let session: Awaited<ReturnType<typeof signedIn>>;

beforeAll(async () => {
  ({ db } = await createTestDb());
});
afterAll(async () => db.close());
beforeEach(async () => {
  await resetDb(db);
  session = await signedIn(buildApp(db).app);
});

const day = (offset: number) => new Date(Date.now() + offset * 86_400_000).toISOString().slice(0, 10);

async function commitment(subject: string, deadline: string, fields: Record<string, unknown> = {}) {
  return insertCommitment(db, await insertEmail(db, { subject }), { subject, deadline, ...fields });
}

async function flag(fields: Record<string, unknown>) {
  const row: Record<string, unknown> = { kind: "duplicate", status: "open", details: {}, ...fields };
  const { rows } = await db.query<{ id: number }>(
    `INSERT INTO calendar_flags (user_id, kind, commitment_id, other_commitment_id, external_event_id, details, status, dedupe_key)
     VALUES ((SELECT min(id) FROM users), $1, $2, $3, $4, $5::jsonb, $6, $7) RETURNING id`,
    [row.kind, row.commitment_id, row.other_commitment_id ?? null, row.external_event_id ?? null,
     JSON.stringify(row.details), row.status, `${row.kind}:${Math.random()}`],
  );
  return rows[0]!.id;
}

async function status(id: number) {
  return (await db.query<{ status: string }>("SELECT status FROM commitments WHERE id = $1", [id])).rows[0]!.status;
}

async function queued() {
  return (await db.query<{ type: string }>("SELECT type FROM jobs ORDER BY id")).rows.map((r) => r.type);
}

describe("calendar flags", () => {
  it("lists open flags with both sides, soonest first", async () => {
    const later = await commitment("Vendor call", `${day(5)} 10:30:00`, { type: "meeting" });
    const earlier = await commitment("Budget sync", `${day(2)} 10:00:00`, { type: "meeting" });
    const lunch = await commitment("Board prep", `${day(2)} 12:00:00`, { type: "meeting" });
    await flag({ kind: "conflict", commitment_id: later, other_commitment_id: earlier, details: {
      overlap: { start: `${day(5)}T10:30:00`, end: `${day(5)}T11:00:00` },
      suggestions: [{ start: `${day(5)}T09:00:00`, end: `${day(5)}T10:00:00` }],
    } });
    await flag({ kind: "duplicate", commitment_id: lunch, external_event_id: "evt1", details: {
      similarity: 0.67,
      items: [{ title: "Board prep", start: "x", end: "y", allDay: false },
              { title: "Board prep (invite)", start: `${day(2)}T12:00:00`, end: `${day(2)}T13:00:00`, allDay: false, link: "https://calendar.google.com/x" }],
    } });
    await flag({ commitment_id: earlier, other_commitment_id: later, status: "dismissed" });

    const { items } = (await session.get("/api/calendar/flags")).body;

    expect(items.map((f: { commitment: { subject: string } }) => f.commitment.subject)).toEqual(["Board prep", "Vendor call"]);
    expect(items[0]).toMatchObject({
      kind: "duplicate", similarity: 0.67, other: null,
      external: { id: "evt1", title: "Board prep (invite)", link: "https://calendar.google.com/x" },
    });
    expect(items[1]).toMatchObject({ kind: "conflict", other: { id: earlier }, suggestions: [{ start: `${day(5)}T09:00:00` }] });
    expect((await session.get("/api/calendar/flags").query({ status: "all" })).body.items).toHaveLength(3);
  });

  it("keeping one copy of a duplicate dismisses the other and republishes", async () => {
    const original = await commitment("Q3 budget report", `${day(3)} 12:00:00`);
    const forwarded = await commitment("Send the Q3 budget report", `${day(3)} 17:00:00`);
    const id = await flag({ commitment_id: forwarded, other_commitment_id: original });

    const res = await session.post(`/api/calendar/flags/${id}/resolve`, { keep: original });

    expect(res.status).toBe(200);
    expect(res.body.status).toBe("resolved");
    expect(await status(forwarded)).toBe("dismissed");
    expect(await status(original)).toBe("pending");
    expect(await queued()).toEqual(["publish_calendar"]);
    expect(await eventTypes(db)).toEqual(expect.arrayContaining(["commitment.updated", "calendar.flag_resolved"]));
  });

  it("keeping the event already on your calendar dismisses ours", async () => {
    const ours = await commitment("Design review", `${day(1)} 14:00:00`, { type: "meeting" });
    const id = await flag({ commitment_id: ours, external_event_id: "invite-1" });
    await session.post(`/api/calendar/flags/${id}/resolve`, { keep: "external" });
    expect(await status(ours)).toBe("dismissed");
  });

  it("refuses to settle a clash by keeping one side, or to keep something unrelated", async () => {
    const a = await commitment("Budget sync", `${day(1)} 10:00:00`, { type: "meeting" });
    const b = await commitment("Vendor call", `${day(1)} 10:30:00`, { type: "meeting" });
    const clash = await flag({ kind: "conflict", commitment_id: b, other_commitment_id: a });
    const dup = await flag({ commitment_id: b, other_commitment_id: a });

    expect((await session.post(`/api/calendar/flags/${clash}/resolve`, { keep: a })).status).toBe(400);
    expect((await session.post(`/api/calendar/flags/${dup}/resolve`, { keep: 999 })).status).toBe(400);
    expect((await session.post(`/api/calendar/flags/${dup}/resolve`, { keep: "external" })).status).toBe(400);
    expect(await status(a)).toBe("pending");
  });

  it("dismissing a flag settles it for good, and a settled flag cannot be settled again", async () => {
    const a = await commitment("Budget sync", `${day(1)} 10:00:00`, { type: "meeting" });
    const id = await flag({ kind: "conflict", commitment_id: a, external_event_id: "dentist" });

    expect((await session.post(`/api/calendar/flags/${id}/dismiss`)).body.status).toBe("dismissed");
    expect((await session.post(`/api/calendar/flags/${id}/dismiss`)).status).toBe(409);
    expect((await session.post("/api/calendar/flags/12345/dismiss")).status).toBe(404);
    expect(await eventTypes(db)).toContain("calendar.flag_resolved");
  });

  it("asks the worker to look again, once", async () => {
    const first = await session.post("/api/calendar/scan");
    const second = await session.post("/api/calendar/scan");
    expect([first.status, first.body.queued, second.body.queued]).toEqual([202, true, false]);
    expect(await queued()).toEqual(["scan_calendar"]);
  });

  it("re-checks when the working hours change", async () => {
    await session.put("/api/settings/calendar", { workingHours: { start: "08:00", end: "16:00", days: [1, 2, 3, 4, 5] } });
    expect(await queued()).toEqual(["scan_calendar"]);
  });
});

describe("calendar insights", () => {
  it("counts the days ahead, the busiest one, and what falls outside working hours", async () => {
    await session.put("/api/settings/calendar", { workingHours: { start: "09:00", end: "18:00", days: [0, 1, 2, 3, 4, 5, 6] } });
    await commitment("Send the report", `${day(1)} 10:00:00`);
    await commitment("Review the contract", `${day(1)} 11:00:00`);
    const sync = await commitment("Budget sync", `${day(1)} 12:00:00`, { type: "meeting" });
    const late = await commitment("Call with Tokyo", `${day(2)} 20:30:00`, { type: "meeting" });
    await commitment("Newsletter webinar", `${day(1)} 13:00:00`, { vip_tier: "SKIP" });   // not for the calendar
    await commitment("Renew passport", `${day(3)} 00:00:00`);                               // all day: no hours
    await flag({ kind: "conflict", commitment_id: sync, other_commitment_id: late });

    const insights = (await session.get("/api/calendar/insights")).body;

    expect(insights.from).toBe(day(0));
    expect(insights.days).toHaveLength(14);
    expect(insights.days[1]).toEqual({ date: day(1), deadlines: 2, meetings: 1 });
    expect(insights.busiestDay).toEqual({ date: day(1), count: 3 });
    expect(insights.outsideHours).toEqual([{ id: late, type: "meeting", subject: "Call with Tokyo", deadline: `${day(2)}T20:30:00` }]);
    expect(insights.openFlags).toEqual({ conflicts: 1, duplicates: 0 });
  });

  it("has no busiest day when nothing piles up", async () => {
    await commitment("Send the report", `${day(1)} 10:00:00`);
    expect((await session.get("/api/calendar/insights").query({ days: 7 })).body).toMatchObject({ busiestDay: null });
  });
});
