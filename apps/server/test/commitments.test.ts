import { readFileSync } from "node:fs";
import { join } from "node:path";

import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";

import type { Db } from "../src/db/types";
import { findRepoRoot } from "../src/env";
import { AWAITING_APPROVAL_SQL, SHOULD_SYNC_SQL } from "../src/modules/commitments";
import { buildApp, createTestDb, insertCommitment, insertEmail, resetDb, signedIn, type TestApp } from "./helpers";

interface PolicyCase {
  input: { type: string; hasDeadline: boolean; status: string; vipTier: string | null; manuallyAdded: boolean; syncApproved: boolean };
  output: { shouldSync: boolean; awaitingApproval: boolean };
}

const cases: PolicyCase[] = JSON.parse(
  readFileSync(join(findRepoRoot(), "packages", "shared", "contracts", "sync-decisions.json"), "utf8"),
);

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

describe("the SQL mirror of the calendar policy", () => {
  it(`selects exactly what Python's decide() selects, across all ${cases.length} cases`, async () => {
    const emailId = await insertEmail(db);
    const idToCase = new Map<number, PolicyCase>();
    for (const c of cases) {
      const id = await insertCommitment(db, emailId, {
        type: c.input.type,
        deadline: c.input.hasDeadline ? "2026-09-01 17:00:00" : null,
        status: c.input.status,
        vip_tier: c.input.vipTier,
        manually_added: c.input.manuallyAdded,
        sync_approved: c.input.syncApproved,
      });
      idToCase.set(id, c);
    }

    const selected = async (predicate: string) =>
      new Set((await db.query<{ id: number }>(`SELECT c.id FROM commitments c WHERE ${predicate}`)).rows.map((r) => r.id));
    const shouldSync = await selected(SHOULD_SYNC_SQL);
    const awaiting = await selected(AWAITING_APPROVAL_SQL);

    const mismatches = [...idToCase].filter(
      ([id, c]) => shouldSync.has(id) !== c.output.shouldSync || awaiting.has(id) !== c.output.awaitingApproval,
    );
    expect(mismatches.map(([, c]) => c.input)).toEqual([]);
  });
});

describe("commitment views", () => {
  it("the review queue holds what is waiting for your approval", async () => {
    const emailId = await insertEmail(db);
    const waiting = await insertCommitment(db, emailId, { vip_tier: "MONITOR" });
    await insertCommitment(db, emailId, { vip_tier: "CRITICAL" });

    const res = await session.get("/api/commitments").query({ view: "review" });
    expect(res.body.items.map((c: { id: number }) => c.id)).toEqual([waiting]);
    expect(res.body.items[0].decision.reason).toBe("MONITOR tier waiting for your approval");
  });

  it("approving queues a calendar publish and records why", async () => {
    const emailId = await insertEmail(db);
    const id = await insertCommitment(db, emailId, { vip_tier: "MONITOR" });

    const res = await session.patch(`/api/commitments/${id}`, { approved: true });
    expect(res.body.decision.shouldSync).toBe(true);

    const jobs = await db.query("SELECT type FROM jobs");
    expect(jobs.rows.map((r) => r.type)).toEqual(["publish_calendar"]);
    const audit = await db.query("SELECT message, correlation_id FROM events WHERE type = 'commitment.updated'");
    expect(audit.rows[0]).toEqual({ message: "Send the quarterly report: approved for the calendar", correlation_id: `email:${emailId}` });
  });

  it("two quick approvals queue one publish, not two", async () => {
    const emailId = await insertEmail(db);
    const a = await insertCommitment(db, emailId, { vip_tier: "MONITOR" });
    const b = await insertCommitment(db, emailId, { vip_tier: "MONITOR" });
    await session.patch(`/api/commitments/${a}`, { approved: true });
    await session.patch(`/api/commitments/${b}`, { approved: true });

    const jobs = await db.query("SELECT count(*)::int AS n FROM jobs WHERE type = 'publish_calendar'");
    expect(jobs.rows[0]!.n).toBe(1);
  });

  it("a superseded commitment cannot be revived from the API", async () => {
    const emailId = await insertEmail(db);
    const id = await insertCommitment(db, emailId, { status: "superseded" });
    expect((await session.patch(`/api/commitments/${id}`, { status: "pending" })).status).toBe(404);
  });

  it("the calendar range returns wall-clock deadlines and flags all-day ones", async () => {
    const emailId = await insertEmail(db);
    await insertCommitment(db, emailId, { deadline: "2026-09-10 00:00:00" });
    await insertCommitment(db, emailId, { deadline: "2026-09-11 14:30:00" });
    await insertCommitment(db, emailId, { deadline: "2026-10-01 09:00:00" });   // outside

    const res = await session.get("/api/calendar").query({ from: "2026-09-01", to: "2026-09-30" });
    expect(res.body.items.map((c: { deadline: string; allDay: boolean }) => [c.deadline, c.allDay])).toEqual([
      ["2026-09-10T00:00:00", true],
      ["2026-09-11T14:30:00", false],
    ]);
  });
});
