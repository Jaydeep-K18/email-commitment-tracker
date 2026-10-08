/**
 * Commitments, the review queue, and the calendar view of them.
 *
 * The "review" and "calendar" views select rows with SQL that mirrors the tier
 * policy (`decide()`), because filtering in JavaScript after paging would make
 * pages come back short. The mirror is tested against every case in
 * contracts/sync-decisions.json, so it cannot quietly drift from the policy.
 */
import {
  calendarRangeSchema,
  commitmentBulkSchema,
  commitmentPatchSchema,
  commitmentQuerySchema,
  idParam,
  type Commitment,
  type CommitmentQuery,
  type Page,
} from "@commitmail/shared";
import { Router } from "express";

import { QueryParts, likeLiteral } from "../db/sql";
import type { Queryable } from "../db/types";
import type { Deps } from "../deps";
import { emailCorrelation, recordEvent } from "../events/record";
import { notFound } from "../http/errors";
import { parse } from "../http/validate";
import { COMMITMENT_COLUMNS, toCommitment } from "./mappers";

const TIER = "upper(coalesce(c.vip_tier, ''))";

/** decide(): everything that rules a commitment off the calendar outright. */
const ELIGIBLE = `(c.type <> 'question_pending' AND c.deadline IS NOT NULL
  AND c.status NOT IN ('superseded', 'dismissed', 'fulfilled')
  AND NOT (${TIER} = 'SKIP' AND NOT c.manually_added))`;

/** decide().shouldSync */
export const SHOULD_SYNC_SQL = `(${ELIGIBLE} AND (${TIER} IN ('CRITICAL', 'IMPORTANT') OR c.sync_approved))`;

/** decide().awaitingApproval */
export const AWAITING_APPROVAL_SQL = `(${ELIGIBLE} AND ${TIER} NOT IN ('CRITICAL', 'IMPORTANT') AND NOT c.sync_approved)`;

/** The user's local "now", for deadlines, which are wall-clock times. */
async function localNow(q: Queryable): Promise<string> {
  const { rows } = await q.query<{ zone: string | null }>(
    "SELECT value->>'timeZone' AS zone FROM settings WHERE section = 'calendar'",
  );
  const zone = rows[0]?.zone ?? "UTC";
  const result = await q.query<{ now: string }>("SELECT (now() AT TIME ZONE $1)::text AS now", [zone]);
  return result.rows[0]!.now;
}

export async function listCommitments(q: Queryable, query: CommitmentQuery): Promise<Page<Commitment>> {
  const parts = new QueryParts();
  switch (query.view) {
    case "review":
      parts.where(AWAITING_APPROVAL_SQL);
      break;
    case "calendar":
      parts.where(SHOULD_SYNC_SQL);
      break;
    case "upcoming":
      parts.where(`c.status IN ('pending', 'overdue') AND c.deadline >= ${parts.param(await localNow(q))}::timestamp`);
      break;
    case "overdue":
      parts.where(`c.status IN ('pending', 'overdue') AND c.deadline < ${parts.param(await localNow(q))}::timestamp`);
      break;
    case "all":
      if (!query.status?.length) parts.where("c.status <> 'superseded'");
      break;
  }
  if (query.status?.length) parts.where(`c.status = ANY(${parts.param(query.status)}::text[])`);
  if (query.tier?.length) parts.where(`c.vip_tier = ANY(${parts.param(query.tier)}::text[])`);
  if (query.type?.length) parts.where(`c.type = ANY(${parts.param(query.type)}::text[])`);
  if (query.q) {
    const pattern = parts.param(`%${likeLiteral(query.q)}%`);
    parts.where(`(c.subject ILIKE ${pattern} OR c.counterparty_name ILIKE ${pattern} OR e.subject ILIKE ${pattern})`);
  }

  const order = query.view === "all" ? "c.created_at DESC, c.id DESC" : "c.deadline ASC NULLS LAST, c.id";
  const counted = parts.clone();
  const limit = parts.param(query.pageSize);
  const offset = parts.param((query.page - 1) * query.pageSize);
  const from = "FROM commitments c JOIN raw_emails e ON e.id = c.email_id";

  const [items, total] = await Promise.all([
    q.query(`SELECT ${COMMITMENT_COLUMNS} ${from} ${parts.whereSql} ORDER BY ${order} LIMIT ${limit} OFFSET ${offset}`, parts.params),
    q.query<{ total: number }>(`SELECT count(*) AS total ${from} ${counted.whereSql}`, counted.params),
  ]);
  return {
    items: items.rows.map(toCommitment),
    total: total.rows[0]?.total ?? 0,
    page: query.page,
    pageSize: query.pageSize,
  };
}

async function getCommitment(q: Queryable, id: number): Promise<Commitment> {
  const { rows } = await q.query(
    `SELECT ${COMMITMENT_COLUMNS} FROM commitments c JOIN raw_emails e ON e.id = c.email_id WHERE c.id = $1`,
    [id],
  );
  if (!rows[0]) throw notFound("Commitment");
  return toCommitment(rows[0]);
}

const BULK_SQL: Record<string, { set: string; label: string }> = {
  approve: { set: "sync_approved = true", label: "Approved for the calendar" },
  unapprove: { set: "sync_approved = false", label: "Approval withdrawn" },
  dismiss: { set: "status = 'dismissed'", label: "Dismissed" },
  fulfil: { set: "status = 'fulfilled'", label: "Marked done" },
  reopen: { set: "status = 'pending'", label: "Reopened" },
};

export function commitmentsRouter(deps: Deps): Router {
  const router = Router();

  router.get("/", async (req, res) => {
    res.json(await listCommitments(deps.db, parse(commitmentQuerySchema, req.query)));
  });

  router.get("/:id", async (req, res) => {
    res.json(await getCommitment(deps.db, parse(idParam, req.params).id));
  });

  router.patch("/:id", async (req, res) => {
    const { id } = parse(idParam, req.params);
    const patch = parse(commitmentPatchSchema, req.body);
    await deps.jobs.withJobs(async (q, jobs) => {
      const { rows } = await q.query<{ email_id: number; subject: string }>(
        `UPDATE commitments
            SET status = COALESCE($2, status), sync_approved = COALESCE($3, sync_approved)
          WHERE id = $1 AND status <> 'superseded'
          RETURNING email_id, subject`,
        [id, patch.status ?? null, patch.approved ?? null],
      );
      const row = rows[0];
      if (!row) throw notFound("Commitment");
      const what = [
        patch.status === "dismissed" && "dismissed",
        patch.status === "fulfilled" && "marked done",
        patch.status === "pending" && "reopened",
        patch.approved === true && "approved for the calendar",
        patch.approved === false && "approval withdrawn",
      ].filter(Boolean).join(", ");
      await recordEvent(q, {
        type: "commitment.updated",
        message: `${row.subject}: ${what}`,
        entityType: "commitment",
        entityId: id,
        correlationId: emailCorrelation(row.email_id),
        payload: { changes: patch },
      });
      // Anything that can change what belongs on the calendar republishes it.
      await jobs.enqueueUnlessActive("publish_calendar");
    });
    res.json(await getCommitment(deps.db, id));
  });

  router.post("/bulk", async (req, res) => {
    const body = parse(commitmentBulkSchema, req.body);
    const { set, label } = BULK_SQL[body.action]!;
    const updated = await deps.jobs.withJobs(async (q, jobs) => {
      const { rowCount } = await q.query(
        `UPDATE commitments SET ${set} WHERE id = ANY($1::int[]) AND status <> 'superseded'`,
        [body.ids],
      );
      await recordEvent(q, {
        type: "commitment.updated",
        message: `${label}: ${rowCount} commitment${rowCount === 1 ? "" : "s"}`,
        entityType: "commitment",
        payload: { action: body.action, ids: body.ids.slice(0, 100), count: rowCount },
      });
      await jobs.enqueueUnlessActive("publish_calendar");
      return rowCount;
    });
    res.json({ updated });
  });

  return router;
}

export function calendarRouter(deps: Deps): Router {
  const router = Router();

  /** Every dated commitment in a date range, with whether it is on the calendar. */
  router.get("/", async (req, res) => {
    const range = parse(calendarRangeSchema, req.query);
    const { rows } = await deps.db.query(
      `SELECT ${COMMITMENT_COLUMNS} FROM commitments c JOIN raw_emails e ON e.id = c.email_id
        WHERE c.deadline >= $1::date AND c.deadline < $2::date + 1 AND c.status <> 'superseded'
        ORDER BY c.deadline, c.id`,
      [range.from, range.to],
    );
    res.json({ items: rows.map(toCommitment) });
  });

  return router;
}

/**
 * Who you are entangled with, and which way it runs — the data behind the
 * relationship graph. A person is keyed by address (falling back to name) so spellings merge, and
 * an obligation runs towards whoever owes: deadlines on you and meetings are
 * yours, deadlines from others and pending questions are theirs.
 */
type Direction = "you_owe" | "they_owe";
interface Person {
  key: string;
  label: string;
  tiers: Set<string>;
  youOwe: number;
  theyOwe: number;
  commitments: Array<{
    id: number; emailId: number; type: string; subject: string;
    deadline: string | null; status: string; direction: Direction;
  }>;
}

export function relationshipsRouter(deps: Deps): Router {
  const router = Router();

  router.get("/", async (_req, res) => {
    const { rows } = await deps.db.query(
      `SELECT c.id, c.type, c.subject, c.deadline, c.status, c.vip_tier, c.email_id,
              lower(trim(coalesce(c.counterparty_email, c.counterparty_name, 'unknown'))) AS person_key,
              trim(coalesce(c.counterparty_name, c.counterparty_email, 'Unknown')) AS person_label
         FROM commitments c
        WHERE c.status IN ('pending', 'overdue')
        ORDER BY c.deadline NULLS LAST, c.id`,
    );
    const TIER_ORDER = ["CRITICAL", "IMPORTANT", "MONITOR", "SKIP"];
    const people = new Map<string, Person>();
    for (const row of rows) {
      const direction: Direction = ["deadline_from_others", "question_pending"].includes(row.type) ? "they_owe" : "you_owe";
      const person: Person = people.get(row.person_key) ?? {
        key: row.person_key, label: row.person_label, tiers: new Set<string>(), youOwe: 0, theyOwe: 0, commitments: [],
      };
      if (row.vip_tier) person.tiers.add(row.vip_tier);
      if (direction === "you_owe") person.youOwe += 1;
      else person.theyOwe += 1;
      person.commitments.push({
        id: row.id, emailId: row.email_id, type: row.type, subject: row.subject,
        deadline: row.deadline ? String(row.deadline).replace(" ", "T") : null, status: row.status, direction,
      });
      people.set(row.person_key, person);
    }
    res.json({
      people: [...people.values()]
        .map(({ tiers, ...person }) => ({ ...person, tier: TIER_ORDER.find((t) => tiers.has(t)) ?? "untiered" }))
        .sort((a, b) => b.youOwe + b.theyOwe - (a.youOwe + a.theyOwe)),
    });
  });

  return router;
}
