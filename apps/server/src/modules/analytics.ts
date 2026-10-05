/**
 * Email analytics, computed in SQL over the live tables.
 *
 * Days are the user's days: timestamps are stored in UTC, and each is converted
 * to the requested time zone before it is bucketed, so an email at 01:00 local
 * time lands on the right day rather than the previous one in UTC.
 */
import {
  CATEGORIES,
  analyticsQuerySchema,
  type Analytics,
  type AnalyticsQuery,
  type Category,
} from "@commitmail/shared";
import { Router } from "express";

import { utc } from "../db/time";
import type { Queryable } from "../db/types";
import type { Deps } from "../deps";
import { parse } from "../http/validate";

const DAYS = { "7d": 7, "30d": 30, "90d": 90 } as const;

/** `received_at` (naive UTC) as a local timestamp in zone $1. */
const LOCAL = (column: string) => `((${column} AT TIME ZONE 'UTC') AT TIME ZONE $1)`;

interface Window {
  zone: string;
  fromDate: string;
  toDate: string;
  days: number;
}

async function window(q: Queryable, query: AnalyticsQuery): Promise<Window> {
  const days = DAYS[query.range];
  const { rows } = await q.query<{ to_date: string; from_date: string }>(
    `SELECT (now() AT TIME ZONE $1)::date::text AS to_date,
            ((now() AT TIME ZONE $1)::date - ($2::int - 1))::text AS from_date`,
    [query.tz, days],
  );
  return { zone: query.tz, fromDate: rows[0]!.from_date, toDate: rows[0]!.to_date, days };
}

/** Rows in [from, to] local days. Params: $1 zone, $2 from date, $3 to date. */
const IN_RANGE = (column: string) =>
  `${LOCAL(column)} >= $2::date AND ${LOCAL(column)} < $3::date + 1`;

function pct(numerator: number, denominator: number): number | null {
  return denominator ? Math.round((numerator / denominator) * 1000) / 10 : null;
}

export async function computeAnalytics(q: Queryable, query: AnalyticsQuery): Promise<Analytics> {
  const w = await window(q, query);
  const params = [w.zone, w.fromDate, w.toDate];
  const visible = "e.deleted_at IS NULL";

  const [volume, byTier, senders, response, conversion, processing, calendar, previous] = await Promise.all([
    q.query<{ day: string; category: Category | null; count: number }>(
      `SELECT ${LOCAL("e.received_at")}::date::text AS day, e.category, count(*) AS count
         FROM raw_emails e WHERE ${visible} AND ${IN_RANGE("e.received_at")}
        GROUP BY 1, 2`,
      params,
    ),
    q.query<{ tier: string | null; count: number }>(
      `SELECT coalesce(e.vip_tier, 'UNTIERED') AS tier, count(*) AS count
         FROM raw_emails e WHERE ${visible} AND ${IN_RANGE("e.received_at")} GROUP BY 1`,
      params,
    ),
    q.query<{ email: string; name: string | null; count: number; last: string }>(
      `SELECT lower(e.sender_email) AS email, max(e.sender_name) AS name, count(*) AS count,
              max(e.received_at)::text AS last
         FROM raw_emails e
        WHERE ${visible} AND e.sender_email IS NOT NULL AND ${IN_RANGE("e.received_at")}
        GROUP BY 1 ORDER BY count DESC, last DESC LIMIT 8`,
      params,
    ),
    // Reply latency: a sent message whose In-Reply-To is the received one.
    q.query<{ replied: number; median: number | null; p90: number | null }>(
      `SELECT count(*) AS replied,
              percentile_cont(0.5) WITHIN GROUP (ORDER BY minutes) AS median,
              percentile_cont(0.9) WITHIN GROUP (ORDER BY minutes) AS p90
         FROM (SELECT DISTINCT ON (e.id) EXTRACT(EPOCH FROM (s.sent_at - e.received_at)) / 60 AS minutes
                 FROM raw_emails e JOIN sent_messages s ON s.in_reply_to = e.message_id
                WHERE s.sent_at >= e.received_at AND ${IN_RANGE("e.received_at")}
                ORDER BY e.id, s.sent_at) first_replies`,
      params,
    ),
    // Analysed = read by the model (processed, and not a skipped sender).
    q.query<{ analyzed: number; with_commitments: number; on_calendar: number }>(
      `SELECT count(*) AS analyzed,
              count(*) FILTER (WHERE EXISTS (SELECT 1 FROM commitments c
                                WHERE c.email_id = e.id AND c.status <> 'superseded')) AS with_commitments,
              count(*) FILTER (WHERE EXISTS (SELECT 1 FROM commitments c
                                WHERE c.email_id = e.id AND c.calendar_synced)) AS on_calendar
         FROM raw_emails e
        WHERE e.processed AND coalesce(e.vip_tier, '') <> 'SKIP' AND ${IN_RANGE("e.received_at")}`,
      params,
    ),
    q.query<{ succeeded: number; failed: number; avg_ms: number | null; p95_ms: number | null }>(
      `SELECT count(*) FILTER (WHERE a.status = 'succeeded') AS succeeded,
              count(*) FILTER (WHERE a.status = 'failed') AS failed,
              round(avg(a.duration_ms) FILTER (WHERE a.status = 'succeeded')) AS avg_ms,
              percentile_cont(0.95) WITHIN GROUP (ORDER BY a.duration_ms)
                FILTER (WHERE a.status = 'succeeded') AS p95_ms
         FROM job_attempts a JOIN jobs j ON j.id = a.job_id
        WHERE j.type = 'process_email' AND ${IN_RANGE("a.started_at")}`,
      params,
    ),
    q.query<{ day: string; type: string; count: number }>(
      `SELECT ${LOCAL("v.created_at")}::date::text AS day, v.type, count(*) AS count
         FROM events v
        WHERE v.type IN ('calendar.event_created', 'calendar.event_removed', 'calendar.event_failed')
          AND ${IN_RANGE("v.created_at")}
        GROUP BY 1, 2`,
      params,
    ),
    // The equally long period before, for the trend arrows.
    q.query<{ total: number; analyzed: number; on_calendar: number }>(
      `SELECT count(*) AS total,
              count(*) FILTER (WHERE e.processed AND coalesce(e.vip_tier, '') <> 'SKIP') AS analyzed,
              count(*) FILTER (WHERE e.processed AND coalesce(e.vip_tier, '') <> 'SKIP'
                AND EXISTS (SELECT 1 FROM commitments c WHERE c.email_id = e.id AND c.calendar_synced)) AS on_calendar
         FROM raw_emails e
        WHERE ${visible}
          AND ${LOCAL("e.received_at")} >= $2::date - $3::int
          AND ${LOCAL("e.received_at")} < $2::date`,
      // Its own parameter list: Postgres rejects a bound parameter the SQL never uses.
      [w.zone, w.fromDate, w.days],
    ),
  ]);

  // Every day in the range appears, including empty ones, so charts have no gaps.
  const days: string[] = [];
  for (let d = new Date(`${w.fromDate}T00:00:00Z`); d <= new Date(`${w.toDate}T00:00:00Z`); d.setUTCDate(d.getUTCDate() + 1)) {
    days.push(d.toISOString().slice(0, 10));
  }
  const volumeByDay = new Map(days.map((day) => [day, { date: day, total: 0 } as Analytics["volume"][number]]));
  const byCategory = Object.fromEntries(CATEGORIES.map((c) => [c, 0])) as Record<Category, number>;
  let total = 0;
  for (const row of volume.rows) {
    const bucket = volumeByDay.get(row.day);
    if (!bucket) continue;
    bucket.total += row.count;
    total += row.count;
    if (row.category) {
      bucket[row.category] = (bucket[row.category] ?? 0) + row.count;
      byCategory[row.category] += row.count;
    }
  }

  const calendarByDay = new Map(days.map((day) => [day, { date: day, created: 0, removed: 0, failed: 0 }]));
  for (const row of calendar.rows) {
    const bucket = calendarByDay.get(row.day);
    if (!bucket) continue;
    if (row.type === "calendar.event_created") bucket.created += row.count;
    else if (row.type === "calendar.event_removed") bucket.removed += row.count;
    else bucket.failed += row.count;
  }

  const conv = conversion.rows[0]!;
  const proc = processing.rows[0]!;
  const prev = previous.rows[0]!;
  const rate = pct(conv.on_calendar, conv.analyzed);
  const previousRate = pct(prev.on_calendar, prev.analyzed);
  const action = byCategory.action_required + byCategory.meeting;

  return {
    range: { from: w.fromDate, to: w.toDate, days: w.days, timeZone: w.zone },
    volume: [...volumeByDay.values()],
    byCategory,
    byTier: Object.fromEntries(byTier.rows.map((r) => [r.tier ?? "UNTIERED", r.count])),
    topSenders: senders.rows.map((r) => ({ email: r.email, name: r.name, count: r.count, lastReceivedAt: utc(r.last) })),
    actionVsInformational: { action, informational: total - action },
    responseTime: {
      replied: response.rows[0]?.replied ?? 0,
      medianMinutes: response.rows[0]?.median == null ? null : Math.round(Number(response.rows[0].median)),
      p90Minutes: response.rows[0]?.p90 == null ? null : Math.round(Number(response.rows[0].p90)),
    },
    conversion: { analyzed: conv.analyzed, withCommitments: conv.with_commitments, onCalendar: conv.on_calendar, rate },
    processing: {
      succeeded: proc.succeeded,
      failed: proc.failed,
      successRate: pct(proc.succeeded, proc.succeeded + proc.failed),
      avgLatencyMs: proc.avg_ms == null ? null : Number(proc.avg_ms),
      p95LatencyMs: proc.p95_ms == null ? null : Math.round(Number(proc.p95_ms)),
    },
    calendarActivity: [...calendarByDay.values()],
    trends: {
      volumeChange: prev.total ? Math.round(((total - prev.total) / prev.total) * 1000) / 10 : null,
      conversionChange: rate != null && previousRate != null ? Math.round((rate - previousRate) * 10) / 10 : null,
      previousTotal: prev.total,
    },
  };
}

export function analyticsRouter(deps: Deps): Router {
  const router = Router();
  router.get("/", async (req, res) => {
    res.json(await computeAnalytics(deps.db, parse(analyticsQuerySchema, req.query)));
  });
  return router;
}
