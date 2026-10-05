/**
 * System health and live metrics.
 *
 * Health is checked, not assumed: each component gets a real round trip with a
 * short timeout, and the overall status is the worst of the components that
 * are configured. A component that is simply not set up (Kafka on a machine
 * without Docker) reads "unconfigured", which is not a failure.
 *
 * Metrics come from Flink when it is running (it writes metric_snapshots); when
 * it is not, the same measures are computed here from the events and job tables,
 * and the response says which source produced them.
 */
import { connect } from "node:net";

import type { ComponentHealth, HealthState, MetricWindow, SystemHealth } from "@commitmail/shared";
import { Router } from "express";
import { z } from "zod";

import { utc } from "../db/time";
import type { Queryable } from "../db/types";
import type { Deps } from "../deps";
import { parse } from "../http/validate";
import { toActivityEvent } from "./mappers";

const WORKER_FRESH_MS = 30_000;
const WORKER_STALE_MS = 120_000;

async function timed<T>(fn: () => Promise<T>): Promise<{ value: T; ms: number }> {
  const started = performance.now();
  const value = await fn();
  return { value, ms: Math.round(performance.now() - started) };
}

async function checkDatabase(q: Queryable): Promise<ComponentHealth> {
  try {
    const { value, ms } = await timed(() => q.query<{ version: string }>("SELECT version() AS version"));
    return { state: "ok", latencyMs: ms, detail: value.rows[0]?.version.split(",")[0] };
  } catch (error) {
    return { state: "down", detail: (error as Error).message };
  }
}

async function checkRedis(deps: Deps): Promise<ComponentHealth> {
  if (!deps.redis) return { state: "unconfigured", detail: "REDIS_URL not set — jobs are dispatched by polling the database" };
  try {
    const { ms } = await timed(() => deps.redis!.ping());
    return { state: "ok", latencyMs: ms, meta: { queue: await deps.jobs.depth() } };
  } catch (error) {
    return { state: "down", detail: (error as Error).message };
  }
}

async function checkWorker(q: Queryable): Promise<ComponentHealth> {
  const { rows } = await q.query<{ last_seen_at: string; details: Record<string, unknown> }>(
    "SELECT last_seen_at, details FROM service_heartbeats WHERE service = 'worker'",
  );
  const row = rows[0];
  if (!row) return { state: "down", detail: "The worker has never run. Start it with: python -m src.jobs.worker" };
  const age = Date.now() - Date.parse(utc(row.last_seen_at)!);
  const state: HealthState = age < WORKER_FRESH_MS ? "ok" : age < WORKER_STALE_MS ? "degraded" : "down";
  return { state, detail: `last heartbeat ${Math.round(age / 1000)}s ago`, meta: { ...row.details, lastSeenAt: utc(row.last_seen_at) } };
}

async function checkOllama(host: string, model: string): Promise<ComponentHealth> {
  try {
    const { value: response, ms } = await timed(() => fetch(`${host.replace(/\/$/, "")}/api/tags`, { signal: AbortSignal.timeout(2_500) }));
    if (!response.ok) return { state: "down", latencyMs: ms, detail: `HTTP ${response.status}` };
    const body = (await response.json()) as { models?: Array<{ name: string }> };
    const base = model.split(":")[0];
    const present = (body.models ?? []).some((m) => m.name === model || m.name.split(":")[0] === base);
    return present
      ? { state: "ok", latencyMs: ms, detail: model }
      : { state: "degraded", latencyMs: ms, detail: `running, but ${model} is not pulled` };
  } catch {
    return { state: "down", detail: `not reachable at ${host}` };
  }
}

/** A broker's port accepting connections. A Kafka admin check replaces this when events are wired. */
function tcpReachable(address: string, timeoutMs = 1_500): Promise<number | null> {
  const [host, port] = address.split(":");
  return new Promise((resolve) => {
    const started = performance.now();
    const socket = connect({ host: host!, port: Number(port ?? 9092) });
    const done = (ok: boolean) => {
      socket.destroy();
      resolve(ok ? Math.round(performance.now() - started) : null);
    };
    socket.setTimeout(timeoutMs, () => done(false));
    socket.once("connect", () => done(true));
    socket.once("error", () => done(false));
  });
}

async function checkKafka(brokers: string[] | undefined): Promise<ComponentHealth> {
  if (!brokers?.length) return { state: "unconfigured", detail: "KAFKA_BROKERS not set — events stream from Postgres" };
  const latency = await tcpReachable(brokers[0]!);
  return latency == null ? { state: "down", detail: `${brokers[0]} not reachable` } : { state: "ok", latencyMs: latency };
}

async function checkFlink(url: string | undefined): Promise<ComponentHealth> {
  if (!url) return { state: "unconfigured", detail: "FLINK_URL not set — metrics computed from the database" };
  try {
    const { value: response, ms } = await timed(() => fetch(`${url.replace(/\/$/, "")}/overview`, { signal: AbortSignal.timeout(2_500) }));
    const body = (await response.json()) as { "jobs-running"?: number; taskmanagers?: number };
    const running = body["jobs-running"] ?? 0;
    return {
      state: running > 0 ? "ok" : "degraded",
      latencyMs: ms,
      detail: running > 0 ? `${running} job(s) running` : "cluster up, but no job running",
      meta: { taskManagers: body.taskmanagers ?? 0, jobsRunning: running },
    };
  } catch {
    return { state: "down", detail: `not reachable at ${url}` };
  }
}

const SEVERITY: Record<HealthState, number> = { ok: 0, unconfigured: 0, degraded: 1, down: 2 };

export async function systemHealth(deps: Deps): Promise<SystemHealth> {
  const [database, redis, worker, ollama, kafka, flink] = await Promise.all([
    checkDatabase(deps.db),
    checkRedis(deps),
    checkWorker(deps.db).catch((): ComponentHealth => ({ state: "down", detail: "unknown" })),
    checkOllama(deps.env.OLLAMA_HOST, deps.env.OLLAMA_MODEL),
    checkKafka(deps.env.KAFKA_BROKERS),
    checkFlink(deps.env.FLINK_URL),
  ]);
  const components = { api: { state: "ok" as HealthState }, database, redis, worker, ollama, kafka, flink };
  const worst = Math.max(...Object.values(components).map((c) => SEVERITY[c.state]));
  const status: HealthState = database.state === "down" ? "down" : worst === 2 ? "degraded" : worst === 1 ? "degraded" : "ok";
  return { status, checkedAt: new Date().toISOString(), components };
}

// --- Metrics --------------------------------------------------------------------

interface MinuteRow {
  minute: string;
  events: number;
  processed: number;
  failures: number;
  succeeded: number;
  avg_ms: number | null;
  p95_ms: number | null;
}

/**
 * One-minute windows over the last `minutes`, computed from the tables. The
 * same definitions the Flink job uses, so the two sources are interchangeable.
 */
export async function fallbackMetrics(q: Queryable, minutes: number): Promise<MetricWindow[]> {
  const { rows } = await q.query<MinuteRow>(
    `WITH buckets AS (
       SELECT generate_series(
         date_trunc('minute', (now() at time zone 'utc')) - ($1::int - 1) * interval '1 minute',
         date_trunc('minute', (now() at time zone 'utc')), interval '1 minute') AS minute
     ),
     ev AS (
       SELECT date_trunc('minute', created_at) AS minute, count(*) AS events,
              count(*) FILTER (WHERE type = 'email.analyzed') AS processed
         FROM events
        WHERE created_at >= (now() at time zone 'utc') - $1::int * interval '1 minute'
        GROUP BY 1
     ),
     att AS (
       SELECT date_trunc('minute', finished_at) AS minute,
              count(*) FILTER (WHERE status = 'failed') AS failures,
              count(*) FILTER (WHERE status = 'succeeded') AS succeeded,
              avg(duration_ms) FILTER (WHERE status = 'succeeded') AS avg_ms,
              percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms)
                FILTER (WHERE status = 'succeeded') AS p95_ms
         FROM job_attempts
        WHERE finished_at >= (now() at time zone 'utc') - $1::int * interval '1 minute'
        GROUP BY 1
     )
     SELECT b.minute::text AS minute, coalesce(ev.events, 0) AS events, coalesce(ev.processed, 0) AS processed,
            coalesce(att.failures, 0) AS failures, coalesce(att.succeeded, 0) AS succeeded,
            att.avg_ms, att.p95_ms
       FROM buckets b LEFT JOIN ev USING (minute) LEFT JOIN att USING (minute)
      ORDER BY b.minute`,
    [minutes],
  );

  // An error spike: a minute with at least 3 failures and three times the
  // window's average. Deliberately simple and explainable.
  const meanFailures = rows.reduce((sum, r) => sum + r.failures, 0) / Math.max(rows.length, 1);
  const meanEvents = rows.reduce((sum, r) => sum + r.events, 0) / Math.max(rows.length, 1);
  const sd = Math.sqrt(rows.reduce((sum, r) => sum + (r.events - meanEvents) ** 2, 0) / Math.max(rows.length, 1));

  return rows.map((row) => {
    const start = utc(row.minute)!;
    const attempts = row.succeeded + row.failures;
    return {
      source: "fallback",
      window: "1m",
      windowStart: start,
      windowEnd: new Date(Date.parse(start) + 60_000).toISOString(),
      metrics: {
        events: row.events,
        emailsProcessed: row.processed,
        throughputPerMinute: row.processed,
        avgLatencyMs: row.avg_ms == null ? null : Math.round(Number(row.avg_ms)),
        p95LatencyMs: row.p95_ms == null ? null : Math.round(Number(row.p95_ms)),
        successRate: attempts ? Math.round((row.succeeded / attempts) * 1000) / 10 : null,
        failures: row.failures,
        errorSpike: row.failures >= 3 && row.failures >= 3 * meanFailures,
        volumeAnomaly: sd > 0 && row.events >= 10 && (row.events - meanEvents) / sd > 3,
      },
    };
  });
}

/** Flink's windows when it has written recently; otherwise the fallback. */
export async function liveMetrics(q: Queryable, minutes: number): Promise<{ source: "flink" | "fallback"; windows: MetricWindow[] }> {
  const { rows } = await q.query<{ window_start: string; window_end: string; metrics: MetricWindow["metrics"] }>(
    `SELECT window_start, window_end, metrics FROM metric_snapshots
      WHERE source = 'flink' AND "window" = '1m'
        AND window_end >= (now() at time zone 'utc') - $1::int * interval '1 minute'
      ORDER BY window_start`,
    [minutes],
  );
  const newest = rows[rows.length - 1];
  if (newest && Date.now() - Date.parse(utc(newest.window_end)!) < 3 * 60_000) {
    return {
      source: "flink",
      windows: rows.map((r) => ({
        source: "flink",
        window: "1m",
        windowStart: utc(r.window_start)!,
        windowEnd: utc(r.window_end)!,
        metrics: r.metrics,
      })),
    };
  }
  return { source: "fallback", windows: await fallbackMetrics(q, minutes) };
}

export function systemRouter(deps: Deps): Router {
  const router = Router();

  router.get("/health", async (_req, res) => {
    res.json(await systemHealth(deps));
  });

  router.get("/metrics", async (req, res) => {
    const { minutes } = parse(z.object({ minutes: z.coerce.number().int().min(5).max(240).default(60) }), req.query);
    res.json(await liveMetrics(deps.db, minutes));
  });

  router.get("/events", async (_req, res) => {
    const { rows } = await deps.db.query(
      `SELECT * FROM events
        WHERE type LIKE 'system.%' OR severity IN ('warning', 'error')
        ORDER BY id DESC LIMIT 50`,
    );
    res.json({ items: rows.map(toActivityEvent) });
  });

  return router;
}
