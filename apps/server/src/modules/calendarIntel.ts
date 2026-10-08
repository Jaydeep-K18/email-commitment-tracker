/**
 * Calendar intelligence: the flags the worker's scan_calendar job raises, what
 * the user does about them, and a look at the days ahead.
 *
 * The worker finds possible duplicates and clashes (src/sync/calendar_intel.py),
 * because only it can read the user's Google Calendar. Settling one is the
 * user's call: keep one copy of a duplicate (the other is dismissed, which takes
 * its event off the calendar), or dismiss the flag, which the worker will then
 * not raise again.
 */
import {
  calendarFlagQuerySchema,
  calendarFlagResolveSchema,
  calendarInsightsQuerySchema,
  defaultSettings,
  idParam,
  SETTINGS_SCHEMAS,
  type CalendarFlag,
  type CalendarInsights,
  type CalendarSpan,
  type Commitment,
} from "@commitmail/shared";
import { Router } from "express";

import { utc, wallClock } from "../db/time";
import type { Queryable } from "../db/types";
import type { Deps } from "../deps";
import { emailCorrelation, recordEvent } from "../events/record";
import { AppError, badRequest, notFound } from "../http/errors";
import { parse } from "../http/validate";
import { localNow, SHOULD_SYNC_SQL } from "./commitments";
import { COMMITMENT_COLUMNS, toCommitment } from "./mappers";

interface FlagRow {
  id: number;
  kind: CalendarFlag["kind"];
  status: CalendarFlag["status"];
  commitment_id: number;
  other_commitment_id: number | null;
  external_event_id: string | null;
  details: {
    items?: Array<{ title: string; start: string; end: string; allDay: boolean; link?: string }>;
    similarity?: number;
    overlap?: CalendarSpan;
    suggestions?: CalendarSpan[];
  };
  created_at: string;
  resolved_at: string | null;
}

async function commitmentsById(q: Queryable, ids: number[]): Promise<Map<number, Commitment>> {
  if (!ids.length) return new Map();
  const { rows } = await q.query(
    `SELECT ${COMMITMENT_COLUMNS} FROM commitments c JOIN raw_emails e ON e.id = c.email_id WHERE c.id = ANY($1::int[])`,
    [ids],
  );
  return new Map(rows.map((row) => [row.id as number, toCommitment(row)]));
}

function toFlag(row: FlagRow, commitments: Map<number, Commitment>): CalendarFlag | null {
  const commitment = commitments.get(row.commitment_id);
  if (!commitment) return null;
  const theirs = row.details.items?.[1];
  return {
    id: row.id,
    kind: row.kind,
    status: row.status,
    commitment,
    other: row.other_commitment_id ? commitments.get(row.other_commitment_id) ?? null : null,
    external: row.external_event_id && theirs
      ? { id: row.external_event_id, title: theirs.title, start: theirs.start, end: theirs.end, allDay: theirs.allDay, link: theirs.link ?? null }
      : null,
    similarity: row.details.similarity ?? null,
    overlap: row.details.overlap ?? null,
    suggestions: row.details.suggestions ?? [],
    createdAt: utc(row.created_at)!,
    resolvedAt: utc(row.resolved_at),
  };
}

async function loadFlags(q: Queryable, where: string, params: unknown[] = []): Promise<CalendarFlag[]> {
  const { rows } = await q.query<FlagRow>(`SELECT * FROM calendar_flags WHERE ${where} ORDER BY id DESC LIMIT 200`, params);
  const ids = [...new Set(rows.flatMap((r) => (r.other_commitment_id ? [r.commitment_id, r.other_commitment_id] : [r.commitment_id])))];
  const commitments = await commitmentsById(q, ids);
  return rows
    .map((row) => toFlag(row, commitments))
    .filter((flag): flag is CalendarFlag => flag !== null)
    .sort((a, b) => (a.commitment.deadline ?? "").localeCompare(b.commitment.deadline ?? ""));
}

async function openFlag(q: Queryable, id: number): Promise<FlagRow> {
  const { rows } = await q.query<FlagRow>("SELECT * FROM calendar_flags WHERE id = $1 FOR UPDATE", [id]);
  const flag = rows[0];
  if (!flag) throw notFound("Flag");
  if (flag.status !== "open") throw new AppError(409, "flag_settled", "This has already been settled.");
  return flag;
}

const SETTLE_SQL = `UPDATE calendar_flags SET status = $2, resolved_at = (now() at time zone 'utc') WHERE id = $1`;

async function workingHours(q: Queryable) {
  const { rows } = await q.query<{ value: unknown }>("SELECT value FROM settings WHERE section = 'calendar'");
  const parsed = SETTINGS_SCHEMAS.calendar.safeParse(rows[0]?.value ?? {});
  return (parsed.success ? parsed.data : defaultSettings().calendar).workingHours;
}

const addDays = (isoDate: string, days: number) =>
  new Date(Date.parse(`${isoDate}T00:00:00Z`) + days * 86_400_000).toISOString().slice(0, 10);

export async function calendarInsights(q: Queryable, days: number): Promise<CalendarInsights> {
  const from = (await localNow(q)).slice(0, 10);
  const to = addDays(from, days - 1);
  const { rows } = await q.query<{ id: number; type: Commitment["type"]; subject: string; deadline: string }>(
    `SELECT c.id, c.type, c.subject, c.deadline::text AS deadline FROM commitments c
      WHERE ${SHOULD_SYNC_SQL} AND c.status IN ('pending', 'overdue')
        AND c.deadline >= $1::date AND c.deadline < $2::date + 1
      ORDER BY c.deadline, c.id`,
    [from, to],
  );
  const hours = await workingHours(q);

  const perDay = new Map<string, { deadlines: number; meetings: number }>();
  for (let i = 0; i < days; i++) perDay.set(addDays(from, i), { deadlines: 0, meetings: 0 });
  const outsideHours: CalendarInsights["outsideHours"] = [];
  for (const row of rows) {
    const deadline = wallClock(row.deadline)!;
    const [date, clock = "00:00:00"] = deadline.split("T") as [string, string?];
    const day = perDay.get(date);
    if (day && row.type === "meeting") day.meetings += 1;
    else if (day) day.deadlines += 1;
    const time = clock.slice(0, 5);
    const weekday = new Date(`${date}T00:00:00Z`).getUTCDay();
    if (time !== "00:00" && (!hours.days.includes(weekday) || time < hours.start || time > hours.end)) {
      outsideHours.push({ id: row.id, type: row.type, subject: row.subject, deadline });
    }
  }

  const dayList = [...perDay].map(([date, counts]) => ({ date, ...counts }));
  const busiest = dayList.reduce<{ date: string; count: number } | null>((best, d) => {
    const count = d.deadlines + d.meetings;
    return count >= 3 && count > (best?.count ?? 0) ? { date: d.date, count } : best;
  }, null);

  const flags = await q.query<{ kind: string; n: number }>(
    "SELECT kind, count(*)::int AS n FROM calendar_flags WHERE status = 'open' GROUP BY kind",
  );
  const open = Object.fromEntries(flags.rows.map((r) => [r.kind, r.n]));
  return {
    from,
    to,
    days: dayList,
    busiestDay: busiest,
    outsideHours,
    openFlags: { conflicts: open.conflict ?? 0, duplicates: open.duplicate ?? 0 },
  };
}

export function calendarIntelRouter(deps: Deps): Router {
  const router = Router();

  router.get("/flags", async (req, res) => {
    const { status } = parse(calendarFlagQuerySchema, req.query);
    res.json({ items: await loadFlags(deps.db, status === "open" ? "status = 'open'" : "TRUE") });
  });

  /** Not a problem after all: the worker will not raise it again. */
  router.post("/flags/:id/dismiss", async (req, res) => {
    const { id } = parse(idParam, req.params);
    await deps.db.transaction(async (q) => {
      const flag = await openFlag(q, id);
      await q.query(SETTLE_SQL, [id, "dismissed"]);
      await recordEvent(q, {
        type: "calendar.flag_resolved",
        message: `Kept as it is: ${flag.details.items?.[0]?.title ?? "a commitment"}`,
        entityType: "calendar_flag",
        entityId: id,
        payload: { kind: flag.kind, how: "dismissed" },
      });
    });
    res.json((await loadFlags(deps.db, "id = $1", [id]))[0]);
  });

  /** A duplicate: keep one side, dismiss the other, and republish. */
  router.post("/flags/:id/resolve", async (req, res) => {
    const { id } = parse(idParam, req.params);
    const { keep } = parse(calendarFlagResolveSchema, req.body);
    await deps.jobs.withJobs(async (q, jobs) => {
      const flag = await openFlag(q, id);
      if (flag.kind !== "duplicate") throw badRequest("Only a possible duplicate is settled by keeping one side.");
      const drop =
        keep === "external" && flag.external_event_id ? flag.commitment_id
        : keep === flag.commitment_id ? flag.other_commitment_id
        : keep === flag.other_commitment_id ? flag.commitment_id
        : null;
      if (drop === null) throw badRequest("Keep one of the two things this flag is about.");

      const { rows } = await q.query<{ email_id: number; subject: string }>(
        `UPDATE commitments SET status = 'dismissed' WHERE id = $1 AND status <> 'superseded' RETURNING email_id, subject`,
        [drop],
      );
      await q.query(SETTLE_SQL, [id, "resolved"]);
      const dropped = rows[0];
      if (dropped) {
        await recordEvent(q, {
          type: "commitment.updated",
          message: `${dropped.subject}: dismissed as a duplicate`,
          entityType: "commitment",
          entityId: drop,
          correlationId: emailCorrelation(dropped.email_id),
          payload: { changes: { status: "dismissed" }, flagId: id },
        });
      }
      await recordEvent(q, {
        type: "calendar.flag_resolved",
        message: `Duplicate settled: kept ${keep === "external" ? "the event already on your calendar" : "one copy"}`,
        entityType: "calendar_flag",
        entityId: id,
        payload: { kind: flag.kind, how: "kept_one", kept: keep, dismissed: drop },
      });
      await jobs.enqueueUnlessActive("publish_calendar");
    });
    res.json((await loadFlags(deps.db, "id = $1", [id]))[0]);
  });

  /** Look again now, rather than after the next mail check. */
  router.post("/scan", async (_req, res) => {
    const queued = await deps.jobs.withJobs((_q, jobs) => jobs.enqueueUnlessActive("scan_calendar"));
    res.status(202).json({ jobId: queued.jobId, queued: queued.created });
  });

  router.get("/insights", async (req, res) => {
    const { days } = parse(calendarInsightsQuerySchema, req.query);
    res.json(await calendarInsights(deps.db, days));
  });

  return router;
}
