/**
 * System health and live metrics.
 *
 * Health is checked, not assumed: each component gets a real round trip with a
 * short timeout, and the overall status is the worst of the components that
 * are configured. A component that is simply not set up (Kafka on a machine
 * without Docker) reads "unconfigured", which is not a failure.
 *
 * Metrics come from Flink when it is running (it writes metric_snapshots); when
 * it is not, the same measures are computed here from the events table — the
 * stream Flink reads, at rest — by the same rules, and the response says which
 * source produced them.
 */
import { connect } from "node:net";

import { BASELINE_MINUTES, windowMetrics } from "@commitmail/shared";
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
/** Flink writes each minute 15 s after it ends; older than this and it has stopped. */
const FLINK_FRESH_MS = 3 * 60_000;

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

/** A broker's port accepting connections. */
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

/** Events the relay has yet to publish, read from the outbox itself. */
export async function relayBacklog(q: Queryable): Promise<{ waiting: number; oldestSeconds: number }> {
  const { rows } = await q.query<{ waiting: number; oldest: number | null }>(
    `SELECT count(*)::int AS waiting,
            EXTRACT(EPOCH FROM ((now() at time zone 'utc') - min(created_at)))::float AS oldest
       FROM events WHERE published_at IS NULL`,
  );
  return { waiting: rows[0]?.waiting ?? 0, oldestSeconds: Math.max(0, Math.round(rows[0]?.oldest ?? 0)) };
}

/** The relay publishes within a second; a minute behind means it is not running. */
const RELAY_STALE_SECONDS = 60;

function ago(seconds: number): string {
  if (seconds < 120) return `${seconds}s`;
  if (seconds < 7_200) return `${Math.round(seconds / 60)} min`;
  return `${Math.round(seconds / 3_600)} h`;
}

/**
 * Kafka is healthy when the broker answers, the relay keeps up, and this
 * server is actually streaming from it (it falls back to Postgres if Kafka
 * was down when it started).
 */
async function checkKafka(deps: Deps): Promise<ComponentHealth> {
  const brokers = deps.env.KAFKA_BROKERS;
  if (!brokers?.length) return { state: "unconfigured", detail: "KAFKA_BROKERS not set — events stream from Postgres" };
  const [latency, backlog] = await Promise.all([tcpReachable(brokers[0]!), relayBacklog(deps.db)]);
  const meta = { ...backlog, liveEvents: deps.liveEvents };
  if (latency == null) return { state: "down", detail: `${brokers[0]} not reachable`, meta };
  if (backlog.waiting > 0 && backlog.oldestSeconds > RELAY_STALE_SECONDS) {
    return {
      state: "degraded",
      latencyMs: latency,
      detail: `${backlog.waiting} event(s) not relayed, oldest ${ago(backlog.oldestSeconds)} — is the worker running with KAFKA_BROKERS set?`,
      meta,
    };
  }
  if (deps.liveEvents !== "kafka") {
    return {
      state: "degraded",
      latencyMs: latency,
      detail: "reachable, but this server fell back to Postgres at startup — restart it to stream from Kafka",
      meta,
    };
  }
  return { state: "ok", latencyMs: latency, detail: "streaming live events", meta };
}

async function checkFlink(deps: Deps): Promise<ComponentHealth> {
  const url = deps.env.FLINK_URL;
  if (!url) return { state: "unconfigured", detail: "FLINK_URL not set — metrics computed from the database" };
  let overview: { value: Response; ms: number };
  try {
    overview = await timed(() => fetch(`${url.replace(/\/$/, "")}/overview`, { signal: AbortSignal.timeout(2_500) }));
  } catch {
    return { state: "down", detail: `not reachable at ${url}` };
  }
  const body = (await overview.value.json()) as { "jobs-running"?: number; taskmanagers?: number };
  const running = body["jobs-running"] ?? 0;
  const meta = { taskManagers: body.taskmanagers ?? 0, jobsRunning: running };
  if (running === 0) return { state: "degraded", latencyMs: overview.ms, detail: "cluster up, but no job running", meta };

  // Running is not the same as working: a job that cannot reach Postgres runs on.
  const { rows } = await deps.db.query<{ newest: string | null }>(
    "SELECT max(window_end) AS newest FROM metric_snapshots WHERE source = 'flink'",
  );
  const newest = rows[0]?.newest;
  const age = newest ? Date.now() - Date.parse(utc(newest)!) : null;
  if (age === null || age > FLINK_FRESH_MS) {
    const since = age === null ? "yet" : `for ${Math.round(age / 60_000)} min`;
    return { state: "degraded", latencyMs: overview.ms, detail: `job running, but no metrics written ${since}`, meta };
  }
  return { state: "ok", latencyMs: overview.ms, detail: `${running} job(s) running`, meta };
}

const SEVERITY: Record<HealthState, number> = { ok: 0, unconfigured: 0, degraded: 1, down: 2 };

export async function systemHealth(deps: Deps): Promise<SystemHealth> {
  const [database, redis, worker, ollama, kafka, flink] = await Promise.all([
    checkDatabase(deps.db),
    checkRedis(deps),
    checkWorker(deps.db).catch((): ComponentHealth => ({ state: "down", detail: "unknown" })),
    checkOllama(deps.env.OLLAMA_HOST, deps.env.OLLAMA_MODEL),
    checkKafka(deps).catch((): ComponentHealth => ({ state: "down", detail: "check failed" })),
    checkFlink(deps).catch((): ComponentHealth => ({ state: "down", detail: "check failed" })),
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
  succeeded: number;
  failures: number;
  durations: number[];
}

/**
 * One-minute windows over the last `minutes`, computed from the events table by
 * the rules the Flink job uses (packages/shared/src/metrics.ts). An extra hour
 * is read in front, so the oldest minute shown has its full baseline too.
 */
export async function fallbackMetrics(q: Queryable, minutes: number): Promise<MetricWindow[]> {
  const span = minutes + BASELINE_MINUTES - 1;
  const { rows } = await q.query<MinuteRow>(
    `WITH buckets AS (
       SELECT generate_series(
         date_trunc('minute', (now() at time zone 'utc')) - ($1::int - 1) * interval '1 minute',
         date_trunc('minute', (now() at time zone 'utc')), interval '1 minute') AS minute
     ),
     ev AS (
       SELECT date_trunc('minute', created_at) AS minute,
              count(*)::int AS events,
              (count(*) FILTER (WHERE type = 'email.analyzed'))::int AS processed,
              (count(*) FILTER (WHERE type = 'job.completed'))::int AS succeeded,
              (count(*) FILTER (WHERE type IN ('job.retrying', 'job.failed')))::int AS failures,
              array_agg((payload->>'duration_ms')::float8)
                FILTER (WHERE type = 'job.completed' AND jsonb_typeof(payload->'duration_ms') = 'number') AS durations
         FROM events
        WHERE created_at >= date_trunc('minute', (now() at time zone 'utc')) - ($1::int - 1) * interval '1 minute'
        GROUP BY 1
     )
     SELECT b.minute::text AS minute, coalesce(ev.events, 0) AS events, coalesce(ev.processed, 0) AS processed,
            coalesce(ev.succeeded, 0) AS succeeded, coalesce(ev.failures, 0) AS failures,
            coalesce(ev.durations, '{}') AS durations
       FROM buckets b LEFT JOIN ev USING (minute)
      ORDER BY b.minute`,
    [span],
  );

  const metrics = windowMetrics(rows.map((r) => ({
    events: r.events,
    processed: r.processed,
    succeeded: r.succeeded,
    failures: r.failures,
    durationsMs: r.durations.map(Number),
  })));
  return rows.slice(BASELINE_MINUTES - 1).map((row, i) => {
    const start = utc(row.minute)!;
    return {
      source: "fallback",
      window: "1m",
      windowStart: start,
      windowEnd: new Date(Date.parse(start) + 60_000).toISOString(),
      metrics: metrics[i + BASELINE_MINUTES - 1]!,
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
  if (newest && Date.now() - Date.parse(utc(newest.window_end)!) < FLINK_FRESH_MS) {
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
